"""System prompt builder.

Combines SOUL.md (personality), SYSTEM_PROMPT.md (instructions),
and dynamic skill descriptions into the final system message.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from lucy.workspace.filesystem import WorkspaceFS
from lucy.workspace.skills import get_skill_descriptions_for_prompt

logger = structlog.get_logger()

_ASSETS_DIR = Path(__file__).parent.parent.parent.parent / "assets"
_SOUL_PATH = _ASSETS_DIR / "SOUL.md"
_PROMPT_TEMPLATE_PATH = _ASSETS_DIR / "SYSTEM_PROMPT.md"

_soul_cache: str | None = None
_template_cache: str | None = None


def _load_soul() -> str:
    global _soul_cache
    if _soul_cache is None:
        if _SOUL_PATH.exists():
            _soul_cache = _SOUL_PATH.read_text(encoding="utf-8")
        else:
            _soul_cache = (
                "You are Lucy, an AI coworker. Direct, helpful, gets things done. "
                "Lives in Slack with access to tools and integrations."
            )
    return _soul_cache


def _load_template() -> str:
    global _template_cache
    if _template_cache is None:
        if _PROMPT_TEMPLATE_PATH.exists():
            _template_cache = _PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")
        else:
            _template_cache = (
                "You are Lucy, an AI coworker in Slack.\n\n"
                "<available_skills>\n{available_skills}\n</available_skills>"
            )
    return _template_cache


async def build_system_prompt(ws: WorkspaceFS) -> str:
    """Build the complete system prompt for a workspace.

    Combines:
    1. SOUL.md — personality traits and voice
    2. SYSTEM_PROMPT.md — structured instructions with {available_skills} placeholder
    3. Dynamic skill descriptions from the workspace
    """
    soul = _load_soul()
    template = _load_template()
    skill_descriptions = await get_skill_descriptions_for_prompt(ws)

    prompt = template.replace("{available_skills}", skill_descriptions)

    # Prepend soul as personality context
    full_prompt = f"{soul}\n\n---\n\n{prompt}"

    logger.debug(
        "system_prompt_built",
        workspace_id=ws.workspace_id,
        prompt_length=len(full_prompt),
        skill_count=skill_descriptions.count("\n- ") + (1 if skill_descriptions.startswith("- ") else 0),
    )
    return full_prompt


def reset_caches() -> None:
    """Clear cached files (useful for testing or hot-reload)."""
    global _soul_cache, _template_cache
    _soul_cache = None
    _template_cache = None
