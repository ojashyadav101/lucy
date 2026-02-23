"""Fast rule-based model router for Lucy.

Classifies user messages by intent and selects the most cost-effective
model.  The classification runs in pure Python with no LLM call, so it
adds <1 ms of latency.

Model tiers (configurable in config.py):
    fast     — cheap, low-latency (greetings, short follow-ups, lookups)
    default  — balanced (tool-calling, general tasks)
    code     — optimized for code generation and debugging
    frontier — deep reasoning, research, analysis
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from lucy.config import settings

MODEL_TIERS: dict[str, str] = {
    "fast": getattr(settings, "model_tier_fast", "google/gemini-2.5-flash"),
    "default": getattr(settings, "model_tier_default", settings.openclaw_model),
    "code": getattr(settings, "model_tier_code", "deepseek/deepseek-v3-0324"),
    "frontier": getattr(settings, "model_tier_frontier", "anthropic/claude-sonnet-4"),
}

_CODE_KEYWORDS = re.compile(
    r"\b(code|deploy|script|function|debug|refactor|implement|"
    r"write a? ?program|create a? ?app|lambda|api endpoint|pull request|"
    r"regex|algorithm|class|module|package|dockerfile|ci/cd|pipeline)\b",
    re.IGNORECASE,
)

_RESEARCH_KEYWORDS = re.compile(
    r"\b(research|analyze|compare|strategy|report|deep dive|"
    r"competitor|market|pricing model|comprehensive|thorough|"
    r"investigate|audit|benchmark|evaluation)\b",
    re.IGNORECASE,
)

_GREETING_PATTERNS = re.compile(
    r"^(hi|hey|hello|yo|sup|thanks|thank you|ok|okay|got it|"
    r"sounds good|perfect|great|cool|nice|yes|no|yep|nope|sure)\s*[!.?]?$",
    re.IGNORECASE,
)

_SIMPLE_QUESTION = re.compile(
    r"^(what|when|where|who|how|is|are|do|does|can|will)\b.{0,60}\??\s*$",
    re.IGNORECASE,
)

_ACTION_VERBS = re.compile(
    r"\b(do|send|run|execute|delete|cancel|merge|deploy|schedule|create|update|remove)\b",
    re.IGNORECASE,
)

_CHECK_PATTERNS = re.compile(
    r"\b(check|verify|look|find|search|pull|get|fetch|show|list)\b",
    re.IGNORECASE,
)


@dataclass
class ModelChoice:
    intent: str
    model: str
    tier: str


def classify_and_route(
    message: str,
    thread_depth: int = 0,
    prev_had_tool_calls: bool = False,
) -> ModelChoice:
    """Classify message intent and select the best model.

    Runs in <1ms — no LLM calls, pure regex + heuristics.

    Args:
        message: The user's message text
        thread_depth: How deep in a thread this message is
        prev_had_tool_calls: Whether the previous assistant message in
            this thread contained tool calls / active work indicators.
    """
    text = message.strip()

    # 1. Pure greetings/acknowledgments
    if _GREETING_PATTERNS.match(text):
        if prev_had_tool_calls:
            return ModelChoice(
                intent="confirmation",
                model=MODEL_TIERS["default"],
                tier="default",
            )
        return ModelChoice(
            intent="chat",
            model=MODEL_TIERS["fast"],
            tier="fast",
        )

    # 2. Short messages deep in threads
    if thread_depth > 5 and len(text) < 50:
        if prev_had_tool_calls:
            return ModelChoice(
                intent="followup",
                model=MODEL_TIERS["default"],
                tier="default",
            )
        if _ACTION_VERBS.search(text):
            return ModelChoice(
                intent="command",
                model=MODEL_TIERS["default"],
                tier="default",
            )
        return ModelChoice(
            intent="followup",
            model=MODEL_TIERS["fast"],
            tier="fast",
        )

    # 3. Coding tasks (removed "build" — "build me a report" is not code)
    has_code = _CODE_KEYWORDS.search(text)
    if has_code:
        if _CHECK_PATTERNS.search(text) and len(text) < 80:
            return ModelChoice(
                intent="tool_use",
                model=MODEL_TIERS["default"],
                tier="default",
            )
        if _RESEARCH_KEYWORDS.search(text) and len(text) > 80:
            return ModelChoice(
                intent="code_reasoning",
                model=MODEL_TIERS["frontier"],
                tier="frontier",
            )
        return ModelChoice(
            intent="code",
            model=MODEL_TIERS["code"],
            tier="code",
        )

    # 4. Deep research / analysis (multiple signals = lower threshold)
    research_matches = _RESEARCH_KEYWORDS.findall(text)
    if research_matches and (len(text) > 60 or len(research_matches) >= 2):
        return ModelChoice(
            intent="reasoning",
            model=MODEL_TIERS["frontier"],
            tier="frontier",
        )

    # 5. Short check/verify requests — need tool calls, not fast tier
    if len(text) < 60 and _CHECK_PATTERNS.search(text):
        return ModelChoice(
            intent="tool_use",
            model=MODEL_TIERS["default"],
            tier="default",
        )

    # 6. Simple lookups — truly simple questions only
    if len(text) < 40 and _SIMPLE_QUESTION.match(text):
        if not _CHECK_PATTERNS.search(text):
            return ModelChoice(
                intent="lookup",
                model=MODEL_TIERS["fast"],
                tier="fast",
            )

    # 7. Default — tool-calling, general tasks
    return ModelChoice(
        intent="tool_use",
        model=MODEL_TIERS["default"],
        tier="default",
    )
