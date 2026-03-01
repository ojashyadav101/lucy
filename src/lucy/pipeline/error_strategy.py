"""Error strategy engine — high-agency error recovery for Lucy.

Philosophy (highagency.com):
- Every problem is solvable until it defies the laws of physics.
- There's no "I can't" — only "I haven't found the right approach yet."
- Try → analyze → adapt → try different approach → escalate model.
- Deliver what you have. 80% is better than 0%.

This module classifies errors into actionable categories and produces
concrete recovery strategies that change on each attempt. No two
retries use the same approach.

Wired into handlers.py's _run_with_recovery as the strategy brain.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger()


# ═══════════════════════════════════════════════════════════════════════════
# ERROR CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════════════

class ErrorCategory(str, Enum):
    """Error categories that map to distinct recovery strategies."""
    TOOL_ERROR = "tool_error"           # Tool call failed / bad response
    AUTH_ERROR = "auth_error"           # 401/403, token expired, no connection
    RATE_LIMIT = "rate_limit"           # 429, throttled
    TIMEOUT = "timeout"                 # Request timed out
    MODEL_ERROR = "model_error"         # LLM returned garbage, context overflow
    DATA_ERROR = "data_error"           # Bad data, missing resource, 404
    PARSE_ERROR = "parse_error"         # JSON parse, unexpected format
    SERVICE_DOWN = "service_down"       # 502/503/504, service unavailable
    CONTEXT_OVERFLOW = "context_overflow"  # Context too long for model
    UNKNOWN = "unknown"                 # Unclassified


@dataclass
class ErrorClassification:
    """Rich classification of an error with context for strategy selection."""
    category: ErrorCategory
    is_transient: bool          # Will likely resolve on retry?
    is_client_fault: bool       # Bad request vs server issue?
    severity: int               # 1=minor, 2=moderate, 3=severe
    raw_error: str
    status_code: int | None = None
    service_name: str | None = None    # Which service/tool failed?
    suggested_wait: float = 0.0        # Seconds to wait before retry


def classify_error(error: Exception) -> ErrorClassification:
    """Classify an exception into a rich, actionable error type.

    Goes beyond simple string matching — extracts status codes,
    service names, and transience indicators for strategy selection.
    """
    error_str = str(error)
    error_lower = error_str.lower()

    # Extract status code if present
    status_code = _extract_status_code(error_str)

    # Extract service/tool name
    service_name = _extract_service_name(error_str)

    # Rate limit: 429 or explicit rate limit mention
    if status_code == 429 or "rate limit" in error_lower or "throttl" in error_lower:
        wait = _extract_retry_after(error_str)
        return ErrorClassification(
            category=ErrorCategory.RATE_LIMIT,
            is_transient=True,
            is_client_fault=False,
            severity=1,
            raw_error=error_str,
            status_code=429,
            service_name=service_name,
            suggested_wait=wait or 15.0,
        )

    # Auth errors: 401, 403, token expired
    if status_code in (401, 403) or any(
        kw in error_lower for kw in (
            "unauthorized", "forbidden", "token expired",
            "invalid token", "authentication", "not authenticated",
            "permission denied", "access denied",
        )
    ):
        return ErrorClassification(
            category=ErrorCategory.AUTH_ERROR,
            is_transient=False,
            is_client_fault=True,
            severity=3,
            raw_error=error_str,
            status_code=status_code,
            service_name=service_name,
        )

    # Service down: 502, 503, 504 (check BEFORE timeout — 504 is server-side)
    if status_code in (502, 503, 504) or any(
        kw in error_lower for kw in ("service unavailable", "bad gateway", "gateway timeout")
    ):
        return ErrorClassification(
            category=ErrorCategory.SERVICE_DOWN,
            is_transient=True,
            is_client_fault=False,
            severity=2,
            raw_error=error_str,
            status_code=status_code,
            service_name=service_name,
            suggested_wait=5.0,
        )

    # Timeout (client-side — not 502/503/504 which are server-side above)
    if "timeout" in error_lower or "timed out" in error_lower or status_code == 408:
        return ErrorClassification(
            category=ErrorCategory.TIMEOUT,
            is_transient=True,
            is_client_fault=False,
            severity=2,
            raw_error=error_str,
            status_code=status_code,
            service_name=service_name,
            suggested_wait=3.0,
        )

    # Context overflow
    if ("context" in error_lower and ("length" in error_lower or "token" in error_lower)) or \
       "maximum context" in error_lower or "too many tokens" in error_lower:
        return ErrorClassification(
            category=ErrorCategory.CONTEXT_OVERFLOW,
            is_transient=False,
            is_client_fault=True,
            severity=2,
            raw_error=error_str,
            status_code=status_code,
        )

    # Data errors: 404, not found, missing
    if status_code == 404 or any(
        kw in error_lower for kw in (
            "not found", "no such", "does not exist", "missing",
            "no results", "empty response",
        )
    ):
        return ErrorClassification(
            category=ErrorCategory.DATA_ERROR,
            is_transient=False,
            is_client_fault=True,
            severity=2,
            raw_error=error_str,
            status_code=status_code,
            service_name=service_name,
        )

    # Parse errors
    if any(kw in error_lower for kw in (
        "json", "parse", "decode", "unexpected token",
        "invalid format", "malformed", "syntax error",
    )):
        return ErrorClassification(
            category=ErrorCategory.PARSE_ERROR,
            is_transient=False,
            is_client_fault=True,
            severity=2,
            raw_error=error_str,
            status_code=status_code,
        )

    # Model errors: bad LLM output
    if any(kw in error_lower for kw in (
        "model", "completion", "generation", "inference",
        "content filter", "safety", "refusal",
    )):
        return ErrorClassification(
            category=ErrorCategory.MODEL_ERROR,
            is_transient=True,
            is_client_fault=False,
            severity=2,
            raw_error=error_str,
            status_code=status_code,
        )

    # Client errors (400, 405, 422) = tool/request errors
    if status_code in (400, 405, 422):
        return ErrorClassification(
            category=ErrorCategory.TOOL_ERROR,
            is_transient=False,
            is_client_fault=True,
            severity=2,
            raw_error=error_str,
            status_code=status_code,
            service_name=service_name,
        )

    # Generic tool errors
    if any(kw in error_lower for kw in (
        "tool", "action", "function call", "composio",
    )):
        return ErrorClassification(
            category=ErrorCategory.TOOL_ERROR,
            is_transient=True,
            is_client_fault=False,
            severity=2,
            raw_error=error_str,
            status_code=status_code,
            service_name=service_name,
        )

    # Unknown
    return ErrorClassification(
        category=ErrorCategory.UNKNOWN,
        is_transient=True,  # Assume transient, worth retrying
        is_client_fault=False,
        severity=2,
        raw_error=error_str,
        status_code=status_code,
        service_name=service_name,
    )


# ═══════════════════════════════════════════════════════════════════════════
# RECOVERY STRATEGIES
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class RecoveryStrategy:
    """A concrete recovery action to take on the next attempt."""
    name: str                       # Human-readable strategy name
    description: str                # What this strategy does differently
    wait_seconds: float = 0.0       # How long to wait before retry
    model_override: str | None = None   # Escalate to different model?
    failure_context: str = ""       # Injected context for the LLM
    should_simplify: bool = False   # Tell LLM to simplify its approach?
    should_trim_context: bool = False  # Trim message history?
    max_tool_turns: int | None = None  # Override max tool turns?
    skip_tools: list[str] = field(default_factory=list)  # Tools to avoid


# Strategy generators per error category.
# Each returns a LIST of strategies ordered by escalation level.
# _run_with_recovery picks strategy[attempt_number].

def _strategies_for_rate_limit(
    classification: ErrorClassification, attempt: int,
) -> RecoveryStrategy:
    """Exponential backoff with model escalation."""
    strategies = [
        RecoveryStrategy(
            name="rate_limit_backoff_1",
            description="Wait and retry with same approach",
            wait_seconds=classification.suggested_wait,
            failure_context=(
                "Previous attempt hit a rate limit. Wait completed. "
                "Proceed with the same approach — the limit should have reset."
            ),
        ),
        RecoveryStrategy(
            name="rate_limit_backoff_2",
            description="Longer wait, reduce parallel calls",
            wait_seconds=classification.suggested_wait * 2,
            failure_context=(
                "Rate limit persists. Make API calls sequentially (not parallel). "
                "If searching, use fewer but more targeted queries. "
                "Batch operations where possible."
            ),
            max_tool_turns=8,
        ),
        RecoveryStrategy(
            name="rate_limit_simplify",
            description="Simplify approach to reduce API calls",
            wait_seconds=classification.suggested_wait * 3,
            failure_context=(
                "Persistent rate limiting. Drastically reduce API calls: "
                "use cached data if available, make ONE targeted call instead "
                "of multiple, or provide your best answer from what you already know."
            ),
            should_simplify=True,
        ),
        RecoveryStrategy(
            name="rate_limit_model_switch",
            description="Switch to frontier model with minimal calls",
            wait_seconds=30.0,
            model_override="frontier",
            failure_context=(
                "All rate limit recovery attempts failed. You're now on a "
                "more capable model. Answer from your training knowledge if "
                "possible. Only make API calls if absolutely essential."
            ),
            should_simplify=True,
        ),
    ]
    return strategies[min(attempt, len(strategies) - 1)]


def _strategies_for_timeout(
    classification: ErrorClassification, attempt: int,
) -> RecoveryStrategy:
    """Progressive simplification + model escalation."""
    strategies = [
        RecoveryStrategy(
            name="timeout_retry",
            description="Quick retry — timeouts are often transient",
            wait_seconds=3.0,
            failure_context=(
                "Previous attempt timed out. Retry the same approach — "
                "timeouts are usually transient network blips."
            ),
        ),
        RecoveryStrategy(
            name="timeout_simplify",
            description="Break task into smaller chunks",
            wait_seconds=5.0,
            failure_context=(
                "Operation timed out twice. Break this into smaller steps: "
                "fetch less data per call, process in chunks, or use "
                "simpler/faster API endpoints. Avoid bulk operations."
            ),
            should_simplify=True,
        ),
        RecoveryStrategy(
            name="timeout_alt_approach",
            description="Try a completely different approach",
            wait_seconds=5.0,
            failure_context=(
                "Persistent timeouts. The current approach may be too heavy. "
                "Try a fundamentally different strategy: "
                "different tool, different API endpoint, or answer from "
                "available context without the failing operation."
            ),
        ),
        RecoveryStrategy(
            name="timeout_frontier",
            description="Escalate to frontier model for smarter recovery",
            wait_seconds=8.0,
            model_override="frontier",
            failure_context=(
                "Multiple timeouts. You're on a stronger model now. "
                "Devise the most efficient approach: minimal API calls, "
                "direct data access, or synthesize an answer from context. "
                "If the specific operation keeps timing out, skip it and "
                "deliver what you can."
            ),
        ),
    ]
    return strategies[min(attempt, len(strategies) - 1)]


def _strategies_for_tool_error(
    classification: ErrorClassification, attempt: int,
) -> RecoveryStrategy:
    """Try different tools, then manual approach."""
    tool_name = classification.service_name or "the failing tool"
    strategies = [
        RecoveryStrategy(
            name="tool_retry_with_fix",
            description="Retry with corrected parameters",
            wait_seconds=1.0,
            failure_context=(
                f"Tool call to {tool_name} failed. Analyze the error carefully: "
                f"'{classification.raw_error[:200]}'. "
                "Fix the parameters, correct any malformed input, and retry. "
                "Check if the tool name is exactly right."
            ),
        ),
        RecoveryStrategy(
            name="tool_alternative",
            description="Try an alternative tool or approach",
            wait_seconds=2.0,
            failure_context=(
                f"Tool {tool_name} failed again even with corrected params. "
                "Try a DIFFERENT tool that achieves the same goal. "
                "Search tools, list tools, or custom integrations may offer "
                "alternative paths. Think: what other tool could get this data?"
            ),
        ),
        RecoveryStrategy(
            name="tool_manual",
            description="Skip the tool, answer from knowledge",
            wait_seconds=2.0,
            failure_context=(
                f"Tool-based approaches keep failing for {tool_name}. "
                "Answer the user's question using your training knowledge, "
                "context from the conversation, or web search as a fallback. "
                "Deliver value even without the specific tool."
            ),
            should_simplify=True,
        ),
        RecoveryStrategy(
            name="tool_frontier",
            description="Escalate model for creative problem-solving",
            wait_seconds=3.0,
            model_override="frontier",
            failure_context=(
                "Multiple tool failures. You're on a stronger model. "
                "Think creatively: is there a web search, a different API, "
                "or a way to derive the answer without the failing tool? "
                "Deliver the best possible answer."
            ),
        ),
    ]
    return strategies[min(attempt, len(strategies) - 1)]


def _strategies_for_auth_error(
    classification: ErrorClassification, attempt: int,
) -> RecoveryStrategy:
    """Auth errors: check connection, suggest re-auth, work around."""
    service = classification.service_name or "the service"
    strategies = [
        RecoveryStrategy(
            name="auth_check_connection",
            description="Verify the connection is still active",
            wait_seconds=1.0,
            failure_context=(
                f"Auth error with {service}. First check if the connection "
                "is still active using COMPOSIO_CHECK_ACTIVE_CONNECTIONS. "
                "The token may have expired. If connected, the parameters "
                "may be wrong — check them carefully."
            ),
        ),
        RecoveryStrategy(
            name="auth_reconnect",
            description="Guide user to reconnect",
            wait_seconds=1.0,
            failure_context=(
                f"Auth verification shows {service} connection may be stale. "
                "Tell the user their connection needs refreshing and provide "
                "a reconnect link. Be specific about which service and why."
            ),
        ),
        RecoveryStrategy(
            name="auth_workaround",
            description="Answer without the authenticated service",
            wait_seconds=1.0,
            failure_context=(
                f"{service} authentication is failing. Try to answer the "
                "user's question using alternative approaches: web search, "
                "training knowledge, or other connected services. "
                "Let the user know about the auth issue and what you found instead."
            ),
        ),
    ]
    return strategies[min(attempt, len(strategies) - 1)]


def _strategies_for_model_error(
    classification: ErrorClassification, attempt: int,
) -> RecoveryStrategy:
    """Model errors: simplify, then escalate."""
    strategies = [
        RecoveryStrategy(
            name="model_retry",
            description="Simple retry — model errors are often transient",
            wait_seconds=2.0,
            failure_context=(
                "Previous model call failed. Retry with the same approach."
            ),
        ),
        RecoveryStrategy(
            name="model_simplify",
            description="Simplify the request to reduce model load",
            wait_seconds=3.0,
            failure_context=(
                "Model error on retry. Simplify your approach: shorter "
                "responses, fewer tool calls, less complex reasoning chains."
            ),
            should_simplify=True,
            should_trim_context=True,
        ),
        RecoveryStrategy(
            name="model_escalate_fast",
            description="Try the fast tier model",
            wait_seconds=3.0,
            model_override="fast",
            failure_context="Switching to a different model. Same task, fresh start.",
        ),
        RecoveryStrategy(
            name="model_escalate_frontier",
            description="Escalate to frontier model",
            wait_seconds=5.0,
            model_override="frontier",
            failure_context=(
                "Escalated to the most capable model. "
                "Deliver the best possible answer."
            ),
        ),
    ]
    return strategies[min(attempt, len(strategies) - 1)]


def _strategies_for_service_down(
    classification: ErrorClassification, attempt: int,
) -> RecoveryStrategy:
    """Service down: wait, retry, then work around."""
    service = classification.service_name or "the service"
    strategies = [
        RecoveryStrategy(
            name="service_wait_retry",
            description="Wait for service to recover",
            wait_seconds=classification.suggested_wait,
            failure_context=f"{service} was temporarily unavailable. Retrying now.",
        ),
        RecoveryStrategy(
            name="service_longer_wait",
            description="Longer wait for service recovery",
            wait_seconds=classification.suggested_wait * 3,
            failure_context=(
                f"{service} is still down. If possible, try a different "
                "endpoint or API that provides similar data."
            ),
        ),
        RecoveryStrategy(
            name="service_workaround",
            description="Work around the down service",
            wait_seconds=5.0,
            failure_context=(
                f"{service} appears to be experiencing an outage. "
                "Answer the user's question using alternative sources: "
                "web search, cached data, training knowledge, or other "
                "connected services. Acknowledge the outage briefly."
            ),
        ),
        RecoveryStrategy(
            name="service_frontier_workaround",
            description="Frontier model to find creative workaround",
            wait_seconds=5.0,
            model_override="frontier",
            failure_context=(
                f"{service} outage persists. You're on the strongest model. "
                "Find a creative alternative approach."
            ),
        ),
    ]
    return strategies[min(attempt, len(strategies) - 1)]


def _strategies_for_data_error(
    classification: ErrorClassification, attempt: int,
) -> RecoveryStrategy:
    """Data errors: try different source, then synthesize."""
    strategies = [
        RecoveryStrategy(
            name="data_retry_params",
            description="Retry with corrected search parameters",
            wait_seconds=1.0,
            failure_context=(
                "The data wasn't found. The search parameters or identifiers "
                "may be wrong. Try: different search terms, broader filters, "
                "or check if the resource name/ID is spelled correctly."
            ),
        ),
        RecoveryStrategy(
            name="data_alt_source",
            description="Try a different data source",
            wait_seconds=2.0,
            failure_context=(
                "Data still not found via the primary source. Try a different "
                "approach: web search, a related API endpoint, or look for "
                "the information in a different format or location."
            ),
        ),
        RecoveryStrategy(
            name="data_synthesize",
            description="Synthesize answer from available information",
            wait_seconds=1.0,
            failure_context=(
                "The specific data requested isn't available through any "
                "source tried so far. Synthesize the best possible answer "
                "from your training knowledge and conversation context. "
                "Be transparent about what you found vs. what you inferred."
            ),
            should_simplify=True,
        ),
    ]
    return strategies[min(attempt, len(strategies) - 1)]


def _strategies_for_parse_error(
    classification: ErrorClassification, attempt: int,
) -> RecoveryStrategy:
    """Parse errors: simplify the request."""
    strategies = [
        RecoveryStrategy(
            name="parse_retry_simplified",
            description="Retry with simplified request format",
            wait_seconds=1.0,
            failure_context=(
                "Previous response had format/parsing issues. "
                "Simplify: use plain text where possible, avoid complex "
                "nested structures, and validate your output format."
            ),
            should_simplify=True,
        ),
        RecoveryStrategy(
            name="parse_alt_format",
            description="Try a different output format entirely",
            wait_seconds=2.0,
            failure_context=(
                "Format issues persist. Use a completely different approach: "
                "plain text instead of structured data, break complex "
                "responses into simple parts."
            ),
            should_simplify=True,
        ),
        RecoveryStrategy(
            name="parse_frontier",
            description="Frontier model for better format handling",
            wait_seconds=3.0,
            model_override="frontier",
            failure_context="Switched to stronger model. Keep output format simple and clean.",
        ),
    ]
    return strategies[min(attempt, len(strategies) - 1)]


def _strategies_for_context_overflow(
    classification: ErrorClassification, attempt: int,
) -> RecoveryStrategy:
    """Context overflow: trim aggressively, then switch model."""
    strategies = [
        RecoveryStrategy(
            name="context_trim",
            description="Trim context and retry",
            wait_seconds=1.0,
            failure_context=(
                "Context was too long. Working with trimmed history. "
                "Focus on the most recent messages and the core request."
            ),
            should_trim_context=True,
        ),
        RecoveryStrategy(
            name="context_minimal",
            description="Minimal context, just the question",
            wait_seconds=1.0,
            failure_context=(
                "Still too much context. Answer based on the user's "
                "most recent message only. Ignore earlier conversation history."
            ),
            should_trim_context=True,
            should_simplify=True,
        ),
        RecoveryStrategy(
            name="context_frontier",
            description="Frontier model with larger context window",
            wait_seconds=2.0,
            model_override="frontier",
            failure_context="Switched to model with larger context. Proceed normally.",
            should_trim_context=True,
        ),
    ]
    return strategies[min(attempt, len(strategies) - 1)]


def _strategies_for_unknown(
    classification: ErrorClassification, attempt: int,
) -> RecoveryStrategy:
    """Unknown errors: progressive escalation."""
    strategies = [
        RecoveryStrategy(
            name="unknown_retry",
            description="Simple retry for unknown error",
            wait_seconds=3.0,
            failure_context=(
                f"Previous attempt failed with: {classification.raw_error[:200]}. "
                "Analyze this error and try a different approach."
            ),
        ),
        RecoveryStrategy(
            name="unknown_simplify",
            description="Simplify everything",
            wait_seconds=5.0,
            failure_context=(
                "Second failure. Simplify your approach drastically: "
                "fewer tool calls, shorter responses, direct answers."
            ),
            should_simplify=True,
        ),
        RecoveryStrategy(
            name="unknown_alt_approach",
            description="Completely different strategy",
            wait_seconds=5.0,
            failure_context=(
                "Multiple failures. Try a COMPLETELY different strategy. "
                "If you were using tools, try answering from knowledge. "
                "If you were doing complex reasoning, simplify. "
                "Think: 'What would I do with 10x more creativity?'"
            ),
        ),
        RecoveryStrategy(
            name="unknown_frontier",
            description="Frontier model as last resort",
            wait_seconds=8.0,
            model_override="frontier",
            failure_context=(
                "Escalated to the most capable model after multiple failures. "
                "Deliver the best answer you can, even if partial. "
                "Something is better than nothing."
            ),
        ),
    ]
    return strategies[min(attempt, len(strategies) - 1)]


# Strategy dispatch table
_STRATEGY_MAP = {
    ErrorCategory.RATE_LIMIT: _strategies_for_rate_limit,
    ErrorCategory.TIMEOUT: _strategies_for_timeout,
    ErrorCategory.TOOL_ERROR: _strategies_for_tool_error,
    ErrorCategory.AUTH_ERROR: _strategies_for_auth_error,
    ErrorCategory.MODEL_ERROR: _strategies_for_model_error,
    ErrorCategory.SERVICE_DOWN: _strategies_for_service_down,
    ErrorCategory.DATA_ERROR: _strategies_for_data_error,
    ErrorCategory.PARSE_ERROR: _strategies_for_parse_error,
    ErrorCategory.CONTEXT_OVERFLOW: _strategies_for_context_overflow,
    ErrorCategory.UNKNOWN: _strategies_for_unknown,
}


def get_recovery_strategy(
    classification: ErrorClassification,
    attempt: int,
) -> RecoveryStrategy:
    """Get the recovery strategy for a given error and attempt number.

    Each attempt returns a DIFFERENT strategy — never the same approach twice.
    Strategies escalate from simple retry → adapt → different approach → model switch.
    """
    strategy_fn = _STRATEGY_MAP.get(
        classification.category,
        _strategies_for_unknown,
    )
    return strategy_fn(classification, attempt)


def should_give_up(
    classification: ErrorClassification,
    attempt: int,
    max_attempts: int = 5,
) -> bool:
    """Determine if we should stop retrying.

    Rules:
    - Auth errors with no workaround: give up after 3 attempts
    - Client errors (bad request): give up after 2 attempts
    - Everything else: try up to max_attempts
    - NEVER give up on attempt 0 (always try at least once)
    """
    if attempt == 0:
        return False

    # Hard client errors (400, 422) — our request is wrong, retrying won't help
    if classification.is_client_fault and classification.category == ErrorCategory.TOOL_ERROR:
        return attempt >= 3

    # Auth errors — reconnection needed, retrying is futile after a point
    if classification.category == ErrorCategory.AUTH_ERROR:
        return attempt >= 3

    return attempt >= max_attempts


# ═══════════════════════════════════════════════════════════════════════════
# ACTIONABLE DEGRADATION MESSAGES
# ═══════════════════════════════════════════════════════════════════════════

def get_actionable_degradation_message(
    classification: ErrorClassification,
    partial_results: str | None = None,
) -> str:
    """Generate a specific, actionable message when all retries are exhausted.

    Unlike generic "I ran into an issue", these messages:
    1. Tell the user WHAT went wrong (without exposing internals)
    2. Tell them what Lucy DID manage to do (partial results)
    3. Suggest a specific next step
    """
    # If we have partial results, lead with those
    partial_prefix = ""
    if partial_results:
        partial_prefix = (
            "Here's what I managed to get done before running into trouble:\n\n"
            f"{partial_results}\n\n"
        )

    messages = {
        ErrorCategory.RATE_LIMIT: (
            f"{partial_prefix}"
            "I'm being rate limited by one of the services I need. "
            "This usually clears up in a minute or two. "
            "Want me to try again shortly?"
        ),
        ErrorCategory.TIMEOUT: (
            f"{partial_prefix}"
            "The service I was reaching out to is responding slowly. "
            "I tried several approaches to work around it. "
            "Want me to try once more, or would a simpler version of this help?"
        ),
        ErrorCategory.AUTH_ERROR: (
            f"{partial_prefix}"
            f"I'm having trouble authenticating with "
            f"{classification.service_name or 'a connected service'}. "
            "The connection may need to be refreshed. "
            "Try saying *connect [service name]* and I'll set up a fresh link."
        ),
        ErrorCategory.TOOL_ERROR: (
            f"{partial_prefix}"
            "I tried multiple approaches but the tool I need isn't cooperating. "
            "This might be a temporary issue. "
            "Want me to try a different way to get this done?"
        ),
        ErrorCategory.MODEL_ERROR: (
            f"{partial_prefix}"
            "I hit a processing issue on my end. "
            "Let me try that again — these are usually one-off hiccups."
        ),
        ErrorCategory.SERVICE_DOWN: (
            f"{partial_prefix}"
            f"{classification.service_name or 'One of the services'} "
            "appears to be experiencing issues right now. "
            "I can try again in a few minutes, or I can try to answer "
            "from what I already know. What would you prefer?"
        ),
        ErrorCategory.DATA_ERROR: (
            f"{partial_prefix}"
            "I couldn't find the specific data I was looking for. "
            "Could you double-check the name or details? "
            "Sometimes a slightly different search term does the trick."
        ),
        ErrorCategory.PARSE_ERROR: (
            f"{partial_prefix}"
            "I got some data back but it was in an unexpected format. "
            "Let me try a simpler approach — want me to give it another go?"
        ),
        ErrorCategory.CONTEXT_OVERFLOW: (
            f"{partial_prefix}"
            "Our conversation has gotten quite long and I'm losing track. "
            "Could you restate what you need in a fresh message? "
            "I'll be able to focus better."
        ),
        ErrorCategory.UNKNOWN: (
            f"{partial_prefix}"
            "I ran into an unexpected issue after trying several approaches. "
            "Want me to take another crack at it? "
            "Sometimes starting fresh helps."
        ),
    }

    return messages.get(classification.category, messages[ErrorCategory.UNKNOWN])


# ═══════════════════════════════════════════════════════════════════════════
# HIGH AGENCY CHECK
# ═══════════════════════════════════════════════════════════════════════════

def high_agency_check(
    error: Exception,
    attempts_so_far: int,
    user_message: str,
) -> str:
    """The "What would I try with 10x agency?" check.

    Called before giving up. Generates a creative last-resort prompt
    that pushes the LLM to think outside the box.

    Inspired by highagency.com: "Does this problem defy the laws of physics?
    No? Then there's a way."
    """
    return (
        "<high_agency_final_attempt>\n"
        f"You've failed {attempts_so_far} times on this task. "
        f"Error: {str(error)[:300]}\n\n"
        "STOP. Before you give up, apply the high-agency test:\n"
        "1. Does this problem defy the laws of physics? (No.)\n"
        "2. What would someone with 10x more creativity try?\n"
        "3. Is there a COMPLETELY different way to deliver value here?\n\n"
        "Options to consider:\n"
        "- Answer from your training knowledge (you know a LOT)\n"
        "- Use web search instead of the failing tool\n"
        "- Break the problem into tiny pieces and solve what you can\n"
        "- Deliver 80% of the answer and be upfront about the missing 20%\n"
        "- Ask the user a clarifying question that unlocks a simpler path\n\n"
        "The user asked: " + user_message[:500] + "\n"
        "Deliver SOMETHING valuable. Zero output is never acceptable.\n"
        "</high_agency_final_attempt>"
    )


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════

_STATUS_CODE_RE = re.compile(r"\b(\d{3})\b")

def _extract_status_code(error_str: str) -> int | None:
    """Extract HTTP status code from error string."""
    # Look for "status XXX" or "(XXX)" patterns first
    explicit = re.search(r"(?:status[_ ]?(?:code)?[: ]*|^\(?)(\d{3})\b", error_str)
    if explicit:
        code = int(explicit.group(1))
        if 100 <= code <= 599:
            return code

    # Fall back to any 3-digit number that looks like a status code
    for match in _STATUS_CODE_RE.finditer(error_str):
        code = int(match.group(1))
        if 400 <= code <= 599:
            return code

    return None


def _extract_service_name(error_str: str) -> str | None:
    """Extract the service/tool name from an error string."""
    # Common patterns: "COMPOSIO_GMAIL_...", "clerk_...", "google_calendar_..."
    tool_match = re.search(
        r"(?:COMPOSIO_)?([A-Z][A-Z_]+?)(?:_[A-Z]+){0,3}",
        error_str,
    )
    if tool_match:
        name = tool_match.group(1).replace("_", " ").title()
        if len(name) > 3 and name not in ("Error", "Status", "Code", "Http"):
            return name

    # Try lowercase patterns
    service_match = re.search(
        r"(?:from|to|with|for) (\w+(?:[_-]\w+)?)",
        error_str.lower(),
    )
    if service_match:
        name = service_match.group(1).replace("_", " ").title()
        if len(name) > 3:
            return name

    return None


def _extract_retry_after(error_str: str) -> float | None:
    """Extract retry-after seconds from error string."""
    match = re.search(r"retry[- ]?after[: ]*(\d+)", error_str, re.IGNORECASE)
    if match:
        return float(match.group(1))
    return None
