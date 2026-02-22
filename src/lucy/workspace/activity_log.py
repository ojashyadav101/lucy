"""Daily activity logging for cron readability.

Appends timestamped entries to logs/YYYY-MM-DD.md so that
crons (heartbeat, monitors) can read what Lucy did recently.
"""

from __future__ import annotations

from datetime import datetime, timezone

import structlog

from lucy.workspace.filesystem import WorkspaceFS

logger = structlog.get_logger()


async def log_activity(ws: WorkspaceFS, message: str) -> None:
    """Append a timestamped entry to today's log file."""
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S UTC")
    log_path = f"logs/{date_str}.md"

    existing = await ws.read_file(log_path)
    if not existing:
        header = f"# Activity Log — {date_str}\n\n"
        await ws.write_file(log_path, header)

    entry = f"- **{time_str}** — {message}\n"
    await ws.append_file(log_path, entry)


async def get_recent_activity(ws: WorkspaceFS, days: int = 1) -> str:
    """Read the most recent activity log(s)."""
    now = datetime.now(timezone.utc)
    lines: list[str] = []

    for offset in range(days):
        from datetime import timedelta
        date = now - timedelta(days=offset)
        date_str = date.strftime("%Y-%m-%d")
        content = await ws.read_file(f"logs/{date_str}.md")
        if content:
            lines.append(content)

    return "\n".join(lines) if lines else "(No recent activity)"


async def get_last_heartbeat_time(ws: WorkspaceFS) -> str | None:
    """Read the last heartbeat timestamp from state."""
    state = await ws.read_state()
    return state.get("last_heartbeat_at")


async def set_last_heartbeat_time(ws: WorkspaceFS) -> None:
    """Record that a heartbeat just ran."""
    await ws.update_state({
        "last_heartbeat_at": datetime.now(timezone.utc).isoformat(),
    })
