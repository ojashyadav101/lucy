"""Persistent memory system for Lucy.

Three tiers of memory:

1. **Thread memory** (ephemeral) — Slack thread history. Loaded via
   conversations.replies API. Dies when the thread goes stale.

2. **Session memory** (medium-term) — Key facts extracted from the
   current conversation and persisted to workspace state. Survives
   across threads within the same day/context.

3. **Knowledge memory** (permanent) — Written to company/SKILL.md,
   team/SKILL.md, or custom skill files. Persists forever and is
   injected into every future system prompt.

The gap in Lucy's current architecture:
- Thread memory works (Slack API)
- Knowledge memory works (skill files)
- Session memory doesn't exist — there's no bridge between "something
  the user said in a thread" and "something Lucy remembers permanently"

This module provides that bridge.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from typing import Any

import structlog

from lucy.workspace.filesystem import WorkspaceFS

logger = structlog.get_logger()

_workspace_locks: dict[str, asyncio.Lock] = {}


def _get_workspace_lock(workspace_id: str) -> asyncio.Lock:
    """Get or create an asyncio.Lock for the given workspace."""
    if workspace_id not in _workspace_locks:
        _workspace_locks[workspace_id] = asyncio.Lock()
    return _workspace_locks[workspace_id]


# ═══════════════════════════════════════════════════════════════════════════
# MEMORY EXTRACTION — What should be remembered?
# ═══════════════════════════════════════════════════════════════════════════

_REMEMBER_SIGNALS = re.compile(
    r"\b(?:"
    r"remember|note that|keep in mind|fyi|for your reference|"
    r"going forward|from now on|always|never|our (?:target|goal|kpi)|"
    r"my (?:name|role|email|timezone|preference)|"
    r"we use|we switched to|our stack|we're moving to|"
    r"our (?:company|team|product) (?:use[sd]?|is|has|runs?)|"
    r"(?:new|updated?) (?:target|goal|deadline|process)|"
    r"i(?:'m| am) (?:the|a|responsible for)|"
    r"(?:my|our) (?:mrr|revenue|arr|budget|runway) is|"
    r"my (?:boss|manager|lead|cto|ceo|coo|vp|director|head|supervisor|report|pm|po|owner) is|"
    r"(?:he|she|they)(?:'s| is| are) (?:my|our|the) (?:boss|manager|lead|head|director)|"
    r"(?:reports?|reports to|manages?|leads?|runs?) (?:the |our )?(?:team|department|eng|product|design|sales|marketing)"  # noqa: E501
    r")\b",
    re.IGNORECASE,
)

_COMPANY_SIGNALS = re.compile(
    r"\b(?:"
    r"our company|we(?:'re| are) (?:a|an)|our product|our service|"
    r"our (?:mrr|arr|revenue|valuation|headcount|team size)|"
    r"we (?:use|switched to|moved to|migrated to)|our stack|"
    r"we(?:'re| are) (?:based|located)|"
    r"our (?:clients?|customers?)|(?:founded|started) in"
    r")\b",
    re.IGNORECASE,
)

_TEAM_SIGNALS = re.compile(
    r"\b(?:"
    r"i(?:'m| am) (?:the|a|an|responsible)|"
    r"(?:he|she|they)(?:'s| is| are) (?:the|our|a)|"
    r"(?:works?|working) on|reports? to|"
    r"new (?:hire|team member|employee)|"
    r"(?:joined|leaving|left) (?:the )?(?:team|company)|"
    r"my (?:team lead|tech lead|engineering lead|product lead|design lead|"
    r"manager|direct manager|skip|skip.level|project manager|product manager|"
    r"program manager|account manager|pm|po|eng lead|head of (?:eng|product|design|sales|marketing))|"  # noqa: E501
    r"(?:head|lead|manager|director|vp|chief) of (?:eng(?:ineering)?|product|design|sales|marketing|growth|ops)|"  # noqa: E501
    r"[A-Z][a-z]+ (?:manages?|leads?|runs?) (?:the )?(?:team|eng(?:ineering)?|product|design|sales|marketing)|"  # noqa: E501
    r"is (?:our|the) (?:cto|ceo|coo|vp|head of \w+|founder|co.?founder|director)"
    r")\b",
    re.IGNORECASE,
)


_PREFERENCE_SIGNALS = re.compile(
    r"\b(?:"
    r"i (?:prefer|like|want|need)|"
    r"(?:please )?(?:always|never) (?:use|include|add|format)|"
    r"my (?:preferred|favorite|default)|"
    r"(?:use|format|write|send) (?:it |things )?in|"
    r"(?:don't|do not|stop) (?:use|include|add|send)|"
    r"(?:tone|style|voice|format) should be"
    r")\b",
    re.IGNORECASE,
)

_DECISION_SIGNALS = re.compile(
    r"\b(?:"
    r"(?:we|i) decided|(?:let's|we'll) go with|"
    r"(?:final|approved|confirmed) (?:decision|choice|plan)|"
    r"(?:we're|we are) going (?:to|with)|"
    r"(?:the plan is|decision made|settled on|chose|picked)"
    r")\b",
    re.IGNORECASE,
)

_PROJECT_SIGNALS = re.compile(
    r"\b(?:"
    r"(?:the|our|this) project|deadline (?:is|was)|"
    r"(?:launch|ship|release|deploy) (?:date|by|on|is)|"
    r"(?:sprint|milestone|phase|roadmap)|"
    r"(?:working on|building|developing|shipping)"
    r")\b",
    re.IGNORECASE,
)


def _has_hypothetical_signals(message: str) -> bool:
    """Return True if the message appears to be hypothetical or testing.

    The check is deliberately conservative: words like 'test' and 'sample'
    that appear inside email addresses (e.g. test@example.com) must not
    trigger this filter, so we first strip any email addresses from the
    message before checking for signal words.
    """
    _EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
    _HYPO_PATTERN = re.compile(
        r"\b(?:"
        r"what if|imagine|hypothetically|suppose|let's say|pretend|"
        r"for example|e\.g\.|(?:^|\s)test(?:ing)?\b|dummy|fake|sample|mock|"
        r"i'll ask (?:about )?this later|ask (?:me|you) (?:about )?(?:this|it) later"
        r")\b",
        re.IGNORECASE,
    )
    text_without_emails = _EMAIL_PATTERN.sub("EMAIL", message)
    return bool(_HYPO_PATTERN.search(text_without_emails))


# Keep the compiled pattern for legacy code paths that use it directly,
# but the public API should use _has_hypothetical_signals().
_HYPOTHETICAL_SIGNALS = re.compile(
    r"\b(?:"
    r"(?:what if|imagine|hypothetically|suppose|let's say|pretend|"
    r"for example|e\.g\.|test|testing|dummy|fake|sample|mock|"
    r"i'll ask (?:about )?this later|ask (?:me|you) (?:about )?(?:this|it) later)"
    r")\b",
    re.IGNORECASE,
)

# ── Structured fact extractors ────────────────────────────────────────────
# Patterns to extract concrete facts from messages for richer categorization
_FACT_EXTRACTORS: list[tuple[re.Pattern[str], str, str]] = [
    # Match name-declaration variants: "my name is", "my preferred name is", "call me", etc.
    # Captures the first 1-2 word(s) after the keyword phrase. Post-processed below
    # (see _apply_fact_extractors) to require the first token starts with an uppercase
    # letter, preventing false positives like "my name is unknown" or "call me anything".
    (
        re.compile(
            r"(?:my (?:preferred |full |first |display )?name is"
            r"|i(?:'m| am) called"
            r"|i go by"
            r"|call me"
            r"|prefer(?:red)? (?:to be called|the name|name))"
            r"\s+(\w+(?: \w+)?)",
            re.IGNORECASE,
        ),
        "user_preferences",
        "User name: {0}",
    ),
    (
        re.compile(
            r"(?:my role is|i'm the|i am the|i work as(?: a| an)?)\s+(.{3,40}?)(?:\.|,|$)",
            re.IGNORECASE,
        ),
        "facts",
        "User's role: {0}",
    ),
    (
        re.compile(
            r"(?:we use|our (?:stack|tech|tools?) (?:is|are|includes?))\s+(.{3,60}?)(?:\.|,|$)",
            re.IGNORECASE,
        ),
        "facts",
        "Tech stack includes: {0}",
    ),
    (
        re.compile(
            r"(?:our|my)\s+(?:mrr|arr|revenue|budget|runway)\s+is\s+(.{3,40}?)(?:\.|,|$)",
            re.IGNORECASE,
        ),
        "facts",
        "Business metric: {0}",
    ),
    (
        re.compile(
            r"(?:deadline is|launch (?:by|on|date)|due (?:by|on|date))\s+(.{3,30}?)(?:\.|,|$)",
            re.IGNORECASE,
        ),
        "project_context",
        "Deadline/launch: {0}",
    ),
    (
        re.compile(
            r"(?:we decided|let's go with|decision(?: made)?:?|settled on|chose)\s+(.{3,80}?)(?:\.|$)",  # noqa: E501
            re.IGNORECASE,
        ),
        "decisions",
        "Decision: {0}",
    ),
    (
        re.compile(
            r"(?:i prefer|please always|always use|never use|my preference is)\s+(.{3,60}?)(?:\.|,|$)",  # noqa: E501
            re.IGNORECASE,
        ),
        "user_preferences",
        "Preference: {0}",
    ),
    # Person → role relationships: "my [role] is [Name]" or "[Name] is my [role]"
    # These patterns capture TWO groups: (role_title, person_name).
    # The extraction logic below has special handling for 2-group matches to build
    # "User's [role]: [Name]" facts that include the role keyword for memory retrieval.
    # (?-i:[A-Z][a-z]+) uses a local case-sensitive subpattern for proper names.
    (
        re.compile(
            r"\bmy\s+(boss|manager|direct manager|project manager|product manager|"
            r"program manager|account manager|team lead|tech lead|engineering lead|"
            r"pm|po|cto|ceo|coo|vp|director|head of \w+|supervisor|lead|skip.?level)\s+is\s+"
            r"((?-i:[A-Z][a-z]+)(?:\s+(?-i:[A-Z][a-z]+))*)\b",
            re.IGNORECASE,
        ),
        "facts",
        "User's {0}: {1}",
    ),
    (
        re.compile(
            r"\b((?-i:[A-Z][a-z]+)(?:\s+(?-i:[A-Z][a-z]+))*)\s+is\s+(?:my|our)\s+"
            r"(boss|manager|direct manager|project manager|product manager|program manager|"
            r"account manager|team lead|tech lead|engineering lead|pm|po|cto|ceo|coo|vp|"
            r"director|head of \w+|supervisor|lead)\b",
            re.IGNORECASE,
        ),
        "facts",
        "User's {1}: {0}",
    ),
    # "[Name] manages/leads [team/project]"
    (
        re.compile(
            r"\b((?-i:[A-Z][a-z]+)(?:\s+(?-i:[A-Z][a-z]+))*)\s+"
            r"(manages?|leads?|runs?|oversees?|handles?)\s+"
            r"(?:the\s+)?(?:team|department|engineering|product|design|sales|marketing|roadmap|project)\b",
            re.IGNORECASE,
        ),
        "facts",
        "{0} manages/leads team",
    ),
    # "[Name] is [the/our] [role title]" - general role assignment
    (
        re.compile(
            r"\b((?-i:[A-Z][a-z]+)(?:\s+(?-i:[A-Z][a-z]+))*)\s+is\s+(?:the|our|a|an)\s+"
            r"(cto|ceo|coo|vp(?:\s+of\s+\w+)?|head of \w+|director(?:\s+of\s+\w+)?|"
            r"(?:senior\s+)?(?:project|product|program|account|engineering|technical)\s+manager|"
            r"(?:tech|engineering|team)\s+lead|founder|co.?founder)\b",
            re.IGNORECASE,
        ),
        "facts",
        "{0} is {1}",
    ),
    (
        re.compile(
            r"(?:my (?:timezone|tz|time ?zone) is|i'm in)\s+([A-Z][A-Za-z/_+-]{2,30})",
            re.IGNORECASE,
        ),
        "user_preferences",
        "User timezone: {0}",
    ),
    (
        re.compile(
            r"(?:my (?:new |updated |current )?email(?: address)? is"
            r"|email me at|reach me at|contact me at"
            r"|(?:my )?email(?: address)? (?:changed|updated|switched) to"
            r"|i(?:'m| am) (?:switching|changing|updating) (?:my )?email to"
            r"|switched (?:email providers?;? *[—–-]+ *)?(?:my new email is)?"
            r")\s*([^\s,<>]+@[^\s,<>]+)",
            re.IGNORECASE,
        ),
        "user_preferences",
        "User email: {0}",
    ),
    # Bare email address (entire message is just an email — e.g. reply to "what's your email?")
    (
        re.compile(r"^\s*([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})\s*$", re.IGNORECASE),
        "user_preferences",
        "User email address: {0}",
    ),
    # LLM confirmation patterns: "noted that X as your email", "X is your email address"
    (
        re.compile(
            r"\b([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})\b"
            r"(?:\s+is\s+(?:your|my|the)\s+email(?:\s+address)?|\s+as\s+(?:your|my)\s+email)",
            re.IGNORECASE,
        ),
        "user_preferences",
        "User email address: {0}",
    ),
    # "noted/stored/saved X" where X contains an email
    (
        re.compile(
            r"(?:noted|stored|saved|recorded)\s+(?:that\s+)?([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})\b",
            re.IGNORECASE,
        ),
        "user_preferences",
        "User email address: {0}",
    ),
]

# ── Memory categories ─────────────────────────────────────────────────────
MEMORY_CATEGORIES: dict[str, str] = {
    "user_preferences": "User preferences and working style",
    "project_context": "Project details, timelines, goals",
    "decisions": "Decisions made during conversations",
    "facts": "Important facts about the company, team, or work",
    "general": "General context worth remembering",
}


def should_persist_memory(message: str) -> bool:
    """Quick check: does this message contain facts worth persisting?

    Returns False for:
    - Questions (the user is asking, not stating a fact)
    - Hypothetical or test scenarios that shouldn't be stored as real facts.
    """
    text = message.strip()

    # Questions are requests for information, not statements of fact.
    if text.endswith("?"):
        return False
    # Common interrogative openings
    if re.match(
        r"^(?:what|where|when|who|how|why|which|can you|could you|do you|did you|is there|are there)\b",  # noqa: E501
        text,
        re.IGNORECASE,
    ):
        return False

    if not _REMEMBER_SIGNALS.search(message):
        # Also check category-specific signals not covered by _REMEMBER_SIGNALS
        if not any(
            sig.search(message)
            for sig in (
                _PREFERENCE_SIGNALS,
                _DECISION_SIGNALS,
                _PROJECT_SIGNALS,
                _TEAM_SIGNALS,
                _COMPANY_SIGNALS,
            )
        ):
            return False

    if _HYPOTHETICAL_SIGNALS.search(message):
        return False

    return True


def classify_memory_target(message: str) -> str:
    """Classify where a fact should be stored.

    Returns: "company", "team", or "session".
    """
    if _COMPANY_SIGNALS.search(message):
        return "company"
    if _TEAM_SIGNALS.search(message):
        return "team"
    return "session"


def classify_memory_category(message: str) -> str:
    """Classify the memory category for session facts.

    Returns one of: user_preferences, project_context, decisions,
    facts, general.
    """
    if _PREFERENCE_SIGNALS.search(message):
        return "user_preferences"
    if _DECISION_SIGNALS.search(message):
        return "decisions"
    if _PROJECT_SIGNALS.search(message):
        return "project_context"
    if _COMPANY_SIGNALS.search(message) or _TEAM_SIGNALS.search(message):
        return "facts"
    return "general"


def extract_facts_from_message(message: str) -> list[tuple[str, str]]:
    """Extract structured facts from a single message using regex patterns.

    Returns list of (fact_text, category) tuples.
    Skips hypothetical/test messages and questions entirely.

    This is the fast, zero-cost first pass. For semantic extraction on memory-
    signalled messages where regex finds nothing, see extract_facts_llm().
    """
    if _has_hypothetical_signals(message):
        return []

    # Questions are requests for information, not statements of fact.
    text = message.strip()
    if text.endswith("?"):
        return []
    if re.match(
        r"^(?:what|where|when|who|how|why|which|can you|could you|do you|did you|is there|are there)\b",  # noqa: E501
        text,
        re.IGNORECASE,
    ):
        return []

    facts: list[tuple[str, str]] = []
    seen: set[str] = set()
    for pattern, category, template in _FACT_EXTRACTORS:
        match = pattern.search(message)
        if match:
            groups = match.groups()
            if groups:
                # For "User name:" and "User's [role]:" patterns, require that the
                # captured person name starts with uppercase. For "User's" templates,
                # the name is always in group 0 (first captured group).
                if template.startswith("User name:"):
                    first_word = groups[0].split()[0] if groups[0] else ""
                    if not first_word or not first_word[0].isupper():
                        continue
                fact_text = template.format(*groups).strip()
                if fact_text.lower() not in seen:
                    seen.add(fact_text.lower())
                    facts.append((fact_text, category))

    return facts


async def extract_facts_llm(message: str) -> list[tuple[str, str]]:
    """Semantic fact extraction using the LLM — second-pass fallback.

    Called only when regex extraction found nothing but the message has
    explicit memory signals (should_persist_memory() returned True).
    Uses the fast model to keep latency low (~100 token call).

    Returns list of (fact_text, category) tuples in the same format as
    extract_facts_from_message(), ready to be stored via add_session_fact().
    """
    if _has_hypothetical_signals(message):
        return []

    from lucy.config import settings

    api_key = settings.openrouter_api_key
    if not api_key:
        return []

    _VALID_CATEGORIES = frozenset(
        {
            "user_preferences",
            "project_context",
            "decisions",
            "facts",
            "general",
        }
    )

    prompt = (
        "Extract memorable facts from the following message. "
        "Return a JSON array where each item has:\n"
        '  "fact": a concise, self-contained fact statement\n'
        '  "category": one of user_preferences, project_context, decisions, facts, general\n\n'
        "Rules:\n"
        "- Only extract genuine facts, preferences, decisions, or project context.\n"
        "- Do NOT extract questions, pleasantries, or vague statements.\n"
        "- Use category user_preferences for personal facts (name, email, timezone, role).\n"
        "- Use decisions for explicit choices made.\n"
        "- Use project_context for project-related facts (deadlines, milestones, goals).\n"
        "- Use facts for factual claims about the company, product, or business.\n"
        "- Use general for everything else that is genuinely worth remembering.\n"
        "- Return [] if there are no clear facts worth remembering.\n"
        "- Return only valid JSON, no commentary.\n\n"
        f"Message: {message[:400]}"
    )

    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
            resp = await client.post(
                f"{settings.openrouter_base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.model_tier_fast,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 256,
                    "temperature": 0,
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()

        import json as _json

        # Strip markdown code fences if present (some models wrap JSON in ```json ... ```)
        if content.startswith("```"):
            content = content.split("```", 2)[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.rstrip("`").strip()
        raw = _json.loads(content)
        if not isinstance(raw, list):
            return []

        results: list[tuple[str, str]] = []
        for item in raw:
            fact = str(item.get("fact", "")).strip()
            cat = str(item.get("category", "general")).strip()
            if fact and cat in _VALID_CATEGORIES:
                results.append((fact, cat))

        if results:
            logger.debug(
                "llm_fact_extraction",
                count=len(results),
                source_preview=message[:60],
            )
        return results

    except Exception as _exc:
        logger.debug("llm_fact_extraction_failed", error=str(_exc))
        return []


# ═══════════════════════════════════════════════════════════════════════════
# SESSION MEMORY — Bridge between threads and permanent knowledge
#
# ARCHITECTURE: Two-tier storage for proper user isolation
#
#   data/session_memory.json          — workspace-shared context
#     categories: decisions, project_context, facts, general
#     visible to all workspace members (team knowledge)
#
#   data/session/{user_id}.json       — per-user personal facts
#     category: user_preferences only
#     hard-isolated: never leaked to other users
#
# This mirrors the existing preferences.py pattern (data/preferences/{user_id}.json)
# and prevents Alice's email/timezone/name from appearing in Bob's context window.
# ═══════════════════════════════════════════════════════════════════════════

SESSION_MEMORY_PATH = "data/session_memory.json"
_SESSION_USER_PATH = "data/session/{user_id}.json"
MAX_SESSION_ITEMS = 50
MAX_USER_ITEMS = 100  # personal facts per user; higher limit since they're isolated
MAX_MEMORY_CONTEXT_CHARS = 1500  # ~500 tokens at ~3 chars/token


def _user_session_path(user_id: str) -> str:
    return _SESSION_USER_PATH.format(user_id=user_id)


async def _read_json_list(ws: WorkspaceFS, path: str) -> list[dict[str, Any]]:
    """Read a JSON list from a workspace file, safely."""
    content = await ws.read_file(path)
    if not content:
        return []
    try:
        data = json.loads(content)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


async def read_session_memory(
    ws: WorkspaceFS,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    """Read session memory items.

    When user_id is provided, merges:
    - Workspace-shared facts from data/session_memory.json
    - User-personal facts from data/session/{user_id}.json

    When user_id is absent, returns only workspace-shared facts.
    Personal (user_preferences) facts from other users are never included.
    """
    shared = await _read_json_list(ws, SESSION_MEMORY_PATH)
    if user_id:
        personal = await _read_json_list(ws, _user_session_path(user_id))
        return shared + personal
    return shared


# Facts where only one value should ever be active at a time.
# When a new fact shares this key prefix, the old one is replaced.
_SINGLETON_FACT_KEYS: frozenset[str] = frozenset(
    {
        "user email",
        "user email address",
        "user timezone",
        "user's name is",
        "user name",
        "user phone",
        "user role",
        "user title",
        "user location",
    }
)


def _fact_key(fact: str) -> str:
    """Return the normalised key prefix of a 'Key: value' structured fact."""
    return fact.split(":", 1)[0].strip().lower()


async def add_session_fact(
    ws: WorkspaceFS,
    fact: str,
    source: str = "conversation",
    category: str = "general",
    thread_ts: str | None = None,
    user_id: str | None = None,
) -> None:
    """Add a fact to session memory with routing based on category.

    Routing:
    - category == "user_preferences" AND user_id provided
        → data/session/{user_id}.json  (hard-isolated per user)
    - all other categories
        → data/session_memory.json  (workspace-shared)

    Within each store:
    - Exact-text duplicates are silently dropped.
    - Singleton keys (email, timezone, name…) replace the old entry rather
      than accumulating stale facts when the user updates them.

    Uses a per-workspace lock to prevent concurrent read-modify-write races.
    """
    is_personal = category == "user_preferences" and bool(user_id)
    lock = _get_workspace_lock(ws.workspace_id)

    async with lock:
        if is_personal:
            items = await _read_json_list(ws, _user_session_path(user_id))  # type: ignore[arg-type]
        else:
            items = await _read_json_list(ws, SESSION_MEMORY_PATH)

        fact_lower = fact.lower().strip()
        for existing in items:
            if existing.get("fact", "").lower().strip() == fact_lower:
                return  # exact duplicate — nothing to do

        # For singleton keys, evict any older fact that shares the same key
        # so the memory reflects the current value, not the old one.
        key = _fact_key(fact)
        if key in _SINGLETON_FACT_KEYS:
            before = len(items)
            items = [item for item in items if _fact_key(item.get("fact", "")) != key]
            if len(items) < before:
                logger.info(
                    "session_fact_replaced",
                    key=key,
                    new_value=fact[:80],
                    workspace_id=ws.workspace_id,
                )

        entry: dict[str, Any] = {
            "fact": fact,
            "source": source,
            "category": category,
            "ts": datetime.now(UTC).isoformat(),
        }
        if thread_ts:
            entry["thread_ts"] = thread_ts
        if user_id:
            entry["user_id"] = user_id

        items.append(entry)

        if is_personal:
            await ws.write_file(
                _user_session_path(user_id),  # type: ignore[arg-type]
                json.dumps(items[-MAX_USER_ITEMS:], indent=2, ensure_ascii=False),
            )
        else:
            await ws.write_file(
                SESSION_MEMORY_PATH,
                json.dumps(items[-MAX_SESSION_ITEMS:], indent=2, ensure_ascii=False),
            )

        logger.info(
            "session_fact_added",
            fact=fact[:100],
            category=category,
            store="personal" if is_personal else "shared",
            workspace_id=ws.workspace_id,
        )


async def load_relevant_memories(
    ws: WorkspaceFS,
    user_id: str | None = None,
    thread_ts: str | None = None,
    topic_hint: str | None = None,
) -> str:
    """Load memories relevant to the current conversation using scoring.

    Prioritizes by:
    1. Same-thread facts (+10) — always most relevant
    2. Same-user facts (+3) — likely relevant
    3. Topic-relevant facts (+1.5/keyword) — keyword overlap with message
    4. Recent facts (+5/<1h, +2/<24h, +1/<1wk)
    5. High-value categories (+2 preferences, +1.5 decisions, +1 project)

    Returns formatted string capped at MAX_MEMORY_CONTEXT_CHARS.
    This supersedes get_session_context_for_prompt() for prompt injection.
    """
    items = await read_session_memory(ws, user_id=user_id)
    if not items:
        return ""

    scored: list[tuple[float, dict]] = []
    topic_keywords: set[str] = set()
    if topic_hint:
        topic_keywords = set(re.findall(r"\b[a-z]{3,}\b", topic_hint.lower()))
        topic_keywords -= {
            "the",
            "and",
            "for",
            "that",
            "this",
            "with",
            "from",
            "have",
            "has",
            "are",
            "was",
            "were",
            "will",
            "can",
            "not",
            "but",
            "all",
            "about",
            "what",
            "how",
            "does",
            "your",
            "you",
            "please",
            "could",
            "would",
            "should",
            "tell",
            "help",
            "know",
            "think",
            "like",
            "just",
            "some",
        }

    for item in items:
        score = 0.0
        fact = item.get("fact", "")

        # Hard isolation: skip personal facts that belong to a different user.
        # user_preferences stored under user isolation (data/session/{user_id}.json)
        # already contain only this user's facts. But if a legacy shared-file entry
        # exists with a mismatched user_id, exclude it here too.
        if (
            item.get("category") == "user_preferences"
            and user_id
            and item.get("user_id")
            and item.get("user_id") != user_id
        ):
            continue

        if thread_ts and item.get("thread_ts") == thread_ts:
            score += 10.0

        if user_id and item.get("user_id") == user_id:
            score += 3.0

        if topic_keywords:
            fact_words = set(re.findall(r"\b[a-z]{3,}\b", fact.lower()))
            overlap = topic_keywords & fact_words
            score += len(overlap) * 1.5

        try:
            ts = datetime.fromisoformat(item.get("ts", ""))
            age_hours = (datetime.now(UTC) - ts).total_seconds() / 3600
            if age_hours < 1:
                score += 5.0
            elif age_hours < 24:
                score += 2.0
            elif age_hours < 168:
                score += 1.0
        except (ValueError, TypeError):
            pass

        cat = item.get("category", "general")
        if cat == "user_preferences":
            score += 2.0
        elif cat == "decisions":
            score += 1.5
        elif cat == "project_context":
            score += 1.0

        if score > 0:
            scored.append((score, item))

    if not scored:
        return ""

    scored.sort(key=lambda x: x[0], reverse=True)

    lines: list[str] = []
    total_chars = 0
    category_prefix = {
        "user_preferences": "Preference",
        "project_context": "Project",
        "decisions": "Decided",
        "facts": "Fact",
        "general": "Context",
    }
    for _score, item in scored:
        fact = item.get("fact", "").strip()
        cat = item.get("category", "general")
        prefix = category_prefix.get(cat, "Context")
        line = f"- [{prefix}] {fact}"
        if total_chars + len(line) > MAX_MEMORY_CONTEXT_CHARS:
            break
        lines.append(line)
        total_chars += len(line)

    if not lines:
        return ""

    return "### Relevant Context\n" + "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# KNOWLEDGE PERSISTENCE — Writing facts to permanent skill files
# ═══════════════════════════════════════════════════════════════════════════


async def check_fact_contradictions(
    ws: WorkspaceFS,
    fact: str,
    target: str,
) -> str | None:
    """Check if a new fact contradicts existing knowledge.

    Returns a warning string if contradictions found, None if clean.
    Uses keyword overlap to find potentially conflicting stored facts.
    """
    path = f"{target}/SKILL.md"
    content = await ws.read_file(path)
    if not content:
        return None

    fact_lower = fact.lower()
    keywords = set(re.findall(r"\b[a-z]{3,}\b", fact_lower))
    keywords -= {
        "the",
        "and",
        "our",
        "for",
        "that",
        "this",
        "with",
        "from",
        "have",
        "has",
        "are",
        "was",
        "were",
        "will",
        "been",
        "being",
        "not",
        "but",
        "can",
        "all",
        "about",
        "into",
        "over",
    }

    if not keywords:
        return None

    existing_lines = [
        line.strip().lstrip("- ") for line in content.split("\n") if line.strip().startswith("-")
    ]

    conflicts: list[str] = []
    for line in existing_lines:
        line_lower = line.lower()
        line_keywords = set(re.findall(r"\b[a-z]{3,}\b", line_lower))
        overlap = keywords & line_keywords
        if len(overlap) >= 3 and line_lower != fact_lower:
            conflicts.append(line)

    if conflicts:
        return f"Potential contradictions with existing {target} knowledge: " + "; ".join(
            conflicts[:3]
        )
    return None


async def append_to_company_knowledge(
    ws: WorkspaceFS,
    fact: str,
) -> None:
    """Append a fact to company/SKILL.md.

    Uses a per-workspace lock to prevent concurrent write corruption.
    """
    lock = _get_workspace_lock(ws.workspace_id)
    async with lock:
        path = "company/SKILL.md"
        content = await ws.read_file(path)

        if not content:
            content = (
                "---\n"
                "name: company\n"
                "description: Company overview, context, and key business information.\n"
                "---\n\n"
                "# Company Info\n\n"
                "(Not yet configured — will be enriched as Lucy learns.)\n"
            )

        if fact.strip() in content:
            return

        section_header = "## Learned Context"
        timestamp = datetime.now(UTC).strftime("%Y-%m-%d")

        if section_header in content:
            content += f"\n- {fact} ({timestamp})"
        else:
            content += f"\n\n{section_header}\n\n- {fact} ({timestamp})"

        await ws.write_file(path, content)
        logger.info("company_knowledge_updated", fact=fact[:100])


async def append_to_team_knowledge(
    ws: WorkspaceFS,
    fact: str,
) -> None:
    """Append a fact to team/SKILL.md.

    Uses a per-workspace lock to prevent concurrent write corruption.
    """
    lock = _get_workspace_lock(ws.workspace_id)
    async with lock:
        path = "team/SKILL.md"
        content = await ws.read_file(path)

        if not content:
            content = (
                "---\n"
                "name: team\n"
                "description: Team members, roles, and preferences.\n"
                "---\n\n"
                "# Team Directory\n\n"
                "(Not yet configured — will be enriched as Lucy learns.)\n"
            )

        if fact.strip() in content:
            return

        section_header = "## Learned Context"
        timestamp = datetime.now(UTC).strftime("%Y-%m-%d")

        if section_header in content:
            content += f"\n- {fact} ({timestamp})"
        else:
            content += f"\n\n{section_header}\n\n- {fact} ({timestamp})"

        await ws.write_file(path, content)
        logger.info("team_knowledge_updated", fact=fact[:100])


# ═══════════════════════════════════════════════════════════════════════════
# LEARNINGS — Workspace-level persistent improvement log
# ═══════════════════════════════════════════════════════════════════════════

LEARNINGS_PATH = "data/LEARNINGS.md"

_CORRECTION_SIGNALS = re.compile(
    r"\b(?:"
    r"that(?:'s| is| was) (?:wrong|incorrect|not right|not accurate|off)|"
    r"no,?\s+actually|not quite|you(?:'re| are) wrong|that's not|"
    r"i said|i meant|what i said|i didn't say|actually it(?:'s| is)|"
    r"the (?:right|correct) (?:answer|number|figure|info)|"
    r"please (?:fix|correct|update)|wrong number|wrong data|incorrect data|"
    r"you got it wrong|you made a mistake|you were wrong"
    r")\b",
    re.IGNORECASE,
)


async def append_learning(
    ws: WorkspaceFS,
    entry: str,
    section: str = "Mistakes",
) -> None:
    """Append a timestamped learning entry to data/LEARNINGS.md.

    Args:
        ws: The workspace to write to.
        entry: The learning to record (one concise line).
        section: Which section to append to: "Corrections", "Mistakes",
                 or "Preferences".
    """
    lock = _get_workspace_lock(ws.workspace_id)
    async with lock:
        content = await ws.read_file(LEARNINGS_PATH) or (
            "# Lucy Learnings\n\n## Corrections\n\n## Mistakes\n\n## Preferences\n\n"
        )

        entry = entry.strip()
        if not entry or entry in content:
            return

        section_header = f"## {section}"
        timestamp = datetime.now(UTC).strftime("%Y-%m-%d")
        line = f"- [{timestamp}] {entry}"

        if section_header in content:
            idx = content.index(section_header) + len(section_header)
            content = content[:idx] + f"\n\n{line}" + content[idx:]
        else:
            content += f"\n\n{section_header}\n\n{line}\n"

        await ws.write_file(LEARNINGS_PATH, content)
        logger.info(
            "learning_appended",
            workspace_id=ws.workspace_id,
            section=section,
            entry=entry[:100],
        )


# ═══════════════════════════════════════════════════════════════════════════
# MEMORY CONSOLIDATION — Periodic promotion of session → knowledge
# ═══════════════════════════════════════════════════════════════════════════

_CONSOLIDATION_JOURNAL_PATH = "data/consolidation_journal.json"


async def consolidate_session_to_knowledge(ws: WorkspaceFS) -> int:
    """Promote high-value session facts to permanent knowledge files.

    Promotion rules (facts must be at least 24 hours old to be stable):
    - category "decisions"       → team/SKILL.md  (team-wide decisions persist)
    - category "project_context" → team/SKILL.md  (project info belongs to team)
    - category "facts"           → company/SKILL.md  (hard facts about the business)
    - category "user_preferences" → kept in per-user file indefinitely (not promoted)
    - category "general"          → kept in shared pool (not promoted)

    Crash safety: Uses a journal file to prevent the data-loss window between
    removing facts from session memory and writing them to skill files. On
    restart, any unfinished journal entries are replayed before new consolidation.

    Returns the number of facts promoted.
    """
    from datetime import timedelta

    lock = _get_workspace_lock(ws.workspace_id)
    cutoff = datetime.now(UTC) - timedelta(hours=24)

    _PROMOTE_TO_TEAM: frozenset[str] = frozenset({"decisions", "project_context"})
    _PROMOTE_TO_COMPANY: frozenset[str] = frozenset({"facts"})

    # ── Step 0: Replay any unfinished journal from a previous crash ──────────
    try:
        journal_raw = await _read_json_list(ws, _CONSOLIDATION_JOURNAL_PATH)
        if journal_raw:
            logger.info(
                "consolidation_journal_replay",
                workspace_id=ws.workspace_id,
                count=len(journal_raw),
            )
            for entry in journal_raw:
                fact = entry.get("fact", "")
                dest = entry.get("dest", "")
                if not fact:
                    continue
                if dest == "team":
                    await append_to_team_knowledge(ws, fact)
                elif dest == "company":
                    await append_to_company_knowledge(ws, fact)
            # Journal replayed — clear it
            await ws.write_file(_CONSOLIDATION_JOURNAL_PATH, "[]")
    except Exception as journal_err:
        logger.warning(
            "consolidation_journal_replay_failed",
            workspace_id=ws.workspace_id,
            error=str(journal_err),
        )

    # ── Step 1: Classify facts and build the journal ─────────────────────────
    async with lock:
        items = await _read_json_list(ws, SESSION_MEMORY_PATH)
        remaining: list[dict] = []
        to_team: list[str] = []
        to_company: list[str] = []

        for item in items:
            cat = item.get("category", "general")
            fact = item.get("fact", "").strip()

            if not fact:
                continue

            try:
                ts = datetime.fromisoformat(item.get("ts", ""))
                old_enough = ts < cutoff
            except (ValueError, TypeError):
                old_enough = True  # malformed timestamp — assume old

            if old_enough and cat in _PROMOTE_TO_TEAM:
                to_team.append(fact)
            elif old_enough and cat in _PROMOTE_TO_COMPANY:
                to_company.append(fact)
            else:
                remaining.append(item)

        promoted = len(to_team) + len(to_company)
        if promoted == 0:
            return 0

        # ── Step 2: Write journal BEFORE removing from session ───────────────
        # If we crash after this point, the journal replay above will finish
        # writing the facts to skill files on the next run.
        journal_entries = [{"fact": f, "dest": "team"} for f in to_team] + [
            {"fact": f, "dest": "company"} for f in to_company
        ]
        await ws.write_file(
            _CONSOLIDATION_JOURNAL_PATH,
            json.dumps(journal_entries, indent=2, ensure_ascii=False),
        )

        # ── Step 3: Remove promoted facts from session memory ────────────────
        await ws.write_file(
            SESSION_MEMORY_PATH,
            json.dumps(remaining[-MAX_SESSION_ITEMS:], indent=2, ensure_ascii=False),
        )

    # ── Step 4: Write facts to skill files ───────────────────────────────────
    # Lock released — if a crash occurs here, journal replay handles recovery.
    for fact in to_team:
        await append_to_team_knowledge(ws, fact)
    for fact in to_company:
        await append_to_company_knowledge(ws, fact)

    # ── Step 5: Clear journal — promotion complete ────────────────────────────
    await ws.write_file(_CONSOLIDATION_JOURNAL_PATH, "[]")

    logger.info(
        "memory_consolidation_complete",
        workspace_id=ws.workspace_id,
        promoted_to_team=len(to_team),
        promoted_to_company=len(to_company),
        remaining=len(remaining),
    )

    return promoted
