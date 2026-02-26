"""Fast rule-based model router for Lucy.

Classifies user messages by intent and selects the most cost-effective
model.  The classification runs in pure Python with no LLM call, so it
adds <1 ms of latency.

Model tiers (configurable in config.py):
    fast     — cheap, low-latency (greetings, short follow-ups, lookups)
    default  — balanced (tool-calling, general tasks)
    code     — optimized for code generation and debugging
    research — 1M context, deep investigation
    document — reasoning depth for client-facing docs
    frontier — sparingly, deep analysis
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lucy.config import settings

MODEL_TIERS: dict[str, str] = {
    "fast": settings.model_tier_fast,
    "default": settings.model_tier_default,
    "code": settings.model_tier_code,
    "research": settings.model_tier_research,
    "document": settings.model_tier_document,
    "frontier": settings.model_tier_frontier,
}

_CODE_KEYWORDS = re.compile(
    r"\b(code|deploy|script|function|debug|refactor|implement|"
    r"write a? ?program|create a? ?app|lambda|api endpoint|pull request|"
    r"regex|algorithm|class|module|package|dockerfile|ci/cd|pipeline)\b",
    re.IGNORECASE,
)

_RESEARCH_LIGHT = re.compile(
    r"\b(research|analyze|compare|strategy|competitor|"
    r"market|pricing model|evaluation|"
    r"investigate|audit|benchmark|"
    r"tell me about|summarize|overview|what do you know)\b",
    re.IGNORECASE,
)

_RESEARCH_HEAVY = re.compile(
    r"\b(deep dive|deep analysis|comprehensive|thorough|investigate|audit|"
    r"benchmark|detailed analysis|competitive analysis|full report|"
    r"in[- ]depth|exhaustive|complete analysis)\b",
    re.IGNORECASE,
)

_GREETING_PATTERNS = re.compile(
    r"^(hi|hey|hello|yo|sup|thanks|thank you|ok|okay|got it|"
    r"sounds good|perfect|great|cool|nice|yes|no|yep|nope|sure)"
    r"(\s+(there|lucy|everyone|all|team))*\s*[!.?]*$",
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

_DATA_SOURCE_KEYWORDS = re.compile(
    r"\b(calendar|email|emails|gmail|inbox|unread|schedule|meeting|meetings|"
    r"slack|github|issues?|pull requests?|commits?|notion|sheets?|"
    r"spreadsheet|jira|linear|trello|drive|news|latest|"
    r"integrations?|connected|connections?)\b",
    re.IGNORECASE,
)

_DOCUMENT_KEYWORDS = re.compile(
    r"\b(pdf|report|document|spreadsheet|excel|csv|"
    r"create a (?:report|pdf|document|spreadsheet))\b",
    re.IGNORECASE,
)

_DATA_PROCESSING_KEYWORDS = re.compile(
    r"\b(export|all users|all customers|all records|merge|de-?duplicate|"
    r"bulk|master list|data extract|pull (?:all|every)|"
    r"cross[- ]reference|import data|full list|complete list|"
    r"generate (?:a )?(?:report|spreadsheet|excel)|"
    r"fetch (?:all|every)|3[,.]?0\d\d|hundreds of|thousands of)\b",
    re.IGNORECASE,
)

# Dynamic prompt modules loaded AFTER the static prefix (tool_use + memory
# are already in the static prefix for all non-chat intents). Only truly
# intent-specific modules are listed here.
INTENT_MODULES: dict[str, list[str]] = {
    "chat": [],
    "lookup": [],
    "confirmation": [],
    "followup": [],
    "tool_use": [],
    "command": ["integrations"],
    "code": ["coding"],
    "reasoning": ["research"],
    "document": [],
}


@dataclass
class ModelChoice:
    intent: str
    model: str
    tier: str
    prompt_modules: list[str] = field(default_factory=list)


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

    def _choice(intent: str, tier: str) -> ModelChoice:
        return ModelChoice(
            intent=intent,
            model=MODEL_TIERS[tier],
            tier=tier,
            prompt_modules=INTENT_MODULES.get(intent, []),
        )

    # 1. Pure greetings/acknowledgments
    if _GREETING_PATTERNS.match(text):
        if prev_had_tool_calls:
            return _choice("confirmation", "default")
        return _choice("chat", "fast")

    # 2. Short messages deep in threads
    if thread_depth > 5 and len(text) < 50:
        if prev_had_tool_calls:
            return _choice("followup", "default")
        if _ACTION_VERBS.search(text):
            return _choice("command", "default")
        return _choice("followup", "fast")

    # 3a. Data processing / bulk tasks — check BEFORE document so
    #     "merge data into Excel" routes to code, not document.
    #     Use default tier (not code) — data tasks need multi-step
    #     reasoning that cheap code models struggle with.
    if _DATA_PROCESSING_KEYWORDS.search(text):
        return _choice("code", "default")

    # 3. Document creation — check BEFORE research so "create a report
    #    about competitors" routes to document, not research.
    if _DOCUMENT_KEYWORDS.search(text) and _ACTION_VERBS.search(text):
        return _choice("document", "document")

    # 4. Deep research / analysis — check before code to avoid
    #    "research code tools" being classified as coding.
    has_heavy = bool(_RESEARCH_HEAVY.search(text))
    light_matches = _RESEARCH_LIGHT.findall(text)
    if has_heavy or len(light_matches) >= 3:
        return _choice("reasoning", "research")
    if len(light_matches) >= 2 and len(text) > 50:
        return _choice("reasoning", "research")
    if light_matches and len(text) > 40:
        return _choice("tool_use", "default")

    # 5. Coding tasks (removed "build" — "build me a report" is not code)
    has_code = _CODE_KEYWORDS.search(text)
    if has_code:
        if _CHECK_PATTERNS.search(text) and len(text) < 80:
            return _choice("tool_use", "default")
        return _choice("code", "code")

    # 6. Messages referencing external data sources always need tools
    if _DATA_SOURCE_KEYWORDS.search(text):
        return _choice("tool_use", "default")

    # 7. Short check/verify requests — need tool calls, not fast tier
    if len(text) < 60 and _CHECK_PATTERNS.search(text):
        return _choice("tool_use", "default")

    # 8. Simple lookups — truly simple questions with no data dependency
    if len(text) < 40 and _SIMPLE_QUESTION.match(text):
        if not _CHECK_PATTERNS.search(text) and not _DATA_SOURCE_KEYWORDS.search(text):
            return _choice("lookup", "fast")

    # 9. Default — tool-calling, general tasks
    return _choice("tool_use", "default")
