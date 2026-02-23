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

import json
import re
from datetime import datetime, timezone
from typing import Any

import structlog

from lucy.workspace.filesystem import WorkspaceFS
from lucy.workspace.skills import parse_frontmatter

logger = structlog.get_logger()

# ═══════════════════════════════════════════════════════════════════════════
# MEMORY EXTRACTION — What should be remembered?
# ═══════════════════════════════════════════════════════════════════════════

_REMEMBER_SIGNALS = re.compile(
    r"\b(?:"
    r"remember|note that|keep in mind|fyi|for your reference|"
    r"going forward|from now on|always|never|our (?:target|goal|kpi)|"
    r"my (?:name|role|email|timezone|preference)|"
    r"we use|we switched to|our stack|we're moving to|"
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
    r"we use|our stack|we(?:'re| are) (?:based|located)|"
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


def should_persist_memory(message: str) -> bool:
    """Quick check: does this message contain facts worth persisting?"""
    return bool(_REMEMBER_SIGNALS.search(message))


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
) -> None:
    """Add a fact to session memory. Deduplicates by content."""
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
    })

    await write_session_memory(ws, items)
    logger.info("session_fact_added", fact=fact[:100], category=category)


async def get_session_context_for_prompt(ws: WorkspaceFS) -> str:
    """Format session memory for injection into the system prompt."""
    items = await read_session_memory(ws)
    if not items:
        return ""

    recent = items[-20:]
    lines = [f"• {item['fact']}" for item in recent]

    return (
        "### Recent Context (from earlier conversations)\n"
        + "\n".join(lines)
    )


# ═══════════════════════════════════════════════════════════════════════════
# KNOWLEDGE PERSISTENCE — Writing facts to permanent skill files
# ═══════════════════════════════════════════════════════════════════════════

async def append_to_company_knowledge(
    ws: WorkspaceFS,
    fact: str,
) -> None:
    """Append a fact to company/SKILL.md."""
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
    """Append a fact to team/SKILL.md."""
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

    Returns the number of facts promoted.
    """
    items = await read_session_memory(ws)
    promoted = 0
    remaining = []

    for item in items:
        cat = item.get("category", "general")
        fact = item.get("fact", "").strip()

        if not fact:
            continue

        if cat == "company":
            await append_to_company_knowledge(ws, fact)
            promoted += 1
        elif cat == "team":
            await append_to_team_knowledge(ws, fact)
            promoted += 1
        else:
            remaining.append(item)

    if promoted > 0:
        await write_session_memory(ws, remaining)
        logger.info(
            "memory_consolidation_complete",
            workspace_id=ws.workspace_id,
            promoted=promoted,
            remaining=len(remaining),
        )

    return promoted
