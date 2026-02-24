"""Fast path for simple messages that don't need the full agent loop.

The problem:
    "Hi Lucy!" takes 25.7s (P50 from load test R10) because it goes through:
    workspace setup → tool fetch → system prompt build → LLM call → output pipeline

    That's insane for a greeting. The response should be <500ms.

The fix:
    A lightweight classifier that intercepts simple messages BEFORE the
    agent loop and returns a response from LLM-generated message pools
    (pre-warmed at startup via humanize.py).

What qualifies for fast path:
    1. Pure greetings: "hi", "hello", "hey"
    2. Status checks: "are you there?", "you up?"
    3. Simple acknowledgments that need a reply (not react-only):
       "what can you do?", "help"

What does NOT qualify:
    - Anything with tool keywords ("check my calendar")
    - Anything asking about specific data ("what's our MRR?")
    - Anything in a thread (needs conversation context)
    - Anything longer than 50 characters
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import structlog

from lucy.core.humanize import pick

logger = structlog.get_logger()


@dataclass
class FastPathResult:
    """Result of fast path evaluation."""
    is_fast: bool
    response: str | None
    reason: str = ""


# ═══════════════════════════════════════════════════════════════════════════
# FAST PATH PATTERNS
# ═══════════════════════════════════════════════════════════════════════════

_GREETING_RE = re.compile(
    r"^(?:hi|hey|hello|yo|hiya|sup|what'?s up|howdy|good (?:morning|afternoon|evening))"
    r"(?:\s+(?:lucy|there|everyone|team))?"
    r"[!.\s]*$",
    re.IGNORECASE,
)

_STATUS_RE = re.compile(
    r"^(?:are you (?:there|online|up|available|awake)\??|"
    r"you (?:there|up|online|around)\??|"
    r"ping|status|alive\??)"
    r"[!.\s]*$",
    re.IGNORECASE,
)

_HELP_RE = re.compile(
    r"^(?:help|what can you do\??|what do you do\??|"
    r"how do you work\??|what are you\??|who are you\??)"
    r"[!.\s]*$",
    re.IGNORECASE,
)


# ═══════════════════════════════════════════════════════════════════════════
# FAST PATH EVALUATION
# ═══════════════════════════════════════════════════════════════════════════

def evaluate_fast_path(
    message: str,
    thread_depth: int = 0,
    has_thread_context: bool = False,
) -> FastPathResult:
    """Evaluate whether a message can be handled without the full agent loop.

    Responses come from LLM-generated pools (pre-warmed at startup).
    If pools aren't ready yet, falls back to sensible defaults.
    """
    text = message.strip()

    if has_thread_context and thread_depth > 0:
        return FastPathResult(is_fast=False, response=None, reason="in_thread")

    if len(text) > 60:
        return FastPathResult(is_fast=False, response=None, reason="too_long")

    if _GREETING_RE.match(text):
        response = pick("greeting")
        logger.info("fast_path_match", pattern="greeting", message=text[:50])
        return FastPathResult(is_fast=True, response=response, reason="greeting")

    if _STATUS_RE.match(text):
        response = pick("status")
        logger.info("fast_path_match", pattern="status", message=text[:50])
        return FastPathResult(is_fast=True, response=response, reason="status")

    if _HELP_RE.match(text):
        response = pick("help")
        logger.info("fast_path_match", pattern="help", message=text[:50])
        return FastPathResult(is_fast=True, response=response, reason="help")

    return FastPathResult(is_fast=False, response=None, reason="no_match")
