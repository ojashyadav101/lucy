"""Gmail custom wrapper — executes via Composio REST API.

Bypasses Composio's 3-step meta-tool chain (SEARCH → GET_SCHEMA →
MULTI_EXECUTE) by executing actions directly via the v2 REST API.

Key design decisions:
- Truncates HTML bodies to plain text for readable responses
- Caps email body length to avoid blowing up the context window
- Returns structured data the LLM can summarize naturally
"""

from __future__ import annotations

import re
import httpx
import structlog

logger = structlog.get_logger()

# ── Tool Definitions ──────────────────────────────────────────────────

TOOLS: list[dict] = [
    {
        "name": "gmail_fetch_emails",
        "description": (
            "Fetch recent emails from Gmail inbox. "
            "Supports search queries (Gmail syntax like 'from:user subject:meeting is:unread'). "
            "Returns sender, subject, snippet, date, and labels."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Gmail search query. Examples: "
                        "'is:unread', 'from:boss@company.com', "
                        "'subject:invoice newer_than:7d', "
                        "'has:attachment from:client'. "
                        "Leave empty for latest emails."
                    ),
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum emails to return (default: 10, max: 25).",
                },
                "label_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Filter by label IDs. Common: INBOX, UNREAD, "
                        "STARRED, IMPORTANT, SENT, DRAFT, SPAM, TRASH."
                    ),
                },
                "include_body": {
                    "type": "boolean",
                    "description": (
                        "Include email body text (default: false). "
                        "Set true only when user needs to read email content."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "gmail_send_email",
        "description": (
            "Send an email from the user's Gmail account. "
            "The email is sent from the authenticated user's address."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "recipient_email": {
                    "type": "string",
                    "description": "Primary recipient email address. REQUIRED.",
                },
                "subject": {
                    "type": "string",
                    "description": "Email subject line.",
                },
                "body": {
                    "type": "string",
                    "description": (
                        "Email body text. For HTML emails, set is_html=true. REQUIRED."
                    ),
                },
                "is_html": {
                    "type": "boolean",
                    "description": "Set true if body is HTML (default: false).",
                },
                "cc": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "CC recipient email addresses.",
                },
                "bcc": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "BCC recipient email addresses.",
                },
            },
            "required": ["recipient_email", "body"],
        },
    },
    {
        "name": "gmail_create_draft",
        "description": (
            "Create an email draft in Gmail (does NOT send it). "
            "Useful when the user wants to review before sending."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "recipient_email": {
                    "type": "string",
                    "description": "Primary recipient email address. REQUIRED.",
                },
                "subject": {
                    "type": "string",
                    "description": "Email subject line. REQUIRED.",
                },
                "body": {
                    "type": "string",
                    "description": "Email body text. REQUIRED.",
                },
                "is_html": {
                    "type": "boolean",
                    "description": "Set true if body is HTML (default: false).",
                },
                "cc": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "CC recipient email addresses.",
                },
                "bcc": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "BCC recipient email addresses.",
                },
                "thread_id": {
                    "type": "string",
                    "description": "Thread ID to reply to (for draft replies).",
                },
            },
            "required": ["recipient_email", "subject", "body"],
        },
    },
    {
        "name": "gmail_reply_to_thread",
        "description": (
            "Reply to an existing email thread. Requires the thread_id "
            "from a previous email fetch."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "thread_id": {
                    "type": "string",
                    "description": "Gmail thread ID to reply to. REQUIRED.",
                },
                "recipient_email": {
                    "type": "string",
                    "description": "Recipient email to reply to. REQUIRED.",
                },
                "message_body": {
                    "type": "string",
                    "description": "Reply message content. REQUIRED.",
                },
                "is_html": {
                    "type": "boolean",
                    "description": "Set true if body is HTML (default: false).",
                },
                "cc": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "CC recipients.",
                },
            },
            "required": ["thread_id", "recipient_email", "message_body"],
        },
    },
    {
        "name": "gmail_get_thread",
        "description": (
            "Get all messages in an email thread by thread_id. "
            "Use after gmail_fetch_emails to read a full conversation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "thread_id": {
                    "type": "string",
                    "description": "Gmail thread ID. REQUIRED.",
                },
            },
            "required": ["thread_id"],
        },
    },
    {
        "name": "gmail_get_profile",
        "description": (
            "Get the user's Gmail profile: email address, "
            "total messages, and total threads."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]


# ── Composio Execution ────────────────────────────────────────────────

_connected_account_id: str | None = None


def _resolve_connection(api_key: str) -> str | None:
    """Find the active gmail connected_account_id."""
    global _connected_account_id
    if _connected_account_id:
        return _connected_account_id

    try:
        resp = httpx.get(
            "https://backend.composio.dev/api/v1/connectedAccounts",
            headers={"x-api-key": api_key},
            params={"appNames": "gmail", "status": "ACTIVE"},
            timeout=10.0,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if items:
            _connected_account_id = items[0]["id"]
            logger.info(
                "gmail_connection_resolved",
                connected_account_id=_connected_account_id,
                entity=items[0].get("clientUniqueUserId"),
            )
            return _connected_account_id
    except Exception as e:
        logger.warning("gmail_connection_resolve_failed", error=str(e))

    return None


def _execute_composio_action(
    action_name: str,
    params: dict,
    api_key: str,
) -> dict:
    """Execute a Composio action via REST API."""
    conn_id = _resolve_connection(api_key)
    if not conn_id:
        return {
            "error": (
                "Gmail is not connected. "
                "Please connect it via the integrations settings."
            ),
        }

    try:
        resp = httpx.post(
            f"https://backend.composio.dev/api/v2/actions/{action_name}/execute",
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            json={
                "connectedAccountId": conn_id,
                "input": params,
            },
            timeout=20.0,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("error"):
            return {"error": data["error"]}

        result = data.get("data", data)
        # Some Composio actions nest results in response_data
        if isinstance(result, dict) and "response_data" in result:
            result = result["response_data"]
        return result

    except httpx.TimeoutException:
        return {"error": "Gmail request timed out. Please try again."}
    except Exception as e:
        return {"error": f"Gmail API error: {e}"}


# ── Response Formatting ───────────────────────────────────────────────

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MULTI_NEWLINE = re.compile(r"\n{3,}")
_MULTI_SPACE = re.compile(r"  +")


def _strip_html(text: str) -> str:
    """Strip HTML tags and collapse whitespace."""
    text = _HTML_TAG_RE.sub("", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = text.replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&#39;", "'")
    text = _MULTI_SPACE.sub(" ", text)
    text = _MULTI_NEWLINE.sub("\n\n", text)
    return text.strip()


def _extract_sender(headers: list[dict]) -> str:
    """Extract sender from email headers."""
    for h in headers:
        if h.get("name", "").lower() == "from":
            return h.get("value", "")
    return ""


def _extract_header(headers: list[dict], name: str) -> str:
    """Extract a header value by name."""
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _format_email(msg: dict, include_body: bool = False) -> dict:
    """Format a raw Gmail message into a clean dict."""
    # messageText is a combined text representation
    message_text = msg.get("messageText", "")
    headers = msg.get("payload", {}).get("headers", []) if msg.get("payload") else []

    # Try to extract from headers first, fall back to parsing messageText
    sender = _extract_header(headers, "from")
    subject = _extract_header(headers, "subject")
    date = _extract_header(headers, "date")

    # If no headers (metadata-only response), parse from messageText
    if not sender and message_text:
        for line in message_text.split("\n")[:10]:
            if line.startswith("From:"):
                sender = line[5:].strip()
            elif line.startswith("Subject:"):
                subject = line[8:].strip()
            elif line.startswith("Date:"):
                date = line[5:].strip()

    entry = {
        "id": msg.get("messageId", msg.get("id", "")),
        "thread_id": msg.get("threadId", ""),
        "from": sender,
        "subject": subject,
        "date": date,
        "labels": msg.get("labelIds", []),
    }

    snippet = msg.get("snippet", "")
    if snippet:
        entry["snippet"] = snippet[:200]

    if include_body and message_text:
        # Strip HTML, cap at 1500 chars
        body = _strip_html(message_text)
        if len(body) > 1500:
            body = body[:1500] + "... [truncated]"
        entry["body"] = body

    return entry


def _format_emails(raw: dict, include_body: bool = False) -> dict:
    """Format raw GMAIL_FETCH_EMAILS response."""
    messages = raw.get("messages", [])
    if not messages:
        return {"emails": [], "count": 0, "message": "No emails found."}

    emails = []
    for msg in messages:
        emails.append(_format_email(msg, include_body=include_body))

    result = {"emails": emails, "count": len(emails)}
    if raw.get("nextPageToken"):
        result["has_more"] = True
    return result


def _format_thread(raw: dict) -> dict:
    """Format raw thread response."""
    messages = raw.get("messages", [])
    if not messages:
        return {"messages": [], "count": 0}

    formatted = []
    for msg in messages:
        formatted.append(_format_email(msg, include_body=True))

    return {"messages": formatted, "count": len(formatted)}


# ── Tool Dispatch ─────────────────────────────────────────────────────

def execute(tool_name: str, parameters: dict, api_key: str) -> dict:
    """Execute a Gmail tool. Dispatches to the right Composio action."""

    if tool_name == "gmail_fetch_emails":
        params = {
            "user_id": "me",
            "max_results": min(parameters.get("max_results", 10), 25),
            "verbose": False,  # Faster metadata fetching
        }
        include_body = parameters.get("include_body", False)
        if include_body:
            params["verbose"] = True

        if parameters.get("query"):
            params["query"] = parameters["query"]
        if parameters.get("label_ids"):
            params["label_ids"] = parameters["label_ids"]

        result = _execute_composio_action(
            "GMAIL_FETCH_EMAILS", params, api_key,
        )
        if "error" in result:
            return result
        return _format_emails(result, include_body=include_body)

    elif tool_name == "gmail_send_email":
        params = {
            "recipient_email": parameters["recipient_email"],
            "body": parameters["body"],
            "user_id": "me",
        }
        if parameters.get("subject"):
            params["subject"] = parameters["subject"]
        if parameters.get("is_html"):
            params["is_html"] = True
        if parameters.get("cc"):
            params["cc"] = parameters["cc"]
        if parameters.get("bcc"):
            params["bcc"] = parameters["bcc"]

        result = _execute_composio_action(
            "GMAIL_SEND_EMAIL", params, api_key,
        )
        if "error" in result:
            return result
        return {
            "sent": True,
            "message_id": result.get("id", result.get("messageId", "")),
            "thread_id": result.get("threadId", ""),
            "to": parameters["recipient_email"],
            "subject": parameters.get("subject", "(no subject)"),
        }

    elif tool_name == "gmail_create_draft":
        params = {
            "recipient_email": parameters["recipient_email"],
            "subject": parameters["subject"],
            "body": parameters["body"],
            "user_id": "me",
        }
        if parameters.get("is_html"):
            params["is_html"] = True
        if parameters.get("cc"):
            params["cc"] = parameters["cc"]
        if parameters.get("bcc"):
            params["bcc"] = parameters["bcc"]
        if parameters.get("thread_id"):
            params["thread_id"] = parameters["thread_id"]

        result = _execute_composio_action(
            "GMAIL_CREATE_EMAIL_DRAFT", params, api_key,
        )
        if "error" in result:
            return result
        return {
            "draft_created": True,
            "draft_id": result.get("id", result.get("draftId", "")),
            "to": parameters["recipient_email"],
            "subject": parameters["subject"],
        }

    elif tool_name == "gmail_reply_to_thread":
        params = {
            "thread_id": parameters["thread_id"],
            "recipient_email": parameters["recipient_email"],
            "message_body": parameters["message_body"],
            "user_id": "me",
        }
        if parameters.get("is_html"):
            params["is_html"] = True
        if parameters.get("cc"):
            params["cc"] = parameters["cc"]

        result = _execute_composio_action(
            "GMAIL_REPLY_TO_THREAD", params, api_key,
        )
        if "error" in result:
            return result
        return {
            "replied": True,
            "thread_id": parameters["thread_id"],
            "to": parameters["recipient_email"],
        }

    elif tool_name == "gmail_get_thread":
        params = {
            "thread_id": parameters["thread_id"],
            "user_id": "me",
        }

        result = _execute_composio_action(
            "GMAIL_FETCH_MESSAGE_BY_THREAD_ID", params, api_key,
        )
        if "error" in result:
            return result
        return _format_thread(result)

    elif tool_name == "gmail_get_profile":
        result = _execute_composio_action(
            "GMAIL_GET_PROFILE", {"user_id": "me"}, api_key,
        )
        if "error" in result:
            return result
        return {
            "email": result.get("emailAddress", ""),
            "total_messages": result.get("messagesTotal", 0),
            "total_threads": result.get("threadsTotal", 0),
        }

    else:
        return {"error": f"Unknown Gmail tool: {tool_name}"}
