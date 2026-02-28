"""Skill system: parse, list, read, write, and format SKILL.md files.

Skills are plain markdown files with YAML frontmatter:
    ---
    name: my-skill
    description: Does X. Use when Y.
    ---
    Full instructions go here...
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml
import structlog

from lucy.workspace.filesystem import WorkspaceFS

logger = structlog.get_logger()

FRONTMATTER_DELIMITER = "---"

# ═══════════════════════════════════════════════════════════════════════════
# INTENT → SKILL MAPPING
# ═══════════════════════════════════════════════════════════════════════════

_SKILL_TRIGGERS: dict[str, list[str]] = {
    "pdf-creation": [
        r"\bpdf\b", r"\breport\b", r"\bdocument\b", r"\binvoice\b",
        r"\bgenerate.*(?:doc|file)\b",
    ],
    "excel-editing": [
        r"\bexcel\b", r"\bxlsx?\b", r"\bspreadsheet\b",
        r"\bworkbook\b", r"\bcsv.*format\b",
    ],
    "docx-editing": [
        r"\bdocx?\b", r"\bword\s*(?:doc|file)?\b", r"\bproposal\b",
        r"\bletter\b", r"\bmemo\b",
    ],
    "pptx-editing": [
        r"\bpptx?\b", r"\bpowerpoint\b", r"\bslide\b", r"\bpresentation\b",
        r"\bdeck\b", r"\bpitch\b",
    ],
    "browser": [
        r"\bbrowse\b", r"\bscrape\b", r"\bwebsite\b", r"\bweb\s*page\b",
        r"\bfill.*form\b", r"\bnavigate\b",
    ],
    "codebase-engineering": [
        r"\bgit(?:hub)?\b", r"\bpull\s*request\b", r"\bPR\b", r"\bcommit\b",
        r"\bbranch\b", r"\brepository\b", r"\brepo\b", r"\bcode\s*review\b",
        r"\bmerge\b", r"\bdeploy\b",
    ],
    "scheduled-crons": [
        r"\bschedule\b", r"\bcron\b", r"\brecurring\b", r"\bautomate\b",
        r"\bevery\s*(?:day|week|hour|morning)\b",
    ],
    "integrations": [
        r"\bintegrat(?:e|ion)s?\b", r"\bconnect(?:ed|ions?)?\b",
        r"\bauthoriz\b", r"\bOAuth\b",
        r"\btools?\b", r"\bservices?\b", r"\bapps?\b",
        r"\bwhat.+(?:have|connected|available)\b",
    ],
    "slack-admin": [
        r"\bchannel\b", r"\binvite\b", r"\bworkspace\b",
        r"\bslack\s*(?:user|member)\b",
    ],
    "company": [
        r"\b(?:our|the)\s+(?:company|team|product|business)\b",
        r"\bwho\s+(?:are\s+we|is)\b",
        r"\bwhat\s+do\s+(?:we|you)\s+(?:do|know)\b",
    ],
}

_COMPILED_TRIGGERS: dict[str, list[re.Pattern[str]]] = {
    skill: [re.compile(p, re.IGNORECASE) for p in patterns]
    for skill, patterns in _SKILL_TRIGGERS.items()
}

_MAX_INJECTED_SKILLS = 3
_MAX_SKILL_CONTENT_CHARS = 20_000
_MIN_REMAINING_FOR_TRUNCATION = 500


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


def detect_relevant_skills(message: str) -> list[str]:
    """Detect which skills are relevant based on message content.

    Returns up to _MAX_INJECTED_SKILLS skill names sorted by match count.
    """
    scores: dict[str, int] = {}

    for skill_name, patterns in _COMPILED_TRIGGERS.items():
        match_count = sum(1 for p in patterns if p.search(message))
        if match_count > 0:
            scores[skill_name] = match_count

    ranked = sorted(scores.keys(), key=lambda s: scores[s], reverse=True)
    selected = ranked[:_MAX_INJECTED_SKILLS]

    if selected:
        logger.debug(
            "skills_detected",
            skills=selected,
            scores={s: scores[s] for s in selected},
        )

    return selected


async def load_relevant_skill_content(
    ws: WorkspaceFS,
    message: str,
) -> str:
    """Detect and load full skill content relevant to the user's message.

    Instead of the model only seeing one-line descriptions, it gets full
    implementation details, code examples, and best practices for skills
    that match the current request.
    """
    skill_names = detect_relevant_skills(message)
    if not skill_names:
        return ""

    all_skills = await list_skills(ws)
    name_to_path: dict[str, str] = {s.name: s.path for s in all_skills}

    sections: list[str] = []
    total_chars = 0
    max_chars = _MAX_SKILL_CONTENT_CHARS

    for name in skill_names:
        path = name_to_path.get(name)
        if not path:
            continue

        content = await ws.read_file(path)
        if not content:
            continue

        _, body = parse_frontmatter(content)
        if not body.strip():
            continue

        if total_chars + len(body) > max_chars:
            remaining = max_chars - total_chars
            if remaining > _MIN_REMAINING_FOR_TRUNCATION:
                body = body[:remaining] + "\n\n[... truncated for brevity]"
            else:
                break

        sections.append(f"### Skill: {name}\n{body.strip()}")
        total_chars += len(body)

    if not sections:
        return ""

    result = "\n\n".join(sections)
    logger.info(
        "skill_content_loaded",
        workspace_id=ws.workspace_id,
        skills_loaded=[s for s in skill_names if s in name_to_path],
        total_chars=total_chars,
    )
    return result


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
