"""Contextual emoji reaction system for Lucy.

Instead of always using hourglass, Lucy selects contextually appropriate
emoji reactions based on the message content and intent. She also decides
when to REACT (no reply needed) vs REPLY.

Viktor's approach:
- Emoji selection is done by the platform, not the LLM
- A lightweight keyword classifier selects the emoji
- Certain message types get ONLY a reaction (no reply):
  acknowledgments, thanks, simple confirmations
- Working indicators use hourglass during processing, removed after
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import structlog

logger = structlog.get_logger()


@dataclass
class ReactionDecision:
    """Result of analyzing a message for reaction."""
    emoji: str
    react_only: bool
    should_react: bool


_REACTION_RULES: list[tuple[re.Pattern[str], str, bool]] = [
    # (pattern, emoji_name, react_only)

    # ── React-only patterns (no reply needed) ─────────────────────────
    (re.compile(
        r"^(?:thanks?(?:\s+(?:a lot|so much|very much|a ton|for|viktor|lucy))?|"
        r"ty(?:\s|!|$)|thx|cheers|appreciate it|much appreciated|"
        r"thank you(?:\s+(?:so much|very much|a lot|for))?)"
        r"[!.\s]*$",
        re.IGNORECASE,
    ), "saluting_face", True),

    (re.compile(
        r"^(?:got it|noted|understood|makes sense|perfect|"
        r"sounds good|sounds great|cool|nice|great|awesome|"
        r"amazing|love it|beautiful|sweet|wonderful|brilliant|"
        r"exactly|precisely|right on|spot on|nailed it)"
        r"[!.\s]*$",
        re.IGNORECASE,
    ), "white_check_mark", True),

    (re.compile(
        r"^(?:(?:looks? )?good(?:\s+to (?:me|go))?|approved?|"
        r"lgtm|ship it|go (?:ahead|for it)|yes(?:\s+please)?|"
        r"yep|yup|sure|absolutely|definitely)"
        r"[!.\s]*$",
        re.IGNORECASE,
    ), "thumbsup", True),

    # ── React + reply patterns ────────────────────────────────────────
    (re.compile(
        r"\b(?:urgent|asap|immediately|critical|emergency|"
        r"right now|time.?sensitive|drop everything|p0|sev.?1)\b",
        re.IGNORECASE,
    ), "zap", False),

    (re.compile(
        r"\b(?:bug|broken|crash(?:ing|ed)?|error|fail(?:ing|ed|ure)?|"
        r"down|outage|not working|500|404)\b",
        re.IGNORECASE,
    ), "mag", False),

    (re.compile(
        r"\b(?:can you (?:check|find|look)|"
        r"what(?:'s| is) (?:the|our|my)|how (?:do|does|can)|"
        r"where (?:is|are|can)|investigate|look into|dig into)\b",
        re.IGNORECASE,
    ), "eyes", False),

    (re.compile(
        r"\b(?:create|build|make|generate|draft|prepare|"
        r"write|set up|configure|schedule)\b",
        re.IGNORECASE,
    ), "hammer_and_wrench", False),

    (re.compile(
        r"\b(?:analyze|research|compare|benchmark|audit|"
        r"report|deep dive|competitor|market)\b",
        re.IGNORECASE,
    ), "bar_chart", False),

    (re.compile(
        r"\b(?:deploy|ship|release|push|launch|go live|publish)\b",
        re.IGNORECASE,
    ), "rocket", False),

    (re.compile(
        r"\b(?:fyi|heads up|just (?:so you know|letting you know)|"
        r"for (?:your )?(?:info|reference|context))\b",
        re.IGNORECASE,
    ), "memo", True),
]

_DEFAULT_REACTION = ReactionDecision(
    emoji="eyes",
    react_only=False,
    should_react=True,
)


def classify_reaction(message: str) -> ReactionDecision:
    """Classify a message and select the appropriate emoji reaction.

    Runs in <1ms — pure regex, no LLM call.
    """
    text = message.strip()
    word_count = len(text.split())

    for pattern, emoji, react_only in _REACTION_RULES:
        if pattern.search(text):
            if react_only and word_count > 8:
                return ReactionDecision(
                    emoji=emoji,
                    react_only=False,
                    should_react=True,
                )
            return ReactionDecision(
                emoji=emoji,
                react_only=react_only,
                should_react=True,
            )

    return _DEFAULT_REACTION


def get_working_emoji(message: str) -> str:
    """Select the working indicator emoji based on message content."""
    text = message.lower()

    if any(kw in text for kw in ["research", "analyze", "compare", "find", "check", "look"]):
        return "mag"
    if any(kw in text for kw in ["create", "build", "make", "generate", "write", "draft"]):
        return "hammer_and_wrench"
    if any(kw in text for kw in ["deploy", "ship", "release", "push", "launch"]):
        return "rocket"

    return "hourglass_flowing_sand"
