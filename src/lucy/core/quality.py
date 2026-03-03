"""Response quality assessment for Lucy's agent loop.

Heuristic-only checks (zero LLM cost) that detect common failure patterns:
- Service name confusion (Clerk vs MoonClerk)
- Stuck loops (same tool called 4× in a row, 3+ consecutive errors)
- Low-agency / dead-end responses
- Off-topic or suspiciously short responses
- Multi-part request incompleteness

All functions are pure and side-effect-free except for structlog calls.
"""

from __future__ import annotations

import json
import re
from typing import Any

import structlog

logger = structlog.get_logger()


# ── Service name mismatch table ──────────────────────────────────────────────
_KNOWN_SERVICE_PAIRS: dict[str, list[str]] = {
    "clerk": ["moonclerk", "metabase"],
    "linear": ["linearb"],
    "notion": ["notionhq"],
    "stripe": ["stripe atlas"],
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def normalize_service_name(name: str) -> str:
    """Normalize a service name for fuzzy comparison."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def is_genuine_service_match(query: str, result_name: str) -> bool:
    """Check if a search result genuinely matches the queried service.

    Prevents Composio's fuzzy search from confusing different services:
    - "Clerk" ≠ "MoonClerk" (auth platform vs payment processor)
    - "Clerk" ≠ "Metabase" (completely unrelated)
    - "GitHub" = "GitHub" (exact match)
    - "google_calendar" ≈ "Google Calendar" (formatting variant)
    """
    q = normalize_service_name(query)
    r = normalize_service_name(result_name)

    if not q or not r:
        return True
    if q == r:
        return True
    if q.replace("_", "") == r.replace("_", ""):
        return True
    if r.startswith(q) and len(r) - len(q) <= 8:
        return True
    if q.startswith(r) and len(q) - len(r) <= 8:
        return True

    # Reject: query is a substring of a longer, different name
    if q in r and r != q:
        prefix = r[: r.index(q)]
        if prefix:
            return False

    return len(set(q) & set(r)) / max(len(set(q)), 1) > 0.7


# ── Search/connection result filtering ───────────────────────────────────────

def filter_search_results(
    result: dict[str, Any],
    max_results: int = 5,
) -> dict[str, Any]:
    """Pre-filter COMPOSIO_SEARCH_TOOLS results to top-N relevant items.

    Prevents the LLM from dumping 50 tools to the user.
    Only connected/relevant tools are kept.
    """
    if not isinstance(result, dict):
        return result

    items = result.get("items") or result.get("tools") or result.get("results")
    if not isinstance(items, list) or len(items) <= max_results:
        return result

    connected_items = [item for item in items if isinstance(item, dict) and item.get("connected")]
    disconnected_items = [
        item for item in items if isinstance(item, dict) and not item.get("connected")
    ]

    filtered = connected_items[:max_results]
    remaining = max_results - len(filtered)
    if remaining > 0:
        filtered.extend(disconnected_items[:remaining])

    key = "items" if "items" in result else ("tools" if "tools" in result else "results")
    return {**result, key: filtered, "_filtered_from": len(items)}


def validate_search_relevance(
    result: dict[str, Any],
    search_query: str,
    user_message: str,
) -> dict[str, Any]:
    """Validate search results against what the user actually asked for.

    Injects relevance warnings into results so the LLM doesn't blindly
    act on fuzzy matches from Composio's search.
    """
    if not isinstance(result, dict) or not search_query:
        return result

    items_key = next(
        (k for k in ("items", "tools", "results") if k in result),
        None,
    )
    if not items_key:
        return result

    items = result.get(items_key, [])
    if not isinstance(items, list):
        return result

    has_exact = False
    mismatched: list[str] = []

    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("app") or item.get("appName") or ""
        if is_genuine_service_match(search_query, name):
            has_exact = True
        else:
            mismatched.append(name)

    if mismatched and not has_exact:
        mismatch_str = ", ".join(f"'{m}'" for m in mismatched[:5])
        result["_relevance_warning"] = (
            f"IMPORTANT: You searched for '{search_query}' but the results "
            f"returned are for different services: {mismatch_str}. "
            f"These are NOT the same as '{search_query}'. Do NOT suggest "
            f"connecting to these services. Do NOT say 'I can build a custom "
            f"integration' without researching first. Instead, call "
            f"lucy_resolve_custom_integration(['{search_query}']) immediately "
            f"to research what integration options exist (MCP, OpenAPI, or API)."
        )
        logger.warning(
            "search_relevance_mismatch",
            query=search_query,
            mismatched=mismatched,
        )
    elif mismatched:
        bad_names = ", ".join(f"'{m}'" for m in mismatched[:5])
        result["_relevance_note"] = (
            f"Note: Some results ({bad_names}) may not match the user's "
            f"request for '{search_query}'. Only use results that exactly "
            f"match the requested service."
        )

    return result


def validate_connection_relevance(
    result: dict[str, Any],
    toolkits_requested: list[str],
    user_message: str,
) -> dict[str, Any]:
    """Validate connection results — catch when Composio returns wrong services.

    When the user asks to connect "Clerk" but Composio resolves it to
    "MoonClerk", this injects a correction so the LLM doesn't present
    the wrong service to the user.
    """
    if not isinstance(result, dict) or not toolkits_requested:
        return result

    connections = result.get("connections") or result.get("results") or []
    if not isinstance(connections, list):
        return result

    corrections: list[str] = []
    for req in toolkits_requested:
        for conn in connections:
            if not isinstance(conn, dict):
                continue
            resolved_name = conn.get("app") or conn.get("name") or conn.get("appName") or ""
            if not is_genuine_service_match(req, resolved_name):
                corrections.append(
                    f"'{req}' was matched to '{resolved_name}' which is a "
                    f"DIFFERENT service. Do NOT present this to the user as "
                    f"'{req}'."
                )

    if corrections:
        result["_connection_corrections"] = corrections
        result["_correction_instruction"] = (
            "WARNING: Some service name matches are INCORRECT. "
            + " ".join(corrections)
            + " If the correct service is not in Composio, do NOT say "
            "'I can build a custom integration' before researching. "
            "Call lucy_resolve_custom_integration with the correct service "
            "name immediately to discover what options exist."
        )

    return result


# ── Quality assessment ────────────────────────────────────────────────────────

def assess_response_quality(
    user_message: str,
    response_text: str | None,
) -> dict[str, Any]:
    """Heuristic confidence scoring for the agent's response.

    Checks for common error patterns WITHOUT an LLM call (zero cost).
    Returns a quality assessment dict with:
        - confidence: 1-10 score
        - should_escalate: whether to re-run with frontier model
        - reason: why escalation was triggered (if any)
        - issues: list of detected issues
    """
    issues: list[str] = []
    confidence = 10
    response_text = response_text or ""
    resp_lower = response_text.lower()
    user_lower = user_message.lower()

    # 1. Service name confusion detection
    for correct, wrong_matches in _KNOWN_SERVICE_PAIRS.items():
        if correct in user_lower:
            for wrong in wrong_matches:
                if wrong in resp_lower and correct not in resp_lower.replace(wrong, ""):
                    issues.append(
                        f"Service confusion: user asked about '{correct}' "
                        f"but response mentions '{wrong}'"
                    )
                    confidence -= 4

    # 2. Suggesting services the user didn't ask about
    service_suggestions = re.findall(
        r"(?:connect|link|authorize|integration for)\s+(?:\*\*?)?(\w[\w\s]{2,20}?)(?:\*\*?)?",
        resp_lower,
    )
    for suggested in service_suggestions:
        suggested_clean = suggested.strip()
        if (
            len(suggested_clean) > 2
            and suggested_clean not in user_lower
            and not any(
                is_genuine_service_match(w, suggested_clean)
                for w in user_lower.split()
                if len(w) > 3
            )
        ):
            issues.append(f"Suggesting unrequested service: '{suggested_clean}'")
            confidence -= 2

    # 3. "I can't find" when user expects action
    cant_patterns = [
        "i don't have",
        "i can't",
        "i couldn't",
        "i wasn't able",
        "no direct",
        "no native",
        "not available",
    ]
    # Mitigating phrases indicate the response offers an alternative — not a dead end.
    _CANT_MITIGATORS = ["but i can", "but here", "here's what i can", "alternatively", "instead"]
    _has_cant_mitigator = any(m in resp_lower for m in _CANT_MITIGATORS)
    if any(p in resp_lower for p in cant_patterns) and not _has_cant_mitigator:
        action_words = ["check", "get", "show", "list", "pull", "create", "report"]
        if any(w in user_lower for w in action_words):
            issues.append("Response says 'can't' but user expected action")
            confidence -= 1

    # 3b. Tool/infrastructure failure reported without exhausting alternatives
    _FAILURE_PHRASES = [
        "connection attempts failed",
        "all connection attempts",
        "failed to connect",
        "connection failed",
        "unable to connect",
        "could not connect",
        "couldn't connect",
        "error connecting",
    ]
    _CONNECT_REQUEST_WORDS = ["connect", "access", "read", "check", "verify", "database", "db"]
    if any(p in resp_lower for p in _FAILURE_PHRASES):
        if any(w in user_lower for w in _CONNECT_REQUEST_WORDS):
            issues.append(
                "Reported infrastructure/tool failure without exhausting alternative approaches"
            )
            confidence -= 4

    # 4. Response is very short for a complex question
    if len(user_message) > 60 and len(response_text) < 100:
        issues.append("Suspiciously short response for complex question")
        confidence -= 4

    # 5. Semantic relevance: check that response addresses the user's topic
    user_keywords = {
        w
        for w in re.findall(r"[a-z]{4,}", user_lower)
        if w
        not in {
            "what", "does", "have", "this", "that", "with", "from", "about",
            "they", "them", "your", "their", "been", "were", "will", "would",
            "could", "should", "which", "where", "when", "some", "many",
            "much", "also", "just", "than", "more", "most", "very", "each",
            "every", "list", "show", "give", "tell", "want", "need", "make",
            "please", "right", "connected", "currently", "today",
        }
    }
    if len(user_keywords) >= 2 and len(response_text) > 80:
        resp_words = set(re.findall(r"[a-z]{4,}", resp_lower))
        overlap = user_keywords & resp_words
        overlap_ratio = len(overlap) / len(user_keywords) if user_keywords else 1.0
        if overlap_ratio < 0.15 and len(user_keywords) >= 3:
            issues.append(
                f"Response may be off-topic: only {len(overlap)}/{len(user_keywords)} "
                f"user keywords appear in the response"
            )
            confidence -= 3

    confidence = max(1, min(10, confidence))
    should_escalate = confidence <= 6 and len(issues) > 0

    if issues:
        logger.info(
            "quality_gate_assessment",
            confidence=confidence,
            issues=issues,
            should_escalate=should_escalate,
        )

    return {
        "confidence": confidence,
        "should_escalate": should_escalate,
        "reason": "; ".join(issues) if issues else "",
        "issues": issues,
    }


# ── Stuck detection ────────────────────────────────────────────────────────────

def _has_real_error(content: str) -> bool:
    """Check if a tool result contains a genuine error, not just an empty error field."""
    try:
        data = json.loads(content) if content.strip().startswith("{") else {}
    except (json.JSONDecodeError, ValueError):
        data = {}
    if isinstance(data, dict):
        err = data.get("error", "")
        if err and str(err).strip():
            return True
        nested = data.get("data", {})
        if isinstance(nested, dict):
            nerr = nested.get("error", "")
            if nerr and str(nerr).strip():
                return True
    if "traceback" in content.lower() or "exception" in content.lower():
        return True
    return False


def detect_stuck_state(
    messages: list[dict[str, Any]],
    current_turn: int,
) -> dict[str, Any]:
    """Analyze recent tool results to detect if the agent is stuck.

    Looks for patterns like:
    - Same error appearing in consecutive tool results
    - Repeated calls to the same tool with same/similar args
    - Tools returning errors in multiple consecutive turns
    """
    result: dict[str, Any] = {
        "is_stuck": False,
        "reason": "",
        "intervention": "",
        "escalate_model": False,
    }

    if current_turn < 3:
        return result

    recent_tool_results: list[str] = []
    recent_tool_names: list[str] = []

    for msg in messages[-12:]:
        if msg.get("role") == "tool":
            recent_tool_results.append(msg.get("content", ""))
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls", []):
                fn = tc.get("function", {})
                recent_tool_names.append(fn.get("name", ""))

    error_count = sum(1 for r in recent_tool_results[-4:] if _has_real_error(r))
    if error_count >= 3:
        result["is_stuck"] = True
        result["reason"] = f"{error_count} consecutive tool errors detected"
        result["intervention"] = (
            "ATTENTION: Multiple consecutive tool calls have returned errors. "
            "STOP repeating the same approach. Take a step back and try a "
            "completely different strategy. If an API call keeps failing, "
            "use lucy_web_search to look up the correct API usage. If a "
            "script keeps erroring, read the error message carefully and "
            "fix the root cause before retrying."
        )
        result["escalate_model"] = True
        return result

    if len(recent_tool_names) >= 4:
        last_four = recent_tool_names[-4:]
        _STUCK_EXEMPT = {
            "COMPOSIO_REMOTE_WORKBENCH",
            "COMPOSIO_REMOTE_BASH_TOOL",
            "lucy_exec_command",
            "lucy_poll_process",
        }
        if (
            last_four[0] not in _STUCK_EXEMPT
            and not (last_four[0].startswith("lucy_custom_") and "_list_" in last_four[0])
            and len(set(last_four)) == 1
        ):
            result["is_stuck"] = True
            result["reason"] = f"Same tool ({last_four[0]}) called 4x in a row"
            result["intervention"] = (
                f"ATTENTION: You have called {last_four[0]} four times in a "
                f"row. This looks like a loop. If it keeps failing, try a "
                f"different approach entirely. Consider using "
                f"lucy_exec_command or COMPOSIO_REMOTE_WORKBENCH to write a script instead of "
                f"repeated tool calls."
            )
            result["escalate_model"] = True
            return result

    return result


# ── Output verification ────────────────────────────────────────────────────────

def verify_output(
    user_message: str,
    response_text: str,
    intent: str,
) -> dict[str, Any]:
    """Heuristic verification that the response addresses the request.

    Zero-cost check (no LLM call) that catches common completeness failures.
    Returns a dict with:
        - passed: whether all checks passed
        - issues: list of specific failures detected
        - should_retry: whether a retry with failure context is warranted
    """
    issues: list[str] = []
    user_lower = user_message.lower()
    resp_lower = response_text.lower()

    all_data_signals = [
        "all users", "all customers", "all data", "all records", "every user",
        "every customer", "complete list", "complete report", "full report",
        "full list", "raw data", "entire", "user base",
    ]
    wants_all = any(s in user_lower for s in all_data_signals)

    if wants_all:
        sample_signals = [
            "showing first 20", "sample of", "here are 20",
            "first 20 users", "showing a sample", "top 20",
        ]
        if any(s in resp_lower for s in sample_signals):
            issues.append(
                "User asked for ALL data but response only contains a sample. "
                "Use COMPOSIO_REMOTE_WORKBENCH to write a script that "
                "paginates through the API and fetches every record."
            )

    multi_part_markers = {
        "excel": [r"\bexcel\b", r"\bspreadsheet\b", r"\bworkbook\b"],
        "google_drive": [r"\bgoogle\s+drive\b", r"upload\b.{0,10}\bto\s+drive\b"],
        "email_send": [r"send\b.*\bemail\b", r"\bemail\b.{0,15}\breport\b.*\bto\b.*@"],
        "summary": [r"\bpost\b.{0,10}\bsummary\b", r"\bgive\b.{0,10}\bsummary\b"],
    }
    requested_parts: list[str] = []
    for part_name, keywords in multi_part_markers.items():
        if any(re.search(kw, user_lower) for kw in keywords):
            requested_parts.append(part_name)

    if len(requested_parts) >= 2:
        delivered_signals = {
            "excel": ["excel", "spreadsheet", ".xlsx", "openpyxl", "file.*upload"],
            "google_drive": ["drive", "uploaded", "shared", "link"],
            "email_send": [
                "email sent", "emailed", "sent.*email", "email.*to.*@",
                "✅.*email", ":white_check_mark:.*email",
            ],
            "summary": [
                "summary", "total", "breakdown", "here's", "overview",
                "results", "report", "findings",
            ],
        }
        for part in requested_parts:
            signals = delivered_signals.get(part, [])
            if signals and not any(re.search(s, resp_lower) for s in signals):
                issues.append(f"User requested '{part}' but it appears missing from the response.")

    if intent == "data" and len(response_text) < 100:
        issues.append(
            "Data task produced a very short response. "
            "Expected detailed output with counts and deliverables."
        )

    degradation_phrases = [
        "ran into a hiccup", "ran into an issue",
        "let me try a different approach", "couldn't complete",
        "unable to process", "something went wrong",
    ]
    if any(p in resp_lower for p in degradation_phrases):
        issues.append(
            "Response is a degradation/error fallback, not an actual answer. "
            "Retry with a fresh approach using the available tools."
        )

    if len(user_lower) > 150 and len(response_text) < 150:
        issues.append(
            "Complex multi-step request received a very short response. "
            "Break the task into steps and execute each one."
        )

    # High-agency check: detect surrender/dead-end patterns
    low_agency_patterns = [
        "i can't access", "i'm unable to", "unfortunately i cannot",
        "i don't have access to", "i'm not able to", "i cannot access",
        "i am unable to", "that's outside my", "falls outside my",
        "i don't have the ability", "i'm afraid i can't",
        "this took longer than expected and i had to stop",
        "the request may have been too complex",
        "want me to try a simpler approach",
        "i wasn't able to complete", "i'm having trouble with",
        "something went wrong", "i hit a snag",
    ]
    action_words = [
        "get", "show", "pull", "fetch", "create", "build", "generate",
        "send", "connect", "set up", "check", "find", "run", "analyze",
        "monitor", "track", "report", "scan", "deploy", "start", "write",
    ]
    has_action_intent = any(w in user_lower for w in action_words)
    has_low_agency = any(p in resp_lower for p in low_agency_patterns)
    if has_low_agency and has_action_intent:
        issues.append(
            "CRITICAL: Response contains a dead-end or surrender pattern. You are a "
            "high-agency problem solver. Every problem is solvable. Rewrite to: "
            "(1) Offer the closest thing you CAN do right now, "
            "(2) Explain specifically what you'd need to do the full thing, "
            "(3) Provide at least one alternative path forward. "
            "Never leave the user with a dead end. Never suggest they 'try a simpler "
            "approach' — YOU find the simpler approach and execute it. "
            "Example: instead of 'I can't access Figma directly' say "
            "'I can't pull from Figma directly, but two options: drop the file here "
            "and I'll extract the content, or I can build a custom connection. "
            "Which works better?'"
        )
    elif has_low_agency:
        issues.append(
            "Response contains a 'can't do' pattern without offering an alternative. "
            "Even if the user didn't explicitly ask for action, always follow 'I can't X' "
            "with 'but I can Y' or 'here's what would make it possible'. No dead ends."
        )

    return {
        "passed": len(issues) == 0,
        "issues": issues,
        "should_retry": len(issues) > 0,
    }


# ── Test-pass detection ───────────────────────────────────────────────────────

# Signals that the agent confirmed tests actually passed.
_TEST_PASS_RE = re.compile(
    r"(?:"
    r"TEST PASSED"
    r"|TESTS? PASSED"
    r"|VERIFICATION PASSED"
    r"|\bverif(?:ied|ication passed)\b"
    r"|\bworking correctly\b"
    r"|\bconfirmed working\b"
    r"|\bconfirmed.*success"
    r"|\bsuccessfully (?:verified|tested|confirmed|triggered|ran|executed)\b"
    r"|\btriggered.*success"
    r"|\ball (?:tests?|checks?) pass"
    r"|\bno errors?\b.*\b(?:found|detected|occurred)\b"
    r"|\bexited with (?:code )?0\b"
    r"|\breturned (?:successfully|with no errors?)\b"
    r"|\bdeployed and (?:live|working)\b"
    r"|\bservice is (?:running|live|healthy)\b"
    r")",
    re.IGNORECASE,
)

# Signals that something actually failed during testing.
_TEST_FAIL_RE = re.compile(
    r"(?:"
    r"TEST FAILED"
    r"|TESTS? FAILED"
    r"|VERIFICATION FAILED"
    r"|\bTraceback \(most recent call last\)"
    r"|\bSyntaxError\b"
    r"|\bImportError\b"
    r"|\bModuleNotFoundError\b"
    r"|\bNameError\b"
    r"|\bAttributeError\b"
    r"|\bTypeError\b"
    r"|\bRuntimeError\b"
    r"|\bException:\b"
    r"|\bexit(?:ed)? with (?:code )?[1-9]\d*\b"
    r"|\berror(?:ed)?\b.{0,60}(?:line \d+|exception|failed)"
    r"|\bfailed to (?:run|execute|import|connect|start)\b"
    r"|\bscript (?:crashed|errored|failed)\b"
    r"|\bnot working\b"
    r"|\bdid not (?:run|work|execute|start)\b"
    r"|\bcould not (?:connect|find|import|run|execute)\b"
    r"|\bHTTP [45]\d{2}\b"
    r"|\bconnection (?:refused|timed? out|failed)\b"
    r"|\b(?:still|keeps?) (?:failing|broken|erroring)\b"
    r")",
    re.IGNORECASE,
)

# Ambiguity signals — agent is just narrating setup, not reporting test results.
_TEST_AMBIGUOUS_RE = re.compile(
    r"(?:"
    r"\bset up\b|\bcreated\b|\bupdated\b|\bmodified\b"
    r"|\bscheduled\b|\bconfigured\b"
    r")",
    re.IGNORECASE,
)


def response_indicates_test_pass(text: str) -> tuple[bool, str]:
    """Determine whether an agent verification response shows tests passing.

    Returns ``(True, "")`` when the response clearly indicates success.
    Returns ``(False, reason)`` when the response shows failures or is ambiguous.

    The agent is instructed to prefix verification results with "TEST PASSED:"
    or "TEST FAILED:" — those are the primary signals. The regex patterns are
    fallbacks for when the agent doesn't follow the format exactly.
    """
    if not text:
        return False, "empty verification response"

    lower = text.lower()

    # Explicit failures always win, even if there are also positive signals.
    # A traceback with a partial success is still a failure.
    if _TEST_FAIL_RE.search(text):
        # Extract a short reason from the first matching failure phrase
        m = _TEST_FAIL_RE.search(text)
        snippet = text[max(0, m.start() - 20) : m.end() + 60].strip()
        snippet = snippet.replace("\n", " ")[:120]
        return False, f"test failure detected: {snippet}"

    if _TEST_PASS_RE.search(text):
        return True, ""

    # Neither explicit pass nor explicit fail — treat as ambiguous.
    # Force the agent to be more explicit rather than silently accepting.
    return False, (
        "verification response is ambiguous — no clear TEST PASSED or TEST FAILED "
        "signal. Agent must explicitly confirm with 'TEST PASSED: <what was tested>' "
        "or 'TEST FAILED: <what failed>'."
    )
