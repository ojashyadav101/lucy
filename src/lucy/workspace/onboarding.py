"""Day-1 onboarding flow for a new workspace.

Triggered when Lucy receives the first message from an unknown workspace.
Creates the workspace directory, seeds platform skills, profiles the team
and company, and sets up default crons.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import structlog

from lucy.config import settings
from lucy.workspace.filesystem import WorkspaceFS

logger = structlog.get_logger()

_SEEDS_DIR = Path(__file__).parent.parent.parent.parent / "workspace_seeds"


async def onboard_workspace(
    workspace_id: str,
    slack_client: object | None = None,
) -> WorkspaceFS:
    """Run the full onboarding sequence for a new workspace.

    Steps:
    1. Create workspace directory structure
    2. Copy pre-seeded platform skills
    3. Profile team members (if Slack client available)
    4. Stub company/SKILL.md
    5. Set up default cron placeholders
    6. Reload cron scheduler for this workspace

    Returns the initialized WorkspaceFS.
    """
    ws = WorkspaceFS(workspace_id=workspace_id, base_path=settings.workspace_root)

    if ws.exists:
        logger.info("workspace_already_exists", workspace_id=workspace_id)
        return ws

    logger.info("onboarding_started", workspace_id=workspace_id)

    # Step 1: Create directory structure
    await ws.ensure_structure()

    # Step 2: Copy platform skills into skills/ subdirectory
    skills_dir = _SEEDS_DIR / "skills"
    skill_count = await ws.copy_seeds(skills_dir, target_subdir="skills")
    logger.info("skills_seeded", workspace_id=workspace_id, count=skill_count)

    # Step 3: Copy default cron definitions into crons/ subdirectory
    crons_dir = _SEEDS_DIR / "crons"
    cron_count = await ws.copy_seeds(crons_dir, target_subdir="crons")
    logger.info("crons_seeded", workspace_id=workspace_id, count=cron_count)

    # Step 4: Profile team members from Slack
    if slack_client:
        await _profile_team(ws, slack_client)

    # Step 5: Create company/SKILL.md (enriched from Slack metadata if available)
    await _create_company_profile(ws, slack_client)

    # Step 6: Update state
    await ws.update_state({
        "onboarded_at": datetime.now(timezone.utc).isoformat(),
        "skills_seeded": skill_count,
        "status": "onboarded",
    })

    # Step 7: Reload cron scheduler so new crons are picked up immediately
    try:
        from lucy.crons.scheduler import get_scheduler

        scheduler = get_scheduler()
        loaded = await scheduler.reload_workspace(workspace_id)
        logger.info(
            "onboarding_crons_loaded",
            workspace_id=workspace_id,
            count=loaded,
        )
    except Exception as e:
        logger.warning("onboarding_cron_reload_failed", error=str(e))

    logger.info(
        "onboarding_complete",
        workspace_id=workspace_id,
        skills=skill_count,
        crons=cron_count,
    )
    return ws


async def _profile_team(ws: WorkspaceFS, slack_client: object) -> None:
    """Fetch team members from Slack and create team/SKILL.md with timezone data."""
    try:
        result = await slack_client.users_list()  # type: ignore[attr-defined]
        members = result.get("members", [])

        lines = [
            "---",
            "name: team",
            "description: Team member profiles, roles, and timezones. Use when personalizing responses, scheduling meetings, or reaching out to individuals.",
            "---",
            "",
            "# Team Members",
            "",
            "| Name | Display Name | Email | Title | Slack ID | Timezone | TZ Offset (s) |",
            "| ---- | ------------ | ----- | ----- | -------- | -------- | -------------- |",
        ]

        for member in members:
            if member.get("is_bot") or member.get("deleted") or member.get("id") == "USLACKBOT":
                continue
            profile = member.get("profile", {})
            name = profile.get("real_name", member.get("name", "Unknown"))
            display = profile.get("display_name", "")
            email = profile.get("email", "")
            title = profile.get("title", "")
            slack_id = member.get("id", "")
            tz = member.get("tz", "")
            tz_offset = member.get("tz_offset", "")
            lines.append(
                f"| {name} | {display} | {email} | {title} "
                f"| {slack_id} | {tz} | {tz_offset} |"
            )

        lines.extend([
            "",
            "## Timezone Notes",
            "",
            "- `Timezone` is the IANA identifier (e.g. `America/New_York`)",
            "- `TZ Offset` is seconds from UTC (changes with DST — refresh periodically)",
            "- To compute a user's local time: `UTC + tz_offset`",
            "- Don't cache tz_offset for long periods — Slack updates it silently for DST",
            "",
            "## Notes",
            "",
            "Update this file as you learn about team members' preferences,",
            "working hours, communication styles, and areas of responsibility.",
        ])

        await ws.write_file("team/SKILL.md", "\n".join(lines))
        logger.info(
            "team_profiled",
            workspace_id=ws.workspace_id,
            member_count=len(
                [m for m in members if not m.get("is_bot") and not m.get("deleted")]
            ),
        )

    except Exception as e:
        logger.warning("team_profiling_failed", error=str(e))
        await _create_team_stub(ws)


async def _create_team_stub(ws: WorkspaceFS) -> None:
    """Create a placeholder team/SKILL.md when Slack API isn't available."""
    content = """\
---
name: team
description: Team member profiles, roles, and timezones. Use when personalizing responses, scheduling meetings, or reaching out to individuals.
---

# Team Members

(Not yet profiled — Lucy will populate this after connecting to Slack.)

## Timezone Notes

- Timezone data will be populated from Slack user profiles
- Each user has: tz (IANA identifier), tz_offset (seconds from UTC)

## Notes

Update this file as you learn about team members' preferences,
working hours, communication styles, and areas of responsibility.
"""
    await ws.write_file("team/SKILL.md", content)


async def _create_company_profile(
    ws: WorkspaceFS,
    slack_client: object | None = None,
) -> None:
    """Create company/SKILL.md enriched from Slack workspace metadata."""
    team_name = ""
    team_domain = ""
    channels: list[str] = []

    if slack_client:
        try:
            info = await slack_client.team_info()  # type: ignore[attr-defined]
            team = info.get("team", {})
            team_name = team.get("name", "")
            team_domain = team.get("domain", "")
        except Exception as e:
            logger.debug("team_info_fetch_failed", error=str(e))

        try:
            ch_result = await slack_client.conversations_list(  # type: ignore[attr-defined]
                types="public_channel", limit=50,
            )
            for ch in ch_result.get("channels", []):
                name = ch.get("name", "")
                purpose = ch.get("purpose", {}).get("value", "")
                if name and not name.startswith("general"):
                    label = f"{name}" + (f" — {purpose}" if purpose else "")
                    channels.append(label)
        except Exception as e:
            logger.debug("channels_fetch_failed", error=str(e))

    lines = [
        "---",
        "name: company",
        "description: Company profile, products, and organizational context. Use when you need company-specific context.",
        "---",
        "",
        "# Company Profile",
        "",
    ]

    if team_name:
        lines.append(f"**Name:** {team_name}")
    if team_domain:
        lines.append(f"**Slack domain:** {team_domain}.slack.com")
    if not team_name:
        lines.append("(Lucy will populate this as she learns about the organization.)")

    lines.extend(["", "## Channels (inferred context)", ""])
    if channels:
        for ch in channels[:15]:
            lines.append(f"- #{ch}")
        lines.append("")
        lines.append("Use channel names and descriptions to infer what the team works on.")
    else:
        lines.append("- (Will be discovered from Slack)")

    lines.extend([
        "",
        "## Products / Services",
        "",
        "- (To be discovered from conversations)",
        "",
        "## Key Context",
        "",
        "- (To be discovered from conversations)",
        "",
        "## Culture & Norms",
        "",
        "- (To be discovered from conversations)",
    ])

    await ws.write_file("company/SKILL.md", "\n".join(lines))
    logger.info(
        "company_profile_created",
        workspace_id=ws.workspace_id,
        team_name=team_name or "(unknown)",
        channel_count=len(channels),
    )


async def _create_company_stub(ws: WorkspaceFS) -> None:
    """Create a placeholder company/SKILL.md (legacy fallback)."""
    await _create_company_profile(ws, slack_client=None)


async def ensure_workspace(
    workspace_id: str,
    slack_client: object | None = None,
) -> WorkspaceFS:
    """Get an existing workspace or onboard a new one.

    This is the main entry point — call this from handlers
    instead of creating WorkspaceFS directly.
    """
    ws = WorkspaceFS(workspace_id=workspace_id, base_path=settings.workspace_root)
    if not ws.exists:
        ws = await onboard_workspace(workspace_id, slack_client)
    return ws
