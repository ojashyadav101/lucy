"""System prompt builder.

Combines SOUL.md (personality), SYSTEM_PROMPT.md (instructions),
and dynamic skill descriptions into the final system message.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from lucy.workspace.filesystem import WorkspaceFS
from lucy.workspace.skills import get_key_skill_content, get_skill_descriptions_for_prompt

logger = structlog.get_logger()

_ASSETS_DIR = Path(__file__).parent.parent.parent.parent / "assets"
_SOUL_PATH = _ASSETS_DIR / "SOUL.md"
_PROMPT_TEMPLATE_PATH = _ASSETS_DIR / "SYSTEM_PROMPT.md"

def _load_soul() -> str:
    if _SOUL_PATH.exists():
        return _SOUL_PATH.read_text(encoding="utf-8")
    return (
        "You are Lucy, an AI coworker. Direct, helpful, gets things done. "
        "Lives in Slack with access to tools and integrations."
    )


def _load_template() -> str:
    if _PROMPT_TEMPLATE_PATH.exists():
        return _PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")
    return (
        "You are Lucy, an AI coworker in Slack.\n\n"
        "<available_skills>\n{available_skills}\n</available_skills>"
    )


async def build_system_prompt(
    ws: WorkspaceFS,
    connected_services: list[str] | None = None,
) -> str:
    """Build the complete system prompt for a workspace.

    Combines:
    1. SOUL.md — personality traits and voice
    2. SYSTEM_PROMPT.md — structured instructions with {available_skills} placeholder
    3. Dynamic skill descriptions from the workspace
    4. Connected services environment block (runtime)
    5. Team/company knowledge
    """
    soul = _load_soul()
    template = _load_template()
    skill_descriptions = await get_skill_descriptions_for_prompt(ws)
    key_content = await get_key_skill_content(ws)

    prompt = template.replace("{available_skills}", skill_descriptions)

    full_prompt = f"{soul}\n\n---\n\n{prompt}"

    if key_content:
        full_prompt += f"\n\n<knowledge>\n{key_content}\n</knowledge>"

    if connected_services:
        services_str = ", ".join(connected_services)
        env_block = (
            "\n\n<current_environment>\n"
            "You are communicating via: Slack (already connected and authenticated)\n"
            f"Connected integrations: {services_str}\n"
            f"DO NOT ask users to connect any of these — they are already active.\n"
            "You are ON Slack — never suggest 'connecting to Slack'.\n"
            "When a user asks what integrations are available, list ONLY these.\n"
            "</current_environment>"
        )
        full_prompt += env_block

    logger.debug(
        "system_prompt_built",
        workspace_id=ws.workspace_id,
        prompt_length=len(full_prompt),
        connected_services=connected_services or [],
    )
    return full_prompt


def reset_caches() -> None:
    """No-op. Prompt files are re-read on every call now."""
