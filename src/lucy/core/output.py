"""Three-layer output pipeline for Lucy's Slack messages.

Layer 1: Sanitizer — strips paths, tool names, internal references
Layer 2: Format converter — transforms Markdown to Slack mrkdwn
Layer 3: Tone validator — catches robotic/error-dump patterns

Applied to every message before posting to Slack.
"""

from __future__ import annotations

import re

import structlog

logger = structlog.get_logger()

# ═══════════════════════════════════════════════════════════════════════
# LAYER 1: OUTPUT SANITIZER
# ═══════════════════════════════════════════════════════════════════════

_REDACT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"/home/user/[^\s)\"']+"), ""),
    (re.compile(r"/workspace[s]?/[^\s)\"']+"), ""),
    (re.compile(r"@?workspace_seeds[^\s]*"), ""),
    # Contextual Composio meta-tool replacements (match with optional verb prefix)
    (re.compile(r"(?:using |called |via |through )?COMPOSIO_SEARCH_TOOLS"), "searching available tools"),
    (re.compile(r"(?:using |called |via |through )?COMPOSIO_MANAGE_CONNECTIONS"), "checking integrations"),
    (re.compile(r"(?:using |called |via |through )?COMPOSIO_MULTI_EXECUTE_TOOL"), "running actions"),
    (re.compile(r"(?:using |called |via |through )?COMPOSIO_REMOTE_WORKBENCH"), "running some code"),
    (re.compile(r"(?:using |called |via |through )?COMPOSIO_REMOTE_BASH_TOOL"), "running a script"),
    (re.compile(r"(?:using |called |via |through )?COMPOSIO_GET_TOOL_SCHEMAS"), "looking up tool details"),
    (re.compile(r"COMPOSIO_\w+"), ""),  # catch-all for any remaining
    # Custom integration tool names (lucy_custom_<slug>_<action>)
    (re.compile(r"`?lucy_custom_\w+`?"), ""),
    (re.compile(r"\blucy_\w+\b"), ""),
    # NOTE: Do NOT strip composio.dev URLs — they are user-facing auth links.
    # Only strip the brand name "composio" when it appears as plain text
    # (not inside a URL).
    (re.compile(r"(?<![\w/.])composio(?!\.dev)(?!\.\w)", re.IGNORECASE), ""),
    (re.compile(r"(?i)\bopenrouter\b"), ""),
    (re.compile(r"(?i)\bopenclaw\b"), ""),
    (re.compile(r"(?i)\bminimax\b"), ""),
    (re.compile(r"SKILL\.md|LEARNINGS\.md|state\.json"), "my notes"),
    (re.compile(r"\btool[_ ]?call[s]?\b", re.IGNORECASE), "request"),
    (re.compile(r"\bmeta[- ]?tool[s]?\b", re.IGNORECASE), ""),
    (re.compile(r"\bfunction calling\b", re.IGNORECASE), ""),
]

_ALLCAPS_TOOL_RE = re.compile(r"\b[A-Z]{2,}_[A-Z_]{3,}\b")

_HUMANIZE_MAP = {
    # Composio meta-tools
    "COMPOSIO_SEARCH_TOOLS": "search for tools",
    "COMPOSIO_MANAGE_CONNECTIONS": "manage integrations",
    "COMPOSIO_MULTI_EXECUTE_TOOL": "execute actions",
    "COMPOSIO_REMOTE_WORKBENCH": "run code",
    "COMPOSIO_REMOTE_BASH_TOOL": "run a script",
    "COMPOSIO_GET_TOOL_SCHEMAS": "look up tool details",
    # Google
    "GOOGLECALENDAR_CREATE_EVENT": "schedule a meeting",
    "GOOGLECALENDAR_EVENTS_LIST": "check your calendar",
    "GOOGLECALENDAR_FIND_FREE_SLOTS": "find open time slots",
    "GMAIL_SEND_EMAIL": "send an email",
    "GMAIL_GET_EMAILS": "check your email",
    "GMAIL_CREATE_DRAFT": "draft an email",
    "GOOGLEDRIVE_LIST_FILES": "check your Drive",
    "GOOGLEDRIVE_CREATE_FILE": "create a file in Drive",
    "GOOGLESHEETS_GET_SPREADSHEET": "check a spreadsheet",
    # GitHub
    "GITHUB_LIST_PULL_REQUESTS": "check pull requests",
    "GITHUB_CREATE_ISSUE": "create an issue",
    "GITHUB_GET_REPOSITORY": "check the repository",
    # Linear
    "LINEAR_CREATE_ISSUE": "create a Linear ticket",
    "LINEAR_LIST_ISSUES": "check Linear issues",
}


def _sanitize(text: str) -> str:
    for pattern, replacement in _REDACT_PATTERNS:
        text = pattern.sub(replacement, text)

    def _humanize_or_strip(match: re.Match[str]) -> str:
        name = match.group(0)
        return _HUMANIZE_MAP.get(name, "")

    text = _ALLCAPS_TOOL_RE.sub(_humanize_or_strip, text)
    text = re.sub(r"  +", " ", text)
    return text


# ═══════════════════════════════════════════════════════════════════════
# LAYER 2: MARKDOWN → SLACK MRKDWN CONVERTER
# ═══════════════════════════════════════════════════════════════════════

def _convert_markdown_to_slack(text: str) -> str:
    text = _convert_tables_to_lists(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    text = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", text, flags=re.MULTILINE)
    text = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"<\2|\1>", text,
    )
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def _convert_tables_to_lists(text: str) -> str:
    lines = text.split("\n")
    result: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if "|" in line and line.startswith("|"):
            table_lines: list[str] = []
            while (
                i < len(lines)
                and "|" in lines[i].strip()
                and lines[i].strip().startswith("|")
            ):
                table_lines.append(lines[i].strip())
                i += 1
            result.extend(_table_to_bullets(table_lines))
        else:
            result.append(lines[i])
            i += 1
    return "\n".join(result)


def _table_to_bullets(table_lines: list[str]) -> list[str]:
    rows: list[list[str]] = []
    for line in table_lines:
        cells = [c.strip() for c in line.strip("|").split("|")]
        cells = [c for c in cells if c]
        if cells and not all(
            c.replace("-", "").replace(":", "").strip() == "" for c in cells
        ):
            rows.append(cells)

    if len(rows) < 2:
        return table_lines

    headers = rows[0]
    bullets: list[str] = []
    for row in rows[1:]:
        if len(headers) >= 2 and len(row) >= 2:
            label = row[0]
            details = " — ".join(
                f"{headers[j]}: {row[j]}"
                for j in range(1, min(len(headers), len(row)))
                if row[j].strip()
            )
            bullets.append(f"• *{label}* — {details}" if details else f"• *{label}*")
        else:
            bullets.append(f"• {' | '.join(row)}")
    return [""] + bullets + [""]


# ═══════════════════════════════════════════════════════════════════════
# LAYER 3: TONE VALIDATOR
# ═══════════════════════════════════════════════════════════════════════

_TONE_REJECT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"I wasn't able to", re.IGNORECASE),
    re.compile(r"Could you try rephrasing", re.IGNORECASE),
    re.compile(r"running into a loop", re.IGNORECASE),
    re.compile(r"tool call[s]? failed", re.IGNORECASE),
    re.compile(r"I hit a snag", re.IGNORECASE),
    re.compile(r"Something went wrong", re.IGNORECASE),
    re.compile(r"several tool calls", re.IGNORECASE),
    re.compile(r"try rephrasing", re.IGNORECASE),
    re.compile(r"after several attempts", re.IGNORECASE),
    re.compile(r"I was(?:n't| not) able to complete", re.IGNORECASE),
    re.compile(r"(?:great|excellent|wonderful|fantastic) question", re.IGNORECASE),
    re.compile(r"(?:I'd be )?happy to help", re.IGNORECASE),
    re.compile(r"it's worth noting", re.IGNORECASE),
    re.compile(r"let me delve into", re.IGNORECASE),
]

_TONE_REPLACEMENTS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"I (?:wasn't|was not) able to (?:complete |finish )?(?:the |this |your )?request[^.]*\.?", re.IGNORECASE),
        "Let me try a different approach on this.",
    ),
    (
        re.compile(r"(?:Could you |Please )try rephrasing[^.]*\.?", re.IGNORECASE),
        "Could you give me a bit more detail on what you're looking for?",
    ),
    (
        re.compile(r"I hit a snag[^.]*\.?", re.IGNORECASE),
        "Let me take another look at this.",
    ),
    (
        re.compile(r"Something went wrong[^.]*\.?", re.IGNORECASE),
        "Working on getting that sorted.",
    ),
    (
        re.compile(r"(?:That's a |This is a |What a )?(?:great|excellent|wonderful|fantastic) question[!.,]?\s*", re.IGNORECASE),
        "",
    ),
    (
        re.compile(r"I'd be happy to help[!.,]?\s*", re.IGNORECASE),
        "",
    ),
    (
        re.compile(r"[Ii]t's worth noting that\s*", re.IGNORECASE),
        "",
    ),
    (
        re.compile(r"[Ll]et me delve into\s*", re.IGNORECASE),
        "Here's ",
    ),
]


_BROKEN_URLS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"<https?://[a-z]{2,15}\.\|[^>]*>", re.IGNORECASE),
        "_(link unavailable — use `/lucy connect <service>`)_",
    ),
    (
        re.compile(r"<https?://[a-z]{2,15}\.>", re.IGNORECASE),
        "_(link unavailable — use `/lucy connect <service>`)_",
    ),
    (
        re.compile(r"\[([^\]]*)\]\(https?://[a-z]{2,15}\.[)\s]", re.IGNORECASE),
        "_(link unavailable — use `/lucy connect <service>`)_ ",
    ),
    (
        re.compile(r"https?://[a-z]{2,15}\.\s", re.IGNORECASE),
        "_(link unavailable — use `/lucy connect <service>`)_ ",
    ),
]


def _fix_broken_urls(text: str) -> str:
    """Remove broken/truncated URLs in both plain and Slack mrkdwn format."""
    for pattern, replacement in _BROKEN_URLS:
        text = pattern.sub(replacement, text)
    return text


def _validate_tone(text: str) -> str:
    for pattern, replacement in _TONE_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    return text


# ═══════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════

def process_output(text: str) -> str:
    """Run all three output layers on a message before posting to Slack."""
    if not text or not text.strip():
        return text

    text = _sanitize(text)
    text = _fix_broken_urls(text)
    text = _convert_markdown_to_slack(text)
    text = _validate_tone(text)
    return text.strip()
