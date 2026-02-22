"""Filesystem-based workspace: skills, memory, activity logs, onboarding."""

from lucy.workspace.filesystem import WorkspaceFS, get_workspace
from lucy.workspace.onboarding import ensure_workspace, onboard_workspace
from lucy.workspace.skills import (
    SkillInfo,
    get_skill_descriptions_for_prompt,
    list_skills,
    parse_frontmatter,
    read_skill,
    write_skill,
)
from lucy.workspace.activity_log import get_recent_activity, log_activity

__all__ = [
    "WorkspaceFS",
    "get_workspace",
    "ensure_workspace",
    "onboard_workspace",
    "SkillInfo",
    "get_skill_descriptions_for_prompt",
    "list_skills",
    "parse_frontmatter",
    "read_skill",
    "write_skill",
    "get_recent_activity",
    "log_activity",
]
