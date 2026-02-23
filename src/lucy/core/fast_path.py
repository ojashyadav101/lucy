"""Fast path for simple messages that don't need the full agent loop.

The problem:
    "Hi Lucy!" takes 25.7s (P50 from load test R10) because it goes through:
    workspace setup → tool fetch → system prompt build → LLM call → output pipeline

    That's insane for a greeting. The response should be <500ms.

The fix:
    A lightweight classifier that intercepts simple messages BEFORE the
    agent loop and returns a canned (but varied) response directly.

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

import random
import re
from dataclasses import dataclass

import structlog

logger = structlog.get_logger()


@dataclass
class FastPathResult:
    """Result of fast path evaluation."""
    is_fast: bool              # True = skip agent loop entirely
    response: str | None       # Pre-computed response (if is_fast)
    reason: str = ""           # Why this was fast-pathed (for logging)


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
# RESPONSE POOLS (varied to avoid robotic repetition)
# ═══════════════════════════════════════════════════════════════════════════

_GREETING_RESPONSES = [
    "Hey! What can I help with?",
    "Hi there — what are you working on?",
    "Hey! What do you need?",
    "Hi! Ready when you are.",
    "Hey — what's on your plate?",
]

_STATUS_RESPONSES = [
    "I'm here — what do you need?",
    "Online and ready. What's up?",
    "Yep, I'm around! What can I help with?",
    "Here and ready to go.",
]

_HELP_RESPONSES = [
    (
        "I can help with a lot — here's a quick rundown:\n\n"
        "• *Search & research* — web, competitors, market data\n"
        "• *Integrations* — Google Calendar, Gmail, GitHub, Linear, Sheets\n"
        "• *Documents* — create PDFs, spreadsheets, presentations\n"
        "• *Code* — review PRs, debug, write scripts, deploy\n"
        "• *Automate* — set up recurring tasks, workflows, alerts\n\n"
        "Just tell me what you need and I'll figure out the best way to do it."
    ),
]


# ═══════════════════════════════════════════════════════════════════════════
# FAST PATH EVALUATION
# ═══════════════════════════════════════════════════════════════════════════

def evaluate_fast_path(
    message: str,
    thread_depth: int = 0,
    has_thread_context: bool = False,
) -> FastPathResult:
    """Evaluate whether a message can be handled without the full agent loop.

    Args:
        message: The user's message text
        thread_depth: How deep in a thread (0 = top-level)
        has_thread_context: Whether this is a reply in an existing thread

    Returns:
        FastPathResult with is_fast=True if we can skip the agent loop.
    """
    text = message.strip()

    # Never fast-path in threads (needs conversation context)
    if has_thread_context and thread_depth > 0:
        return FastPathResult(is_fast=False, response=None, reason="in_thread")

    # Never fast-path long messages
    if len(text) > 60:
        return FastPathResult(is_fast=False, response=None, reason="too_long")

    # Check greeting
    if _GREETING_RE.match(text):
        response = random.choice(_GREETING_RESPONSES)
        logger.info("fast_path_match", pattern="greeting", message=text[:50])
        return FastPathResult(
            is_fast=True,
            response=response,
            reason="greeting",
        )

    # Check status
    if _STATUS_RE.match(text):
        response = random.choice(_STATUS_RESPONSES)
        logger.info("fast_path_match", pattern="status", message=text[:50])
        return FastPathResult(
            is_fast=True,
            response=response,
            reason="status",
        )

    # Check help
    if _HELP_RE.match(text):
        response = random.choice(_HELP_RESPONSES)
        logger.info("fast_path_match", pattern="help", message=text[:50])
        return FastPathResult(
            is_fast=True,
            response=response,
            reason="help",
        )

    return FastPathResult(is_fast=False, response=None, reason="no_match")
