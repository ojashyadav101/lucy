"""Proactive event queue — lightweight buffer for event-driven awareness.

Slack events (reactions, new members, new channels) are captured by
handlers and written here. The heartbeat reads and clears this queue
each run, giving it rich context about what has happened since last check.

File location: workspaces/{id}/data/proactive_events.jsonl
Format: one JSON object per line, newest at bottom.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import structlog

from lucy.workspace.filesystem import WorkspaceFS

logger = structlog.get_logger()

_EVENTS_FILE = "data/proactive_events.jsonl"
_MAX_EVENTS = 500  # cap file growth between heartbeat runs


async def append_proactive_event(
    ws: WorkspaceFS,
    event_type: str,
    data: dict[str, Any],
) -> None:
    """Append a proactive event to the queue.

    Args:
        ws: Workspace filesystem.
        event_type: One of "reaction_added", "member_joined", "channel_created",
            "unanswered_question", "celebration".
        data: Arbitrary context dict (channel, user, message_ts, text, etc.)
    """
    entry = {
        "type": event_type,
        "ts": datetime.now(UTC).isoformat(),
        **data,
    }
    line = json.dumps(entry, ensure_ascii=False)
    await ws.append_file(_EVENTS_FILE, line + "\n")

    # Trim if overgrown (rare -- heartbeat should clear regularly)
    existing = await ws.read_file(_EVENTS_FILE)
    if existing:
        lines = [ln for ln in existing.splitlines() if ln.strip()]
        if len(lines) > _MAX_EVENTS:
            trimmed = "\n".join(lines[-_MAX_EVENTS:]) + "\n"
            await ws.write_file(_EVENTS_FILE, trimmed)


async def read_and_clear_proactive_events(
    ws: WorkspaceFS,
) -> list[dict[str, Any]]:
    """Read all pending events and clear the queue.

    Called at the start of each heartbeat run. Returns events in
    chronological order (oldest first).
    """
    content = await ws.read_file(_EVENTS_FILE)
    if not content or not content.strip():
        return []

    events: list[dict[str, Any]] = []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    # Clear the file
    await ws.write_file(_EVENTS_FILE, "")

    logger.debug(
        "proactive_events_read",
        workspace_id=ws.workspace_id,
        count=len(events),
    )
    return events


def format_events_for_prompt(events: list[dict[str, Any]]) -> str:
    """Format proactive events into a concise LLM-readable summary."""
    if not events:
        return ""

    lines: list[str] = [f"## Queued Events ({len(events)} since last heartbeat)"]

    by_type: dict[str, list[dict[str, Any]]] = {}
    for ev in events:
        by_type.setdefault(ev.get("type", "unknown"), []).append(ev)

    for ev_type, items in by_type.items():
        lines.append(f"\n### {ev_type.replace('_', ' ').title()} ({len(items)})")
        for item in items[:10]:  # cap per type to avoid bloat
            ts = item.get("ts", "")[:19]  # trim to YYYY-MM-DDTHH:MM:SS
            parts = [ts]
            if "channel" in item:
                parts.append(f"#{item['channel']}")
            if "user" in item:
                parts.append(f"<{item['user']}>")
            if "text" in item:
                parts.append(item["text"][:100])
            elif "emoji" in item:
                parts.append(f":{item['emoji']}:")
            lines.append("  - " + " ".join(parts))
        if len(items) > 10:
            lines.append(f"  ... and {len(items) - 10} more")

    return "\n".join(lines)
