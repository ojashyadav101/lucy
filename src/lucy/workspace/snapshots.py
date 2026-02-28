"""Data snapshot system for workspace trend detection.

Saves JSON data to `data/{category}/YYYY-MM-DD.json` so that crons
and the agent can track metrics over time and compute deltas.

Example categories: "revenue", "signups", "channel-stats", "linear-issues".
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

from lucy.workspace.filesystem import WorkspaceFS

logger = structlog.get_logger()


async def save_snapshot(
    ws: WorkspaceFS,
    category: str,
    data: dict[str, Any] | list[Any],
    date: datetime | None = None,
) -> str:
    """Save a data snapshot for a category.

    Args:
        ws: The workspace filesystem.
        category: Grouping key (e.g. "revenue", "signups").
        data: The data to persist.
        date: Override date (defaults to now UTC).

    Returns:
        The relative path of the saved file.
    """
    date = date or datetime.now(timezone.utc)
    date_str = date.strftime("%Y-%m-%d")
    path = f"data/{category}/{date_str}.json"

    payload = {
        "category": category,
        "captured_at": date.isoformat(),
        "data": data,
    }

    await ws.write_file(path, json.dumps(payload, indent=2, default=str))
    logger.info(
        "snapshot_saved",
        workspace_id=ws.workspace_id,
        category=category,
        date=date_str,
    )
    return path


async def load_latest(
    ws: WorkspaceFS,
    category: str,
) -> dict[str, Any] | None:
    """Load the most recent snapshot for a category.

    Returns the full payload (with `category`, `captured_at`, `data` keys)
    or None if no snapshots exist.
    """
    entries = await ws.list_dir(f"data/{category}")
    json_files = sorted(
        [e for e in entries if e.endswith(".json")], reverse=True
    )

    if not json_files:
        return None

    content = await ws.read_file(json_files[0])
    if not content:
        return None

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        logger.warning(
            "snapshot_parse_failed",
            workspace_id=ws.workspace_id,
            category=category,
            file=json_files[0],
        )
        return None


async def load_snapshot(
    ws: WorkspaceFS,
    category: str,
    date: datetime,
) -> dict[str, Any] | None:
    """Load a specific date's snapshot."""
    date_str = date.strftime("%Y-%m-%d")
    path = f"data/{category}/{date_str}.json"
    content = await ws.read_file(path)
    if not content:
        return None
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return None


async def compute_delta(
    ws: WorkspaceFS,
    category: str,
    key: str,
    days_back: int = 1,
) -> dict[str, Any] | None:
    """Compute the numeric delta for a key between today and N days ago.

    Useful for trend lines like "signups up 12% vs yesterday".

    Returns:
        {current, previous, delta, pct_change} or None if data is missing.
    """
    now = datetime.now(timezone.utc)
    current = await load_snapshot(ws, category, now)
    previous = await load_snapshot(
        ws, category, now - timedelta(days=days_back)
    )

    if not current or not previous:
        return None

    cur_val = _extract_numeric(current.get("data", {}), key)
    prev_val = _extract_numeric(previous.get("data", {}), key)

    if cur_val is None or prev_val is None:
        return None

    delta = cur_val - prev_val
    pct = (delta / prev_val * 100) if prev_val != 0 else 0.0

    return {
        "key": key,
        "current": cur_val,
        "previous": prev_val,
        "delta": delta,
        "pct_change": round(pct, 2),
        "days_back": days_back,
    }


async def list_categories(ws: WorkspaceFS) -> list[str]:
    """List all snapshot categories that have data."""
    entries = await ws.list_dir("data")
    return [e.rstrip("/") for e in entries if e.endswith("/")]


def _extract_numeric(data: Any, key: str) -> float | None:
    """Extract a numeric value from nested data using dot notation.

    Supports paths like "metrics.total" or simple keys like "count".
    """
    if isinstance(data, dict):
        parts = key.split(".", 1)
        val = data.get(parts[0])
        if len(parts) == 1:
            if isinstance(val, (int, float)):
                return float(val)
            return None
        return _extract_numeric(val, parts[1])
    return None
