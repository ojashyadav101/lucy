"""Skill system: parse, list, read, write, and format SKILL.md files.

Skills are plain markdown files with YAML frontmatter:
    ---
    name: my-skill
    description: Does X. Use when Y.
    ---
    Full instructions go here...
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
import structlog

from lucy.workspace.filesystem import WorkspaceFS

logger = structlog.get_logger()

FRONTMATTER_DELIMITER = "---"


@dataclass
class SkillInfo:
    """Parsed metadata from a SKILL.md file."""

    name: str
    description: str
    path: str  # relative path within workspace, e.g. "skills/browser/SKILL.md"


def parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    """Extract YAML frontmatter and body from a SKILL.md file.

    Returns (metadata_dict, body_text).
    """
    stripped = content.strip()
    if not stripped.startswith(FRONTMATTER_DELIMITER):
        return {}, content

    end_idx = stripped.find(FRONTMATTER_DELIMITER, len(FRONTMATTER_DELIMITER))
    if end_idx == -1:
        return {}, content

    yaml_block = stripped[len(FRONTMATTER_DELIMITER):end_idx].strip()
    body = stripped[end_idx + len(FRONTMATTER_DELIMITER):].strip()

    try:
        metadata = yaml.safe_load(yaml_block) or {}
    except yaml.YAMLError:
        logger.warning("skill_frontmatter_parse_error", yaml_block=yaml_block[:200])
        metadata = {}

    if not isinstance(metadata, dict):
        metadata = {}

    return metadata, body


async def list_skills(ws: WorkspaceFS) -> list[SkillInfo]:
    """Discover all SKILL.md files in the workspace and parse their frontmatter.

    Searches: skills/, company/, team/ directories.
    """
    skills: list[SkillInfo] = []

    search_dirs = ["skills", "company", "team"]
    for search_dir in search_dirs:
        dir_path = ws.root / search_dir
        if not dir_path.is_dir():
            continue

        for skill_file in dir_path.rglob("SKILL.md"):
            rel_path = str(skill_file.relative_to(ws.root))
            content = await ws.read_file(rel_path)
            if not content:
                continue

            metadata, _ = parse_frontmatter(content)
            name = metadata.get("name", skill_file.parent.name)
            description = metadata.get("description", "")

            if not description:
                continue

            skills.append(SkillInfo(
                name=name,
                description=description,
                path=rel_path,
            ))

    logger.debug(
        "skills_listed",
        workspace_id=ws.workspace_id,
        count=len(skills),
    )
    return skills


async def read_skill(ws: WorkspaceFS, skill_path: str) -> str | None:
    """Read the full content of a skill file."""
    return await ws.read_file(skill_path)


async def write_skill(
    ws: WorkspaceFS,
    skill_name: str,
    content: str,
    subdirectory: str = "skills",
) -> str:
    """Create or update a skill file.

    Returns the relative path of the written file.
    """
    rel_path = f"{subdirectory}/{skill_name}/SKILL.md"
    await ws.write_file(rel_path, content)
    logger.info(
        "skill_written",
        workspace_id=ws.workspace_id,
        skill_name=skill_name,
        path=rel_path,
    )
    return rel_path


async def get_skill_descriptions_for_prompt(ws: WorkspaceFS) -> str:
    """Format all skill descriptions for injection into the system prompt.

    Returns skill names and descriptions (no internal paths exposed).
    """
    skills = await list_skills(ws)
    if not skills:
        return "(No skills loaded yet)"

    lines: list[str] = []
    for skill in sorted(skills, key=lambda s: s.name):
        lines.append(f"- {skill.name}: {skill.description}")

    return "\n".join(lines)


async def get_key_skill_content(ws: WorkspaceFS) -> str:
    """Load full content of team and company skills for direct prompt injection.

    These are small, frequently-needed files that the model should always
    have access to without needing to make tool calls.
    """
    sections: list[str] = []

    for subdir, label in [("team", "Team Directory"), ("company", "Company Info")]:
        skill_path = f"{subdir}/SKILL.md"
        content = await ws.read_file(skill_path)
        if content:
            _, body = parse_frontmatter(content)
            if body.strip():
                sections.append(f"### {label}\n{body.strip()}")

    return "\n\n".join(sections) if sections else ""
