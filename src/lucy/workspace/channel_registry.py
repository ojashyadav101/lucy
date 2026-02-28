"""Channel registry — store and retrieve channel metadata.

Tracks purpose, topic, and sensitivity level for each channel Lucy operates in.
This allows Lucy to:
- Know what each channel is for without asking every time
- Respect channel boundaries (don't post marketing content in #engineering)
- Avoid leaking sensitive DM info into public channels

Channel data is stored per-workspace in data/channels.json.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from lucy.workspace.filesystem import WorkspaceFS

logger = structlog.get_logger()

_CHANNELS_FILE = "data/channels.json"


def _channels_path(ws: WorkspaceFS) -> Path:
    path = ws.root / _CHANNELS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_channel_registry(ws: WorkspaceFS) -> dict[str, Any]:
    """Load the channel registry. Returns empty dict if none stored."""
    path = _channels_path(ws)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_channel_registry(ws: WorkspaceFS, registry: dict[str, Any]) -> None:
    """Persist the channel registry to disk."""
    path = _channels_path(ws)
    try:
        path.write_text(
            json.dumps(registry, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("channel_registry_write_failed", error=str(e))


def register_channel(
    ws: WorkspaceFS,
    channel_id: str,
    name: str = "",
    purpose: str = "",
    topic: str = "",
    is_private: bool = False,
    is_dm: bool = False,
) -> None:
    """Store or update channel metadata."""
    registry = load_channel_registry(ws)
    existing = registry.get(channel_id, {})

    registry[channel_id] = {
        **existing,
        "channel_id": channel_id,
        "name": name or existing.get("name", ""),
        "purpose": purpose or existing.get("purpose", ""),
        "topic": topic or existing.get("topic", ""),
        "is_private": is_private,
        "is_dm": is_dm,
        "last_seen": datetime.now(timezone.utc).isoformat(),
    }
    save_channel_registry(ws, registry)
    logger.debug("channel_registered", channel_id=channel_id, name=name)


def get_channel_context(ws: WorkspaceFS, channel_id: str) -> dict[str, Any]:
    """Get stored metadata for a channel. Returns empty dict if unknown."""
    registry = load_channel_registry(ws)
    return registry.get(channel_id, {})


def format_channel_context_for_prompt(
    ws: WorkspaceFS,
    channel_id: str,
    channel_name: str = "",
) -> str:
    """Format channel context as a prompt snippet."""
    ctx = get_channel_context(ws, channel_id)

    if ctx.get("is_dm"):
        return (
            "<channel_context>\n"
            "You are in a private DM. This is a personal 1:1 conversation. "
            "Information shared here MUST NOT be referenced, quoted, or surfaced "
            "in public channels. Treat DM content as confidential.\n"
            "</channel_context>"
        )

    parts = []
    name = ctx.get("name") or channel_name
    if name:
        parts.append(f"Channel: #{name}")
    if purpose := ctx.get("purpose"):
        parts.append(f"Purpose: {purpose}")
    if topic := ctx.get("topic"):
        parts.append(f"Topic: {topic}")
    if ctx.get("is_private"):
        parts.append("This is a private channel — be mindful of what you surface here.")

    if not parts:
        return ""

    body = "\n".join(parts)
    return (
        f"<channel_context>\n{body}\n"
        "Respect the channel's purpose. Only post content relevant to this channel.\n"
        "</channel_context>"
    )
