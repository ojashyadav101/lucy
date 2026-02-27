"""System prompt builder.

Assembles the system prompt from layered components. Content is ordered
static-to-dynamic so that LLM providers with automatic prefix caching
(Gemini, DeepSeek, Kimi) get maximum cache hits.

Order:
  STATIC PREFIX  — SOUL.md + SYSTEM_CORE.md + common modules + env block
  ─── cache boundary ───
  DYNAMIC SUFFIX — intent modules, custom integrations, skills, knowledge
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
_SYSTEM_CORE_PATH = _ASSETS_DIR / "SYSTEM_CORE.md"
_PROMPT_MODULES_DIR = _ASSETS_DIR / "prompt_modules"

# Separator used between prompt sections
_SECTION_SEP = "\n\n---\n\n"


def _load_soul() -> str:
    if _SOUL_PATH.exists():
        return _SOUL_PATH.read_text(encoding="utf-8")
    return (
        "You are Lucy, an AI coworker. Direct, helpful, gets things done. "
        "Lives in Slack with access to tools and integrations."
    )


def _load_system_core() -> str:
    """Load SYSTEM_CORE.md (Phase 2) or fall back to full SYSTEM_PROMPT.md."""
    if _SYSTEM_CORE_PATH.exists():
        return _SYSTEM_CORE_PATH.read_text(encoding="utf-8")
    if _PROMPT_TEMPLATE_PATH.exists():
        return _PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")
    return (
        "You are Lucy, an AI coworker in Slack.\n\n"
        "<available_skills>\n{available_skills}\n</available_skills>"
    )


def _load_prompt_modules(names: list[str]) -> str:
    """Load and concatenate prompt module files by name."""
    if not _PROMPT_MODULES_DIR.exists():
        return ""
    parts: list[str] = []
    for name in names:
        path = _PROMPT_MODULES_DIR / f"{name}.md"
        if path.exists():
            parts.append(path.read_text(encoding="utf-8"))
    return "\n\n".join(parts)


# Modules loaded into the static prefix for all non-chat intents.
_COMMON_MODULES = ["tool_use", "memory"]


async def build_system_prompt(
    ws: WorkspaceFS,
    connected_services: list[str] | None = None,
    user_message: str | None = None,
    prompt_modules: list[str] | None = None,
) -> str:
    """Build the complete system prompt for a workspace.

    Content is ordered static-to-dynamic for prefix caching:

    STATIC PREFIX (identical across requests per workspace):
      1. SOUL.md — personality traits and voice
      2. SYSTEM_CORE.md — core instructions
      3. Common modules (tool_use + memory) for non-chat intents
      4. Connected services environment block

    DYNAMIC SUFFIX (varies per request):
      5. Intent-specific prompt modules (research, coding, etc.)
      6. Custom integrations block
      7. Skill descriptions + relevant skill content
      8. Knowledge blocks (company/team)

    Session memory is NOT included here — it is injected via
    _preflight_context() as a late system message to preserve
    the cacheable prefix.
    """
    soul = _load_soul()
    system_core = _load_system_core()
    skill_descriptions = await get_skill_descriptions_for_prompt(ws)
    key_content = await get_key_skill_content(ws)

    # ── STATIC PREFIX ────────────────────────────────────────────
    system_core_with_skills = system_core.replace(
        "{available_skills}", skill_descriptions,
    )

    common_modules_text = _load_prompt_modules(_COMMON_MODULES)

    static_parts: list[str] = [soul, system_core_with_skills]
    if common_modules_text:
        static_parts.append(common_modules_text)

    if connected_services:
        services_str = ", ".join(connected_services)
        env_block = (
            "<current_environment>\n"
            "You are communicating via: Slack (already connected and authenticated)\n"
            f"Connected integrations: {services_str}\n"
            "DO NOT ask users to connect any of these — they are already active.\n"
            "You are ON Slack — never suggest 'connecting to Slack'.\n"
            "When a user asks what integrations are available, list ONLY these.\n"
            "</current_environment>"
        )
        static_parts.append(env_block)

    if settings.agentmail_enabled and settings.agentmail_api_key:
        email_addr = f"lucy@{settings.agentmail_domain}"
        static_parts.append(
            "<email_identity>\n"
            f"You have your own email address: {email_addr}\n"
            "This is YOUR email, not the user's. You can send emails, "
            "read your inbox, reply to threads, and search messages using "
            "the lucy_send_email, lucy_read_emails, lucy_reply_to_email, "
            "lucy_search_emails, and lucy_get_email_thread tools.\n"
            "Use it for outbound communication, notifications, agent-to-agent "
            "messaging, or any task where you need your own email identity.\n"
            "When you receive an inbound email, you will be notified.\n"
            "</email_identity>"
        )

    if settings.spaces_enabled:
        static_parts.append(
            "<spaces_capability>\n"
            "You can build and deploy web apps on zeeya.app.\n"
            "WORKFLOW (exactly 3 steps):\n"
            "1. lucy_spaces_init → scaffolds React project, returns sandbox_path\n"
            "2. lucy_write_file → write your app code to {sandbox_path}/src/App.tsx\n"
            "3. lucy_spaces_deploy → auto-builds, deploys, validates, returns live URL\n"
            "\n"
            "WRITING App.tsx — CRITICAL:\n"
            "- Write ALL component code INLINE in App.tsx as a single file.\n"
            "- Do NOT import from ./components/ or ./contexts/ — those paths may not exist.\n"
            "- You CAN import from these pre-installed libraries:\n"
            "  • shadcn/ui: import { Button } from '@/components/ui/button', etc. "
            "(53 components available)\n"
            "  • lucide-react: icons\n"
            "  • framer-motion: animations\n"
            "  • recharts: charts\n"
            "  • react, react-dom, react-router-dom\n"
            "  • Tailwind CSS classes in className\n"
            "- Keep it self-contained. Define all state, helpers, and sub-components "
            "in the same file.\n"
            "- Export default: export default function App() { ... }\n"
            "\n"
            "AFTER DEPLOY:\n"
            "- The deploy tool validates the app loads before returning.\n"
            "- If it returns a validation_warning, the app has issues — investigate.\n"
            "- Share the EXACT url from the result, including query strings.\n"
            "- Do NOT dump raw JSON. Summarize in natural language.\n"
            "- NEVER use COMPOSIO tools for spaces.\n"
            "</spaces_capability>"
        )

    static_prefix = _SECTION_SEP.join(static_parts)

    # ── DYNAMIC SUFFIX ───────────────────────────────────────────
    dynamic_parts: list[str] = []

    if prompt_modules:
        intent_modules_text = _load_prompt_modules(prompt_modules)
        if intent_modules_text:
            dynamic_parts.append(intent_modules_text)

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
            "<custom_integrations>",
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
                f"- {svc} [{status}]: use lucy_custom_{slug}_* tools "
                f"({tools_list})"
            )
        lines.append("</custom_integrations>")
        dynamic_parts.append("\n".join(lines))

    relevant_skills = ""
    if user_message:
        relevant_skills = await load_relevant_skill_content(ws, user_message)
        if relevant_skills:
            dynamic_parts.append(
                "<relevant_skill_details>\n"
                "The following skill details are relevant to the current "
                "request. Use these implementation details, code patterns, "
                "and best practices to deliver high-quality output.\n\n"
                f"{relevant_skills}\n"
                "</relevant_skill_details>"
            )

    if key_content:
        dynamic_parts.append(f"<knowledge>\n{key_content}\n</knowledge>")

    if dynamic_parts:
        full_prompt = static_prefix + _SECTION_SEP + "\n\n".join(dynamic_parts)
    else:
        full_prompt = static_prefix

    logger.debug(
        "system_prompt_built",
        workspace_id=ws.workspace_id,
        prompt_length=len(full_prompt),
        static_prefix_length=len(static_prefix),
        connected_services=connected_services or [],
        has_relevant_skills=bool(relevant_skills),
        prompt_modules=prompt_modules or [],
    )
    return full_prompt


def reset_caches() -> None:
    """No-op. Prompt files are re-read on every call now."""
