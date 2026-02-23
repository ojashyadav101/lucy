"""Timezone resolution utilities.

Resolves user-local times from Slack timezone data stored in team/SKILL.md.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import structlog

from lucy.workspace.filesystem import WorkspaceFS, get_workspace

logger = structlog.get_logger()


async def get_user_local_time(
    workspace_id: str,
    user_slack_id: str,
) -> datetime | None:
    """Compute a user's current local time from their Slack tz_offset.

    Returns None if the user or timezone data can't be found.
    Note: tz_offset changes silently with DST — this uses the last
    known value from team/SKILL.md. For fresh data, re-profile the team.
    """
    tz_offset_seconds = await _get_user_tz_offset(workspace_id, user_slack_id)
    if tz_offset_seconds is None:
        return None

    user_tz = timezone(timedelta(seconds=tz_offset_seconds))
    return datetime.now(user_tz)


async def get_user_timezone_name(
    workspace_id: str,
    user_slack_id: str,
) -> str | None:
    """Get a user's IANA timezone identifier (e.g. 'America/New_York')."""
    ws = get_workspace(workspace_id)
    content = await ws.read_file("team/SKILL.md")
    if not content:
        return None

    for line in content.splitlines():
        if user_slack_id in line:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 8:
                tz_name = parts[6]
                if tz_name and "/" in tz_name:
                    return tz_name
    return None


async def get_all_user_timezones(
    workspace_id: str,
) -> dict[str, dict[str, str | int]]:
    """Get timezone data for all team members.

    Returns {slack_id: {"name": "...", "tz": "America/...", "tz_offset": N}}
    """
    ws = get_workspace(workspace_id)
    content = await ws.read_file("team/SKILL.md")
    if not content:
        return {}

    result: dict[str, dict[str, str | int]] = {}
    in_table = False

    for line in content.splitlines():
        if "| Name |" in line:
            in_table = True
            continue
        if "| ----" in line:
            continue
        if not in_table or not line.startswith("|"):
            if in_table and not line.strip():
                break
            continue

        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 8:
            continue

        name = parts[1]
        slack_id = parts[5]
        tz_name = parts[6]
        tz_offset_str = parts[7]

        if not slack_id:
            continue

        tz_offset = 0
        if tz_offset_str:
            try:
                tz_offset = int(tz_offset_str)
            except ValueError:
                pass

        result[slack_id] = {
            "name": name,
            "tz": tz_name,
            "tz_offset": tz_offset,
        }

    return result


async def find_best_meeting_time(
    workspace_id: str,
    participant_ids: list[str],
    preferred_hour_start: int = 9,
    preferred_hour_end: int = 17,
) -> list[int]:
    """Find overlapping working hours across participants.

    Returns a list of UTC hours where all participants are within
    their preferred working hours.
    """
    timezones = await get_all_user_timezones(workspace_id)

    offsets = []
    for pid in participant_ids:
        tz_data = timezones.get(pid)
        if tz_data:
            offsets.append(int(tz_data.get("tz_offset", 0)))

    if not offsets:
        return list(range(preferred_hour_start, preferred_hour_end))

    good_utc_hours: list[int] = []
    for utc_hour in range(24):
        all_ok = True
        for offset in offsets:
            local_hour = (utc_hour + offset // 3600) % 24
            if not (preferred_hour_start <= local_hour < preferred_hour_end):
                all_ok = False
                break
        if all_ok:
            good_utc_hours.append(utc_hour)

    return good_utc_hours


async def _get_user_tz_offset(
    workspace_id: str,
    user_slack_id: str,
) -> int | None:
    """Extract tz_offset for a user from team/SKILL.md."""
    ws = get_workspace(workspace_id)
    content = await ws.read_file("team/SKILL.md")
    if not content:
        return None

    for line in content.splitlines():
        if user_slack_id in line:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 8:
                offset_str = parts[7]
                try:
                    return int(offset_str)
                except (ValueError, TypeError):
                    return None
    return None
