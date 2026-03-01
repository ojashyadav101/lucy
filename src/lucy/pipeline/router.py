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

# Advisory / planning questions — "help me plan the tech stack",
# "review this architecture". These are educational/advisory, not research.
_ADVISORY_INTENT = re.compile(
    r"(?:"
    r"help\s+me\s+(?:plan|create|design|choose|decide|pick|figure\s+out|think\s+(?:about|through))"
    r"|review\s+(?:this|my|the|our|a)\s+(?:architect|design|stack|code|setup|approach|system|plan|infra)"
    r"|(?:suggest|recommend)\s+(?:a\s+)?(?:tech\s+)?(?:stack|approach|architecture|strategy|plan|framework)"
    r"|I'm\s+(?:building|creating|starting|launching)\s+.{5,40}\s+(?:help|what|how|which|should)"
    r")",
    re.IGNORECASE,
)

_RESEARCH_HEAVY = re.compile(
    r"\b(deep dive|deep analysis|comprehensive|thorough|investigate|audit|"
    r"benchmark|detailed analysis|competitive analysis|full report|"
    r"in[- ]depth|exhaustive|complete analysis)\b",
    re.IGNORECASE,
)

_GREETING_PATTERNS = re.compile(
    r"^(hi|hey|hello|yo|sup|hiya|howdy|thanks|thank you|thx|ty|"
    r"ok|okay|got it|sounds good|perfect|great|cool|nice|"
    r"yes|no|yep|nope|sure|good morning|good afternoon|good evening|"
    r"morning|evening|gm|how are you|how's it going|what's up)"
    r"(\s+(there|lucy|everyone|all|team|buddy|mate|friend))*"
    r"[!.,?\s]*$",
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

_MONITORING_KEYWORDS = re.compile(
    r"(?:"
    r"(?:inform|alert|notify|tell|ping|warn)\s+me\s+(?:when|if|as\s+soon\s+as|whenever)"
    r"|(?:keep|start)\s+(?:monitoring|tracking|watching|checking)"
    r"|long[- ]running\s+task"
    r"|(?:monitor|watch|track)\s+(?:for\s+)?(?:changes?|drops?|spikes?|issues?|errors?|performance)"
    r"|as\s+soon\s+as\s+(?:something|anything|it|there)"
    r"|real[- ]?time\s+(?:alert|monitor|notification|tracking)"
    r"|heartbeat\s+(?:for|on|to|check|monitor)"
    r"|(?:set\s+up|create|configure|build)\s+(?:a\s+|an\s+)?(?:monitor|alert|watch|heartbeat|notification)"
    r"|continuously\s+(?:monitor|check|track|watch)"
    r"|(?:daily|weekly|hourly|every\s+\d+\s+(?:min|hour|day))\s+(?:report|check|update|summary)"
    r"|(?:goes?\s+live|back\s+in\s+stock|becomes?\s+available)"
    r"|(?:drops?\s+below|goes?\s+(?:above|over|under))"
    r")",
    re.IGNORECASE,
)

_CHECK_PATTERNS = re.compile(
    r"\b(check|verify|look|find|search|pull|get|fetch|show|list|"
    r"how many|how much|count|total|number of)\b",
    re.IGNORECASE,
)

_DATA_SOURCE_KEYWORDS = re.compile(
    r"\b(calendar|email|emails|gmail|inbox|unread|schedule|meeting|meetings|"
    r"slack|github|issues?|pull requests?|commits?|notion|sheets?|"
    r"spreadsheet|jira|linear|trello|drive|news|latest|"
    r"integrations?|connected|connections?|"
    # Connected services and their data entities
    r"clerk|polar|polarsh|polar\.sh|"
    r"users?|customers?|subscribers?|subscriptions?|"
    r"orders?|products?|invoices?|payments?|"
    r"organizations?|sessions?|webhooks?|benefits?|discounts?|"
    r"analytics|metrics|stats|statistics|signups?|"
    # Web data operations
    r"website|web\s*search|google|bing|scrape|crawl)\b",
    re.IGNORECASE,
)

# Composition tasks: user wants content WRITTEN, not data fetched.
# "write me a product update" mentions "product" and "update" but
# shouldn't be routed as a data task. Must check BEFORE _DATA_SOURCE_KEYWORDS.
_COMPOSITION_INTENT = re.compile(
    r"^\s*(?:write|draft|compose|summarize|rewrite|edit|proofread)\s+"
    r"(?:me\s+|us\s+)?(?:a\s+|an\s+|the\s+|some\s+)?"
    r"(?:\w+\s+){0,3}"  # flexible: up to 3 words before noun (e.g. "cold outreach")
    r"(?:update|announcement|email|message|memo|"
    r"report|summary|brief|newsletter|post|blog|note|copy|text|"
    r"description|blurb|paragraph|response|reply|answer|"
    r"letter|proposal|pitch|tweet|thread|caption|tagline|headline)",
    re.IGNORECASE,
)

# Broad composition intent: catches "help me write", "can you draft",
# "I need a post about", etc. These are writing tasks, NOT data fetches.
# Route to full pipeline (not lightweight) for proper token limits.
_COMPOSITION_BROAD = re.compile(
    r"(?:help\s+me\s+|can\s+you\s+|could\s+you\s+|please\s+|I\s+need\s+(?:you\s+to\s+)?)"
    r"(?:write|draft|compose|create|come up with|put together|craft)\s+"
    r"(?:me\s+|us\s+)?(?:a\s+|an\s+|the\s+|some\s+)?"
    r"(?:short\s+|brief\s+|quick\s+|long\s+|detailed\s+|professional\s+)?"
    r"(?:linkedin\s+|twitter\s+|blog\s+|marketing\s+|sales\s+|product\s+)?"
    r"(?:post|email|message|announcement|update|newsletter|blog|article|"
    r"copy|pitch|proposal|brief|memo|letter|tweet|thread|caption|"
    r"tagline|headline|bio|intro|description|outline|summary|script|"
    r"press\s+release|job\s+(?:description|posting)|content)",
    re.IGNORECASE,
)

# Knowledge/educational questions: user wants explanation, not data fetch.
# Route to full model path so they get thorough answers with the complete
# system prompt, NOT the lightweight 500-token path.
_KNOWLEDGE_INTENT = re.compile(
    r"(?:"
    # Explicit educational patterns
    r"walk\s+me\s+through"
    r"|explain\s+(?:how|what|why|the|to\s+me|(?:\w+\s+){0,3}(?:vs\.?|versus|and|architecture|concept|pattern|model|component))"
    # "What is X" / "What are X" — educational questions.
    # Negative lookahead excludes possessive/data lookups:
    # "what is MY mrr", "what is OUR user count", "what is THE CURRENT plan"
    r"|what\s+(?:is|are)\s+(?!my\b|our\b|your\b|his\b|her\b|their\b|its\b"
    r"|the\s+(?:current|latest|status|total|number|count))"
    r"(?:\w+\s+){0,2}\w+"
    # "How does X work"
    r"|how\s+does\s+\w+\s+(?:work|function)"
    # "How do I set up / build / implement" — allow words between verb parts
    # so "how do I set IT up" still matches (not just "how do I set up")
    r"|how\s+do\s+(?:you|I|we)\s+(?:\w+\s+){0,2}(?:build|implement|set\s+\w*\s*up|design|architect|configure|deploy|use|start)"
    # "What is the best / key / common"
    r"|what\s+(?:is|are)\s+(?:the\s+)?(?:best|top|main|key|common|different|recommended)"
    r"|(?:give|tell)\s+me\s+(?:a\s+)?(?:overview|breakdown|rundown|summary)\s+(?:of|on)"
    r"|compare\s+.+?\s+(?:vs\.?|versus|and|or|with)\s+.+"
    r"|^\s*compare\b"
    r"|(?:pros?\s+(?:and|&)\s+cons?|advantages?\s+(?:and|&)\s+disadvantages?)(?:\s+of)?"
    r"|what\s+are\s+(?:some|the|good|best)\s+(?:ways?|approaches?|strategies?|practices?|tips?)"
    r"|(?:guide|teach)\s+me\s+(?:through|on|about|how)"
    r"|break\s+(?:down|it\s+down)"
    r"|deep\s+dive\s+(?:into|on)"
    # "When should I use X" — educational choice question
    r"|when\s+should\s+I\s+(?:use|choose|pick|go\s+with|prefer)"
    r"|should\s+I\s+(?:use|choose|pick|go\s+with)\s+\w+"
    # "Difference(s) between X and Y"
    r"|differences?\s+between"
    # "How to / How can I set up / implement / use"
    r"|how\s+(?:to|can\s+I)\s+(?:set\s+up|implement|use|configure|deploy|get\s+started)"
    r")",
    re.IGNORECASE,
)

_DOCUMENT_KEYWORDS = re.compile(
    r"\b(pdf|report|document|spreadsheet|excel|csv|"
    r"create a (?:report|pdf|document|spreadsheet))\b",
    re.IGNORECASE,
)

_DATA_TASK_KEYWORDS = re.compile(
    r"\b(all (?:\w+ )?(?:users?|customers?|data|records?|subscribers?|members?)|"
    r"export|bulk|every (?:user|customer|record|subscriber|member)|"
    r"complete (?:list|report|export|data|breakdown)|"
    r"raw data|user ?base|multi[- ]sheet|detailed analysis|"
    r"full (?:report|list|export|breakdown|data)|"
    r"conversion rate|signups? (?:by|per|over)|"
    r"(?:pull|get|fetch) .*(?:clerk|polar|user|customer) .*data)\b",
    re.IGNORECASE,
)


# Requests Lucy fundamentally cannot fulfill — detect early so we can
# route to a graceful "I can't do that, but here's what I CAN do" response.
_IMPOSSIBLE_REQUESTS = re.compile(
    r"\b(?:"
    r"(?:what(?:'s| is) the )?(?:weather|temperature|forecast|humidity)"
    r"|(?:current |today'?s? )?(?:stock price|share price|stock market|nasdaq|dow jones)"
    r"|live score|match score|game score|sports? score"
    r"|(?:send|write) (?:a )?(?:text|sms|text message)"
    r"|(?:call|phone|ring|dial) (?:me|them|him|her|this number|\d)"
    r"|(?:post|tweet|publish|share) (?:on|to) (?:twitter|x\.com|linkedin|instagram|tiktok|facebook)"
    r"|(?:buy|purchase|order|pay for|checkout|add to cart)"
    r"|(?:crypto|bitcoin|ethereum|btc|eth) (?:price|value|rate)"
    r")\b",
    re.IGNORECASE,
)

# Response template for impossible requests (used by the handler)
IMPOSSIBLE_RESPONSE_HINT = (
    "The user asked for something Lucy cannot do. Respond by: "
    "1) Clearly stating you can't do that specific thing. "
    "2) Suggesting the closest thing you CAN do. "
    "3) Being direct and helpful, not apologetic. "
    "Example: \"I can't check live weather, but I can search for a recent "
    "forecast and summarize it for you. Want me to do that?\""
)

# Dynamic prompt modules loaded AFTER the static prefix (tool_use + memory
# are already in the static prefix for all non-chat intents). Only truly
# intent-specific modules are listed here.
INTENT_MODULES: dict[str, list[str]] = {
    "impossible": [],
    "chat": [],
    "lookup": [],
    "confirmation": [],
    "followup": [],
    "tool_use": [],
    "monitoring": [],
    "command": ["integrations"],
    "code": ["coding"],
    "reasoning": ["research"],
    "document": ["data_tasks"],
    "data": ["data_tasks"],
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

    # 0. Impossible requests — detect FIRST, before anything else can match.
    #    "What's the weather?" would otherwise match _SIMPLE_QUESTION or
    #    _KNOWLEDGE_INTENT and get a clarifying question instead of a
    #    graceful "I can't do that" response.
    if _IMPOSSIBLE_REQUESTS.search(text):
        return _choice("impossible", "fast")

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
        # Data queries need tools even deep in threads —
        # "how many users signed up?" should never be fast-path
        if _DATA_SOURCE_KEYWORDS.search(text) or _CHECK_PATTERNS.search(text):
            return _choice("command", "default")
        return _choice("followup", "fast")

    # 3. Monitoring / alerting — must check BEFORE data tasks so
    #    "monitor performance" doesn't misroute as a data export.
    if _MONITORING_KEYWORDS.search(text):
        return _choice("monitoring", "default")

    # 3a. Data tasks — bulk data exports, "all users", complete reports
    if _DATA_TASK_KEYWORDS.search(text):
        if _DOCUMENT_KEYWORDS.search(text):
            return _choice("data", "code")
        return _choice("data", "code")

    # 3b. Document creation — check BEFORE research so "create a report
    #    about competitors" routes to document, not research.
    if _DOCUMENT_KEYWORDS.search(text) and _ACTION_VERBS.search(text):
        return _choice("document", "document")

    # 3c. Advisory / planning questions — "help me plan", "review my architecture"
    #     These are educational/advisory, not research tasks. Route to chat.
    if _ADVISORY_INTENT.search(text):
        return _choice("chat", "default")

    # 4. Deep research / analysis — check before code to avoid
    #    "research code tools" being classified as coding.
    has_heavy = bool(_RESEARCH_HEAVY.search(text))
    light_matches = _RESEARCH_LIGHT.findall(text)
    if has_heavy or len(light_matches) >= 3:
        return _choice("reasoning", "research")
    if len(light_matches) >= 2 and len(text) > 50:
        return _choice("reasoning", "research")
    if light_matches and len(text) > 40:
        return _choice("chat", "default")

    # 4b. Knowledge/educational questions — MUST be checked BEFORE code
    #     keywords. "What is CI/CD?" or "Explain how Docker works" mention
    #     code topics but are educational, not coding tasks. Route to chat
    #     path so LLM answers from knowledge, no tools needed.
    if _KNOWLEDGE_INTENT.search(text):
        return _choice("chat", "default")

    # 5. Composition tasks — user wants content written, not data fetched.
    #     MUST fire before code keywords so "write a product description
    #     for a code review tool" doesn't misroute as coding.
    if _COMPOSITION_INTENT.search(text) or _COMPOSITION_BROAD.search(text):
        return _choice("chat", "default")

    # 5b. Coding tasks (removed "build" — "build me a report" is not code)
    has_code = _CODE_KEYWORDS.search(text)
    if has_code:
        if _CHECK_PATTERNS.search(text) and len(text) < 80:
            return _choice("tool_use", "default")
        return _choice("code", "code")

    # 5c. Broad composition — "help me write", "can you draft", etc.
    #     Route to default model (not lightweight path) because these need
    #     the full system prompt and higher token limits for quality content.
    if _COMPOSITION_BROAD.search(text):
        return _choice("tool_use", "default")

    # 6. Messages referencing external data sources always need tools
    if _DATA_SOURCE_KEYWORDS.search(text):
        return _choice("tool_use", "default")

    # 7. Short check/verify requests — need tool calls, not fast tier
    if len(text) < 60 and _CHECK_PATTERNS.search(text):
        return _choice("tool_use", "default")

    # 8. Simple lookups — truly simple questions with no data dependency
    if len(text) < 80 and _SIMPLE_QUESTION.match(text):
        if not _CHECK_PATTERNS.search(text) and not _DATA_SOURCE_KEYWORDS.search(text):
            return _choice("lookup", "fast")

    # 8b. (Moved to 4b — knowledge intent now checked before code keywords)

    # 8c. Short conversational messages with no tool/data keywords
    if len(text) < 100 and not _CHECK_PATTERNS.search(text) and not _DATA_SOURCE_KEYWORDS.search(text) and not _ACTION_VERBS.search(text):
        return _choice("chat", "fast")

    # 9. Default — tool-calling, general tasks
    return _choice("tool_use", "default")
