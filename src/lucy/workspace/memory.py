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


_HYPOTHETICAL_SIGNALS = re.compile(
    r"\b(?:"
    r"(?:what if|imagine|hypothetically|suppose|let's say|pretend|"
    r"for example|e\.g\.|test|testing|dummy|fake|sample|mock|"
    r"i'll ask (?:about )?this later|ask (?:me|you) (?:about )?(?:this|it) later)"
    r")\b",
    re.IGNORECASE,
)


def should_persist_memory(message: str) -> bool:
    """Quick check: does this message contain facts worth persisting?

    Returns False for hypothetical or test scenarios that shouldn't
    be stored as real facts.
    """
    if not _REMEMBER_SIGNALS.search(message):
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


# ═══════════════════════════════════════════════════════════════════════════
# SESSION MEMORY — Bridge between threads and permanent knowledge
# ═══════════════════════════════════════════════════════════════════════════

SESSION_MEMORY_PATH = "data/session_memory.json"
MAX_SESSION_ITEMS = 50


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

        items.append({
            "fact": fact,
            "source": source,
            "category": category,
            "ts": datetime.now(timezone.utc).isoformat(),
            **({"thread_ts": thread_ts} if thread_ts else {}),
        })

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
