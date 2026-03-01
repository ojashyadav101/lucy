"""Response quality assessment — zero-cost heuristic checks.

Standalone functions with no class dependencies. Zero LLM calls.
Pure pattern matching and heuristics for quality gating.

Sections:
1. Service name matching (prevents Composio fuzzy-search confusion)
2. Search/connection result validation
3. Response quality assessment (confidence scoring)
4. Response depth assessment (data-dump detection, depth scoring)
5. Stuck-state detection
6. Output verification
"""

from __future__ import annotations

import json
import re
from typing import Any

import structlog

logger = structlog.get_logger()

# ═══════════════════════════════════════════════════════════════════════════
# Service name matching (prevents Composio fuzzy-search confusion)
# ═══════════════════════════════════════════════════════════════════════════

_KNOWN_SERVICE_PAIRS: dict[str, list[str]] = {
    "clerk": ["moonclerk", "metabase"],
    "linear": ["linearb"],
    "notion": ["notionhq"],
    "stripe": ["stripe atlas"],
}


def normalize_service_name(name: str) -> str:
    """Normalize service name for comparison."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def is_genuine_service_match(query: str, result_name: str) -> bool:
    """Check if a search result genuinely matches the queried service.

    Prevents Composio's fuzzy search from confusing different services:
    - "Clerk" ≠ "MoonClerk" (auth platform vs payment processor)
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
    if q in r and r != q:
        prefix = r[:r.index(q)]
        if prefix:
            return False
    return len(set(q) & set(r)) / max(len(set(q)), 1) > 0.7


# ═══════════════════════════════════════════════════════════════════════════
# Search / connection result validation
# ═══════════════════════════════════════════════════════════════════════════


def filter_search_results(
    result: dict[str, Any],
    max_results: int = 5,
) -> dict[str, Any]:
    """Pre-filter COMPOSIO_SEARCH_TOOLS results to top-N relevant items."""
    if not isinstance(result, dict):
        return result

    items = result.get("items") or result.get("tools") or result.get("results")
    if not isinstance(items, list) or len(items) <= max_results:
        return result

    connected_items = [
        item for item in items
        if isinstance(item, dict) and item.get("connected")
    ]
    disconnected_items = [
        item for item in items
        if isinstance(item, dict) and not item.get("connected")
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
    """Validate search results against what the user actually asked for."""
    if not isinstance(result, dict) or not search_query:
        return result

    items_key = next(
        (k for k in ("items", "tools", "results") if k in result), None,
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
            f"connecting to these services. Instead, acknowledge that "
            f"'{search_query}' is not available as a native integration "
            f"and offer to build a custom connection."
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
    """Validate connection results — catch when Composio returns wrong services."""
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
            resolved_name = (
                conn.get("app") or conn.get("name")
                or conn.get("appName") or ""
            )
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
            + " If the correct service is not available, acknowledge "
            "honestly and offer to build a custom integration."
        )

    return result


# ═══════════════════════════════════════════════════════════════════════════
# Response quality assessment (zero-cost heuristics)
# ═══════════════════════════════════════════════════════════════════════════


def assess_response_quality(
    user_message: str,
    response_text: str | None,
    connected_services: list[str] | None = None,
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

    # 0. Connection link for already-connected services
    if connected_services:
        _connect_link_re = re.compile(
            r"composio\.dev/connect|connect here|connect (?:your |the )?(?:"
            + "|".join(re.escape(s.lower()) for s in connected_services)
            + r")",
            re.IGNORECASE,
        )
        _not_connected_re = re.compile(
            r"(?:don't|do not|doesn't|does not) have (?:active |any )?"
            r"(?:connections?|access)",
            re.IGNORECASE,
        )
        if _connect_link_re.search(response_text):
            issues.append(
                f"Offering connection links for already-connected services "
                f"({', '.join(connected_services)})"
            )
            confidence -= 5
        elif _not_connected_re.search(response_text):
            issues.append(
                f"Claims no active connections but these are connected: "
                f"{', '.join(connected_services)}"
            )
            confidence -= 4

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
            issues.append(
                f"Suggesting unrequested service: '{suggested_clean}'"
            )
            confidence -= 2

    # 3. "I can't find" when user expects action
    cant_patterns = [
        "i don't have", "i can't", "i couldn't", "i wasn't able",
        "no direct", "no native", "not available",
    ]
    if any(p in resp_lower for p in cant_patterns):
        action_words = ["check", "get", "show", "list", "pull", "create", "report"]
        if any(w in user_lower for w in action_words):
            issues.append(
                "Response says 'can't' but user expected action"
            )
            confidence -= 1

    # 4. Response is very short for a complex question
    _COMMAND_WORDS = {"save", "delete", "remove", "create", "set", "update",
                      "write", "store", "add", "send", "deploy", "start",
                      "stop", "trigger", "schedule", "cancel"}
    is_command = any(w in user_lower.split()[:5] for w in _COMMAND_WORDS)
    if len(user_message) > 80 and len(response_text) < 80 and not is_command:
        issues.append("Suspiciously short response for complex question")
        confidence -= 4

    # 5. Depth check — data dump without analysis
    depth_result = compute_depth_score(user_message, response_text)
    if depth_result["is_shallow"]:
        issues.append(
            f"Shallow response detected (depth {depth_result['depth_score']}/10). "
            f"Missing: {', '.join(depth_result['missing_layers'])}. "
            f"Add interpretation, comparison, or recommendations."
        )
        confidence -= 2

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


# ═══════════════════════════════════════════════════════════════════════════
# Response depth assessment (NEW — data-dump detection + depth scoring)
# ═══════════════════════════════════════════════════════════════════════════

# Signals that the response contains data/numbers
_DEPTH_DATA_PATTERNS = [
    re.compile(r"\b\d{1,3}(?:,\d{3})+\b"),              # formatted numbers
    re.compile(r"\$[\d,.]+[KkMmBb]?"),                   # currency
    re.compile(r"\b\d+(?:\.\d+)?%"),                     # percentages
    re.compile(r"\b\d+\s*(?:users?|customers?|subscribers?|records?)\b", re.I),
    re.compile(r"\b(?:MRR|ARR|LTV|CAC|NPS|DAU|MAU)\b"),
]

# Signals of interpretation/analysis
_DEPTH_INTERPRETATION_PATTERNS = [
    re.compile(r"\b(?:this (?:means|suggests|indicates|shows)|trend|pattern|shift)\b", re.I),
    re.compile(r"\b(?:because|driven by|likely due to|caused by|correlat)\b", re.I),
    re.compile(r"\b(?:compared to|vs\.?|versus|up from|down from|MoM|YoY)\b", re.I),
    re.compile(r"\b(?:up|down|increased|decreased|grew|fell|rose|dropped)\s+(?:by\s+)?\d", re.I),
]

# Signals of actionable recommendations
_DEPTH_RECOMMENDATION_PATTERNS = [
    re.compile(r"\b(?:recommend|suggest|consider|should|want me to)\b", re.I),
    re.compile(r"\b(?:next step|action item|follow[- ]?up|set (?:this |it )?up)\b", re.I),
    re.compile(r"\b(?:dig(?:ging)? deeper|look(?:ing)? into|investigate|automate)\b", re.I),
]

# Signals that user expects analysis
_DEPTH_ANALYSIS_INTENT = [
    re.compile(r"\b(?:how (?:is|are|was|were)|how's)\b", re.I),
    re.compile(r"\b(?:analyze|analysis|insight|trend|breakdown|report)\b", re.I),
    re.compile(r"\b(?:pull|show|get|fetch)\s+(?:my|our|the)\b", re.I),
]

# Data-dump signals (data without analysis wrapper)
_DEPTH_DUMP_PATTERNS = [
    re.compile(r"(?:here (?:is|are)|here's) (?:the|your)\s+(?:data|list|report|breakdown)", re.I),
    re.compile(r"(?:^|\n)\s*•\s*\*?\w[^:]*\*?:\s*[\$\d]", re.MULTILINE),
]


def compute_depth_score(
    user_message: str,
    response_text: str,
    tool_calls_count: int = 0,
) -> dict[str, Any]:
    """Compute a depth score (1-10) for a response.

    Integrated into assess_response_quality as an additional quality signal.
    Also usable standalone for depth-specific checks.

    Returns:
        {
            "depth_score": int (1-10),
            "is_shallow": bool,
            "has_data": bool,
            "has_interpretation": bool,
            "has_recommendations": bool,
            "missing_layers": list[str],
        }
    """
    if not response_text or len(response_text) < 50:
        return {
            "depth_score": 5,
            "is_shallow": False,
            "has_data": False,
            "has_interpretation": False,
            "has_recommendations": False,
            "missing_layers": [],
        }

    has_data = sum(1 for p in _DEPTH_DATA_PATTERNS if p.search(response_text)) >= 1
    has_interpretation = sum(1 for p in _DEPTH_INTERPRETATION_PATTERNS if p.search(response_text)) >= 2
    has_recommendations = sum(1 for p in _DEPTH_RECOMMENDATION_PATTERNS if p.search(response_text)) >= 1
    is_dump = (
        has_data
        and sum(1 for p in _DEPTH_DUMP_PATTERNS if p.search(response_text)) >= 1
        and not has_interpretation
    )
    user_wants_analysis = sum(1 for p in _DEPTH_ANALYSIS_INTENT if p.search(user_message)) >= 1

    # Score calculation
    score = 3
    if has_data:
        score += 1
    if has_interpretation:
        score += 2
    if has_recommendations:
        score += 1
    if len(response_text) > 500 and has_data:
        score += 1
    if is_dump:
        score -= 2
    if tool_calls_count >= 3 and score < 6:
        score -= 1
    score = max(1, min(10, score))

    # Missing layers
    missing: list[str] = []
    if has_data and not has_interpretation:
        missing.append("interpretation")
    if (has_data or user_wants_analysis) and not has_recommendations:
        missing.append("recommendations")

    # Shallow determination: data response missing 2+ layers, or data dump
    is_shallow = (
        (has_data and len(missing) >= 2)
        or (user_wants_analysis and score <= 5)
        or is_dump
    )

    return {
        "depth_score": score,
        "is_shallow": is_shallow,
        "has_data": has_data,
        "has_interpretation": has_interpretation,
        "has_recommendations": has_recommendations,
        "missing_layers": missing,
    }


def detect_data_dump(response_text: str) -> bool:
    """Quick check: is this a data dump without interpretation?

    Fast gate for use in output pipeline before full depth assessment.
    """
    if not response_text or len(response_text) < 80:
        return False

    has_data = sum(1 for p in _DEPTH_DATA_PATTERNS if p.search(response_text)) >= 2
    has_dump = sum(1 for p in _DEPTH_DUMP_PATTERNS if p.search(response_text)) >= 1
    has_interp = sum(1 for p in _DEPTH_INTERPRETATION_PATTERNS if p.search(response_text)) >= 2

    return has_data and has_dump and not has_interp


# ═══════════════════════════════════════════════════════════════════════════
# Stuck-state detection
# ═══════════════════════════════════════════════════════════════════════════


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
            content = msg.get("content", "")
            recent_tool_results.append(content)
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls", []):
                fn = tc.get("function", {})
                recent_tool_names.append(fn.get("name", ""))

    def _has_real_error(content: str) -> bool:
        """Check if tool result contains a genuine error."""
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

    error_count = sum(1 for r in recent_tool_results if _has_real_error(r))
    if error_count >= 3:
        result["is_stuck"] = True
        result["reason"] = (
            f"Last {len(recent_tool_results)} tool calls had "
            f"{error_count} errors"
        )
        result["intervention"] = (
            "Multiple consecutive tool errors detected. "
            "Stop retrying the same approach. "
            "Try a completely different tool or method. "
            "If you're stuck on an API, try using the workbench "
            "to write a direct script instead."
        )

    if len(recent_tool_names) >= 4:
        last_4 = recent_tool_names[-4:]
        if len(set(last_4)) == 1:
            result["is_stuck"] = True
            result["reason"] = (
                f"Same tool '{last_4[0]}' called 4+ times in a row"
            )
            result["intervention"] = (
                f"You've called '{last_4[0]}' multiple times with "
                f"similar results. This approach isn't working. "
                f"Try a fundamentally different tool or strategy."
            )

    if current_turn >= 8 and not result["is_stuck"]:
        progress_signals = ["success", "created", "found", "result", "data"]
        has_progress = any(
            any(s in r.lower() for s in progress_signals)
            for r in recent_tool_results[-3:]
        )
        if not has_progress:
            result["is_stuck"] = True
            result["reason"] = "No progress signals after 8+ turns"
            result["intervention"] = (
                "You've been working for many turns without clear progress. "
                "Summarize what you've found so far and present it to the "
                "user. Ask if they want you to continue or try differently."
            )
            result["escalate_model"] = True

    return result


def verify_output(
    user_message: str,
    response_text: str,
    intent: str,
) -> dict[str, Any]:
    """Heuristic verification that the response addresses the request.

    Zero-cost check (no LLM call) that catches common completeness failures.
    """
    issues: list[str] = []
    user_lower = user_message.lower()
    resp_lower = response_text.lower()

    all_data_signals = [
        "all users", "all customers", "all data", "all records",
        "every user", "every customer", "complete list", "complete report",
        "full report", "full list", "raw data", "entire", "user base",
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
                "email sent", "emailed", "sent.*email",
                "email.*to.*@", "✅.*email", ":white_check_mark:.*email",
            ],
            "summary": [
                "summary", "total", "breakdown", "here's", "overview",
                "results", "report", "findings",
            ],
        }
        for part in requested_parts:
            signals = delivered_signals.get(part, [])
            if signals and not any(
                re.search(s, resp_lower) for s in signals
            ):
                issues.append(
                    f"User requested '{part}' but it appears missing "
                    f"from the response."
                )

    if intent == "data" and len(response_text) < 100:
        issues.append(
            "Data task produced a very short response. "
            "Expected detailed output with counts and deliverables."
        )

    degradation_phrases = [
        "ran into a hiccup",
        "ran into an issue",
        "let me try a different approach",
        "couldn't complete",
        "unable to process",
        "something went wrong",
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

    # Depth check: flag data dumps that lack analysis
    depth = compute_depth_score(user_message, response_text)
    if depth["is_shallow"]:
        missing_str = ", ".join(depth["missing_layers"])
        issues.append(
            f"Response lacks analytical depth (score: {depth['depth_score']}/10). "
            f"Missing: {missing_str}. Every data response needs: "
            f"the data + what it means + what to do about it."
        )

    # High-agency check: detect surrender/dead-end patterns
    low_agency_patterns = [
        "i can't access",
        "i'm unable to",
        "unfortunately i cannot",
        "i don't have access to",
        "i'm not able to",
        "i cannot access",
        "i am unable to",
        "that's outside my",
        "falls outside my",
        "i don't have the ability",
        "i'm afraid i can't",
        "this took longer than expected and i had to stop",
        "the request may have been too complex",
        "want me to try a simpler approach",
        "i wasn't able to complete",
        "i'm having trouble with",
        "something went wrong",
        "i hit a snag",
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
            "Never leave the user with a dead end."
        )
    elif has_low_agency:
        issues.append(
            "Response contains a 'can't do' pattern without offering an alternative. "
            "Always follow 'I can't X' with 'but I can Y' or 'here's what would "
            "make it possible'. No dead ends."
        )

    return {
        "passed": len(issues) == 0,
        "issues": issues,
        "should_retry": len(issues) > 0,
    }
