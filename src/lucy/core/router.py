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
    r"\b(code|build|deploy|script|function|debug|refactor|implement|"
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


@dataclass
class ModelChoice:
    intent: str
    model: str
    tier: str


def classify_and_route(message: str, thread_depth: int = 0) -> ModelChoice:
    """Classify message intent and select the best model.

    Runs in <1ms — no LLM calls, pure regex + heuristics.
    """
    text = message.strip()

    # 1. Greetings, confirmations, very short messages
    if _GREETING_PATTERNS.match(text):
        return ModelChoice(
            intent="chat",
            model=MODEL_TIERS["fast"],
            tier="fast",
        )

    # 2. Short follow-ups deep in a thread
    if thread_depth > 5 and len(text) < 50:
        return ModelChoice(
            intent="followup",
            model=MODEL_TIERS["fast"],
            tier="fast",
        )

    # 3. Coding tasks
    if _CODE_KEYWORDS.search(text):
        return ModelChoice(
            intent="code",
            model=MODEL_TIERS["code"],
            tier="code",
        )

    # 4. Deep research / analysis (longer message + research keywords)
    if _RESEARCH_KEYWORDS.search(text) and len(text) > 60:
        return ModelChoice(
            intent="reasoning",
            model=MODEL_TIERS["frontier"],
            tier="frontier",
        )

    # 5. Simple lookups — short questions with no action verb
    if len(text) < 40 and _SIMPLE_QUESTION.match(text):
        return ModelChoice(
            intent="lookup",
            model=MODEL_TIERS["fast"],
            tier="fast",
        )

    # 6. Default — tool-calling, general tasks
    return ModelChoice(
        intent="tool_use",
        model=MODEL_TIERS["default"],
        tier="default",
    )
