"""System prompt builder.

Combines SOUL.md (personality), SYSTEM_PROMPT.md (instructions),
and dynamic skill descriptions into the final system message.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog

from lucy.config import settings
from lucy.workspace.filesystem import WorkspaceFS
from lucy.workspace.skills import (
    get_key_skill_content,
    get_skill_descriptions_for_prompt,
    load_relevant_skill_content,
)

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
    user_message: str | None = None,
) -> str:
    """Build the complete system prompt for a workspace.

    Combines:
    1. SOUL.md — personality traits and voice
    2. SYSTEM_PROMPT.md — structured instructions with {available_skills} placeholder
    3. Dynamic skill descriptions from the workspace
    4. Full content of skills relevant to the user's message
    5. Connected services environment block (runtime)
    6. Team/company knowledge
    """
    soul = _load_soul()
    template = _load_template()
    skill_descriptions = await get_skill_descriptions_for_prompt(ws)
    key_content = await get_key_skill_content(ws)

    prompt = template.replace("{available_skills}", skill_descriptions)

    full_prompt = f"{soul}\n\n---\n\n{prompt}"

    relevant_skills = ""
    if user_message:
        relevant_skills = await load_relevant_skill_content(ws, user_message)
        if relevant_skills:
            full_prompt += (
                "\n\n<relevant_skill_details>\n"
                "The following skill details are relevant to the current request. "
                "Use these implementation details, code patterns, and best practices "
                "to deliver high-quality output.\n\n"
                f"{relevant_skills}\n"
                "</relevant_skill_details>"
            )

    if key_content:
        full_prompt += f"\n\n<knowledge>\n{key_content}\n</knowledge>"

    from lucy.workspace.memory import get_session_context_for_prompt
    session_ctx = await get_session_context_for_prompt(ws)
    if session_ctx:
        full_prompt += f"\n\n<session_memory>\n{session_ctx}\n</session_memory>"

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

    from lucy.integrations.wrapper_generator import discover_saved_wrappers
    custom_wrappers = discover_saved_wrappers()

    keys_path = Path(settings.workspace_root).parent / "keys.json"
    keys_data: dict[str, Any] = {}
    if keys_path.exists():
        try:
            keys_data = json.loads(keys_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    if custom_wrappers:
        lines = [
            "\n\n<custom_integrations>",
            "IMPORTANT: You have built the following custom integrations. "
            "Their tools are in your tool list prefixed with lucy_custom_. "
            "When a user asks about one of these services, call the "
            "lucy_custom_* tools directly. Do NOT use COMPOSIO_MULTI_EXECUTE_TOOL "
            "or COMPOSIO_MANAGE_CONNECTIONS for these services — Composio does not "
            "know about them. Use the lucy_custom_* tools instead.",
        ]
        for w in custom_wrappers:
            svc = w.get("service_name", w.get("slug", "unknown"))
            slug = w.get("slug", "")
            n = w.get("total_tools", 0)
            tool_samples = w.get("tools", [])[:8]
            tools_list = ", ".join(tool_samples)
            if n > len(tool_samples):
                tools_list += f", ... ({n} total)"

            ci_keys = keys_data.get("custom_integrations", {}).get(slug, {})
            key_stored = bool(ci_keys.get("api_key"))
            status = "READY" if key_stored else "needs API key"
            lines.append(
                f"- {svc} [{status}]: use lucy_custom_{slug}_* tools ({tools_list})"
            )
        lines.append("</custom_integrations>")
        full_prompt += "\n".join(lines)

    logger.debug(
        "system_prompt_built",
        workspace_id=ws.workspace_id,
        prompt_length=len(full_prompt),
        connected_services=connected_services or [],
        has_relevant_skills=bool(relevant_skills),
    )
    return full_prompt


def reset_caches() -> None:
    """No-op. Prompt files are re-read on every call now."""
