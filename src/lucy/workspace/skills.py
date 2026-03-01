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

_MAX_INJECTED_SKILLS = 2
_MAX_SKILL_CONTENT_CHARS = 8_000
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




# ═══════════════════════════════════════════════════════════════════════════
# DYNAMIC SKILL RETRIEVAL — Semantic keyword matching (not just regex triggers)
# ═══════════════════════════════════════════════════════════════════════════

_MAX_RELEVANT_SKILLS = 3
_MAX_TOTAL_SKILL_CHARS = 4_000  # ~1000 tokens


def _extract_keywords(text: str) -> set[str]:
    """Extract meaningful keywords from a query for skill matching."""
    text_lower = text.lower()
    words = set(re.findall(r"\b[a-z]{3,}\b", text_lower))
    # Remove common stop words
    stop_words = {
        "the", "and", "for", "that", "this", "with", "from", "have", "has",
        "are", "was", "were", "will", "been", "not", "but", "can", "all",
        "about", "into", "over", "you", "your", "how", "what", "when",
        "where", "why", "who", "could", "would", "should", "does", "did",
        "make", "help", "please", "want", "need", "like", "just", "get",
        "use", "know", "think", "also", "here", "there", "then", "than",
        "very", "much", "more", "some", "any", "each", "every",
    }
    return words - stop_words


def _score_skill_relevance(
    query_keywords: set[str],
    skill_name: str,
    skill_description: str,
) -> float:
    """Score how relevant a skill is to the query based on keyword overlap.

    Uses a weighted approach: name matches count more than description matches.
    """
    if not query_keywords:
        return 0.0

    name_keywords = _extract_keywords(skill_name.replace("-", " ").replace("_", " "))
    desc_keywords = _extract_keywords(skill_description)

    name_overlap = len(query_keywords & name_keywords)
    desc_overlap = len(query_keywords & desc_keywords)

    # Name matches are worth 3x since they're more specific
    score = (name_overlap * 3.0) + (desc_overlap * 1.0)

    # Boost if skill name appears directly in query
    if skill_name.lower().replace("-", " ") in " ".join(query_keywords):
        score += 5.0

    return score


async def find_relevant_skills(
    query: str,
    workspace_id: str,
    ws: WorkspaceFS | None = None,
) -> list[str]:
    """Find and return content of skills most relevant to a query.

    Uses two-stage matching:
    1. Regex trigger matching (fast, high-precision for known intents)
    2. Keyword similarity matching against skill names + descriptions

    Returns the body content of up to 3 most relevant skills, with
    total content capped at ~1000 tokens (_MAX_TOTAL_SKILL_CHARS).

    Args:
        query: The user's message or search query.
        workspace_id: The workspace ID (used for filesystem access).
        ws: Optional pre-initialized WorkspaceFS instance.

    Returns:
        List of skill content strings (body text, no frontmatter).
    """
    if ws is None:
        ws = WorkspaceFS(workspace_id)

    all_skills = await list_skills(ws)
    if not all_skills:
        return []

    name_to_skill: dict[str, SkillInfo] = {s.name: s for s in all_skills}

    # Stage 1: Regex trigger matches (high confidence)
    trigger_matches = detect_relevant_skills(query)

    # Stage 2: Keyword similarity scoring
    query_keywords = _extract_keywords(query)
    scored: list[tuple[str, float]] = []

    for skill in all_skills:
        # Skip skills already matched by triggers
        if skill.name in trigger_matches:
            continue
        score = _score_skill_relevance(query_keywords, skill.name, skill.description)
        if score > 0:
            scored.append((skill.name, score))

    scored.sort(key=lambda x: x[1], reverse=True)

    # Combine: trigger matches first (they're high-confidence), then keyword matches
    selected_names: list[str] = list(trigger_matches)
    for name, _score in scored:
        if name not in selected_names:
            selected_names.append(name)
        if len(selected_names) >= _MAX_RELEVANT_SKILLS:
            break

    # Load content for selected skills
    results: list[str] = []
    total_chars = 0

    for name in selected_names:
        skill = name_to_skill.get(name)
        if not skill:
            continue

        content = await ws.read_file(skill.path)
        if not content:
            continue

        _, body = parse_frontmatter(content)
        if not body.strip():
            continue

        remaining = _MAX_TOTAL_SKILL_CHARS - total_chars
        if remaining <= 200:
            break

        if len(body) > remaining:
            body = body[:remaining] + "\n\n[... truncated]"

        results.append(body.strip())
        total_chars += len(body)

    logger.info(
        "find_relevant_skills",
        workspace_id=workspace_id,
        query_preview=query[:80],
        matched_skills=selected_names[:_MAX_RELEVANT_SKILLS],
        total_chars=total_chars,
    )

    return results


async def update_skill_with_learning(
    skill_name: str,
    learning: str,
    workspace_id: str,
    ws: WorkspaceFS | None = None,
) -> str:
    """Append a new learning to a skill file, creating it if necessary.

    Learnings are appended under a "## Learnings" section with timestamps.
    If the skill doesn't exist, a new skill file is created with the
    learning as its initial content.

    Args:
        skill_name: The skill identifier (e.g., "browser", "pdf-creation").
        learning: The insight or lesson to persist.
        workspace_id: The workspace ID.
        ws: Optional pre-initialized WorkspaceFS instance.

    Returns:
        The relative path of the updated skill file.
    """
    if ws is None:
        ws = WorkspaceFS(workspace_id)

    from datetime import datetime, timezone

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rel_path = f"skills/{skill_name}/SKILL.md"

    content = await ws.read_file(rel_path)

    if content:
        # Skill exists — append learning under ## Learnings section
        learning_entry = f"- {learning} ({timestamp})"

        # Check for duplicate
        if learning.strip() in content:
            logger.debug(
                "skill_learning_duplicate",
                skill_name=skill_name,
                learning=learning[:80],
            )
            return rel_path

        section_header = "## Learnings"
        if section_header in content:
            content += f"\n{learning_entry}"
        else:
            content += f"\n\n{section_header}\n\n{learning_entry}"
    else:
        # Skill doesn't exist — create with frontmatter
        content = (
            f"---\n"
            f"name: {skill_name}\n"
            f"description: Accumulated knowledge about {skill_name}.\n"
            f"---\n\n"
            f"# {skill_name.replace('-', ' ').title()}\n\n"
            f"## Learnings\n\n"
            f"- {learning} ({timestamp})"
        )

    await ws.write_file(rel_path, content)
    logger.info(
        "skill_learning_updated",
        workspace_id=workspace_id,
        skill_name=skill_name,
        learning_preview=learning[:100],
    )
    return rel_path


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
