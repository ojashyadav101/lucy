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

    Returns the initialized WorkspaceFS.
    """
    ws = WorkspaceFS(workspace_id=workspace_id, base_path=settings.workspace_root)

    if ws.exists:
        logger.info("workspace_already_exists", workspace_id=workspace_id)
        return ws

    logger.info("onboarding_started", workspace_id=workspace_id)

    # Step 1: Create directory structure
    await ws.ensure_structure()

    # Step 2: Copy platform skills
    skills_dir = _SEEDS_DIR / "skills"
    skill_count = await ws.copy_seeds(skills_dir)
    logger.info("skills_seeded", workspace_id=workspace_id, count=skill_count)

    # Step 3: Copy default cron definitions
    crons_dir = _SEEDS_DIR / "crons"
    cron_count = await ws.copy_seeds(crons_dir)
    logger.info("crons_seeded", workspace_id=workspace_id, count=cron_count)

    # Step 4: Profile team members from Slack
    if slack_client:
        await _profile_team(ws, slack_client)

    # Step 5: Create stub company/SKILL.md
    await _create_company_stub(ws)

    # Step 6: Update state
    await ws.update_state({
        "onboarded_at": datetime.now(timezone.utc).isoformat(),
        "skills_seeded": skill_count,
        "status": "onboarded",
    })

    logger.info(
        "onboarding_complete",
        workspace_id=workspace_id,
        skills=skill_count,
        crons=cron_count,
    )
    return ws


async def _profile_team(ws: WorkspaceFS, slack_client: object) -> None:
    """Fetch team members from Slack and create team/SKILL.md."""
    try:
        result = await slack_client.users_list()  # type: ignore[attr-defined]
        members = result.get("members", [])

        lines = [
            "---",
            "name: team",
            "description: Team member profiles and roles. Use when personalizing responses or reaching out to individuals.",
            "---",
            "",
            "# Team Members",
            "",
            "| Name | Display Name | Email | Title | Timezone |",
            "| ---- | ------------ | ----- | ----- | -------- |",
        ]

        for member in members:
            if member.get("is_bot") or member.get("deleted") or member.get("id") == "USLACKBOT":
                continue
            profile = member.get("profile", {})
            name = profile.get("real_name", member.get("name", "Unknown"))
            display = profile.get("display_name", "")
            email = profile.get("email", "")
            title = profile.get("title", "")
            tz = member.get("tz", "")
            lines.append(f"| {name} | {display} | {email} | {title} | {tz} |")

        lines.extend([
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
            member_count=len([m for m in members if not m.get("is_bot") and not m.get("deleted")]),
        )

    except Exception as e:
        logger.warning("team_profiling_failed", error=str(e))
        await _create_team_stub(ws)


async def _create_team_stub(ws: WorkspaceFS) -> None:
    """Create a placeholder team/SKILL.md when Slack API isn't available."""
    content = """\
---
name: team
description: Team member profiles and roles. Use when personalizing responses or reaching out to individuals.
---

# Team Members

(Not yet profiled — Lucy will populate this after connecting to Slack.)

## Notes

Update this file as you learn about team members' preferences,
working hours, communication styles, and areas of responsibility.
"""
    await ws.write_file("team/SKILL.md", content)


async def _create_company_stub(ws: WorkspaceFS) -> None:
    """Create a placeholder company/SKILL.md."""
    content = """\
---
name: company
description: Company profile, products, and organizational context. Use when you need company-specific context.
---

# Company Profile

(Lucy will update this as she learns about the organization.)

## Products / Services

- (To be discovered)

## Key Context

- (To be discovered)

## Culture & Norms

- (To be discovered)
"""
    await ws.write_file("company/SKILL.md", content)


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
