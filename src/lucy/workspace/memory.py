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
from datetime import datetime, timezone
from typing import Any

import structlog

from lucy.workspace.filesystem import WorkspaceFS
from lucy.workspace.skills import parse_frontmatter

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
    r"(?:my|our) (?:mrr|revenue|arr|budget|runway) is"
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
    r"(?:i|my) (?:name|role|title|email|timezone|tz)|"
    r"i(?:'m| am) (?:the|a|an|responsible)|"
    r"(?:he|she|they)(?:'s| is| are) (?:the|our|a)|"
    r"(?:works?|working) on|reports? to|"
    r"new (?:hire|team member|employee)|"
    r"(?:joined|leaving|left) (?:the )?(?:team|company)"
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
    (re.compile(r"(?:my name is|i'm|i am)\s+([A-Z][a-z]+(?: [A-Z][a-z]+)?)", re.IGNORECASE),
     "user_preferences", "User's name is {0}"),
    (re.compile(r"(?:my role is|i'm the|i am the|i work as(?: a| an)?)\s+(.{3,40}?)(?:\.|,|$)", re.IGNORECASE),
     "facts", "User's role: {0}"),
    (re.compile(r"(?:we use|our (?:stack|tech|tools?) (?:is|are|includes?))\s+(.{3,60}?)(?:\.|,|$)", re.IGNORECASE),
     "facts", "Tech stack includes: {0}"),
    (re.compile(r"(?:our|my)\s+(?:mrr|arr|revenue|budget|runway)\s+is\s+(.{3,40}?)(?:\.|,|$)", re.IGNORECASE),
     "facts", "Business metric: {0}"),
    (re.compile(r"(?:deadline is|launch (?:by|on|date)|due (?:by|on|date))\s+(.{3,30}?)(?:\.|,|$)", re.IGNORECASE),
     "project_context", "Deadline/launch: {0}"),
    (re.compile(r"(?:we decided|let's go with|decision(?: made)?:?|settled on|chose)\s+(.{3,80}?)(?:\.|$)", re.IGNORECASE),
     "decisions", "Decision: {0}"),
    (re.compile(r"(?:i prefer|please always|always use|never use|my preference is)\s+(.{3,60}?)(?:\.|,|$)", re.IGNORECASE),
     "user_preferences", "Preference: {0}"),
    (re.compile(r"(?:my (?:timezone|tz|time ?zone) is|i'm in)\s+([A-Z][A-Za-z/_+-]{2,30})", re.IGNORECASE),
     "user_preferences", "User timezone: {0}"),
    (re.compile(r"(?:my email is|email me at|reach me at)\s+([^\s,]+@[^\s,]+)", re.IGNORECASE),
     "user_preferences", "User email: {0}"),
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

    Returns False for hypothetical or test scenarios that shouldn't
    be stored as real facts.
    """
    if not _REMEMBER_SIGNALS.search(message):
        # Also check category-specific signals not covered by _REMEMBER_SIGNALS
        if not any(sig.search(message) for sig in (
            _PREFERENCE_SIGNALS, _DECISION_SIGNALS, _PROJECT_SIGNALS,
        )):
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
    """Extract structured facts from a single message.

    Returns list of (fact_text, category) tuples.
    Skips hypothetical/test messages entirely.
    """
    if _HYPOTHETICAL_SIGNALS.search(message):
        return []

    facts: list[tuple[str, str]] = []
    for pattern, category, template in _FACT_EXTRACTORS:
        match = pattern.search(message)
        if match:
            groups = match.groups()
            if groups:
                fact_text = template.format(*groups)
                facts.append((fact_text.strip(), category))

    return facts


# ═══════════════════════════════════════════════════════════════════════════
# SESSION MEMORY — Bridge between threads and permanent knowledge
# ═══════════════════════════════════════════════════════════════════════════

SESSION_MEMORY_PATH = "data/session_memory.json"
MAX_SESSION_ITEMS = 50
MAX_MEMORY_CONTEXT_CHARS = 1500  # ~500 tokens at ~3 chars/token


async def read_session_memory(ws: WorkspaceFS) -> list[dict[str, Any]]:
    """Read session memory items.

    Each item: {"fact": str, "source": str, "ts": str, "category": str}
    """
    content = await ws.read_file(SESSION_MEMORY_PATH)
    if not content:
        return []
    try:
        data = json.loads(content)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


async def write_session_memory(
    ws: WorkspaceFS,
    items: list[dict[str, Any]],
) -> None:
    """Write session memory, keeping only the most recent MAX_SESSION_ITEMS."""
    trimmed = items[-MAX_SESSION_ITEMS:]
    await ws.write_file(
        SESSION_MEMORY_PATH,
        json.dumps(trimmed, indent=2, ensure_ascii=False),
    )


async def add_session_fact(
    ws: WorkspaceFS,
    fact: str,
    source: str = "conversation",
    category: str = "general",
    thread_ts: str | None = None,
    user_id: str | None = None,
) -> None:
    """Add a fact to session memory. Deduplicates by content.

    Uses a per-workspace lock to prevent concurrent read-modify-write
    races that could lose data.
    """
    lock = _get_workspace_lock(ws.workspace_id)
    async with lock:
        items = await read_session_memory(ws)

        fact_lower = fact.lower().strip()
        for existing in items:
            if existing.get("fact", "").lower().strip() == fact_lower:
                return

        entry: dict[str, Any] = {
            "fact": fact,
            "source": source,
            "category": category,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        if thread_ts:
            entry["thread_ts"] = thread_ts
        if user_id:
            entry["user_id"] = user_id

        items.append(entry)
        await write_session_memory(ws, items)
        logger.info("session_fact_added", fact=fact[:100], category=category)


async def get_session_context_for_prompt(
    ws: WorkspaceFS,
    thread_ts: str | None = None,
) -> str:
    """Format session memory for injection into the system prompt.

    If thread_ts is provided, only include facts from that thread
    plus global facts (no thread_ts). This prevents cross-thread
    contamination.

    Kept for backward compatibility. Prefer load_relevant_memories()
    when user_id and topic_hint are available.
    """
    items = await read_session_memory(ws)
    if not items:
        return ""

    filtered: list[dict] = []
    for item in items:
        item_thread = item.get("thread_ts")
        if item_thread is None:
            filtered.append(item)
        elif thread_ts and item_thread == thread_ts:
            filtered.append(item)

    if not filtered:
        return ""

    recent = filtered[-20:]
    lines = [f"• {item['fact']}" for item in recent]

    return (
        "### Recent Context (from earlier conversations)\n"
        + "\n".join(lines)
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
    items = await read_session_memory(ws)
    if not items:
        return ""

    scored: list[tuple[float, dict]] = []
    topic_keywords: set[str] = set()
    if topic_hint:
        topic_keywords = set(re.findall(r"\b[a-z]{3,}\b", topic_hint.lower()))
        topic_keywords -= {
            "the", "and", "for", "that", "this", "with", "from",
            "have", "has", "are", "was", "were", "will", "can",
            "not", "but", "all", "about", "what", "how", "does",
            "your", "you", "please", "could", "would", "should",
            "tell", "help", "know", "think", "like", "just", "some",
        }

    for item in items:
        score = 0.0
        fact = item.get("fact", "")

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
            age_hours = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
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
        "the", "and", "our", "for", "that", "this", "with", "from",
        "have", "has", "are", "was", "were", "will", "been", "being",
        "not", "but", "can", "all", "about", "into", "over",
    }

    if not keywords:
        return None

    existing_lines = [
        line.strip().lstrip("- ")
        for line in content.split("\n")
        if line.strip().startswith("-")
    ]

    conflicts: list[str] = []
    for line in existing_lines:
        line_lower = line.lower()
        line_keywords = set(re.findall(r"\b[a-z]{3,}\b", line_lower))
        overlap = keywords & line_keywords
        if len(overlap) >= 3 and line_lower != fact_lower:
            conflicts.append(line)

    if conflicts:
        return (
            f"Potential contradictions with existing {target} knowledge: "
            + "; ".join(conflicts[:3])
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
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")

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
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")

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
            "# Lucy Learnings\n\n"
            "## Corrections\n\n"
            "## Mistakes\n\n"
            "## Preferences\n\n"
        )

        entry = entry.strip()
        if not entry or entry in content:
            return

        section_header = f"## {section}"
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
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

async def consolidate_session_to_knowledge(ws: WorkspaceFS) -> int:
    """Promote high-value session facts to permanent knowledge files.

    Returns the number of facts promoted.  The per-workspace lock is
    acquired by the individual write helpers, so we only hold the session
    lock for the read-then-write-back of the remaining items.
    """
    lock = _get_workspace_lock(ws.workspace_id)

    async with lock:
        items = await read_session_memory(ws)
        promoted = 0
        remaining = []

        for item in items:
            cat = item.get("category", "general")
            fact = item.get("fact", "").strip()

            if not fact:
                continue

            if cat in ("company", "team"):
                promoted += 1
            else:
                remaining.append(item)

        promote_items = [
            item for item in items
            if item.get("category") in ("company", "team") and item.get("fact", "").strip()
        ]

        if promoted > 0:
            await write_session_memory(ws, remaining)

    for item in promote_items:
        fact = item["fact"].strip()
        if item["category"] == "company":
            await append_to_company_knowledge(ws, fact)
        else:
            await append_to_team_knowledge(ws, fact)

    if promoted > 0:
        logger.info(
            "memory_consolidation_complete",
            workspace_id=ws.workspace_id,
            promoted=promoted,
            remaining=len(remaining),
        )

    return promoted
