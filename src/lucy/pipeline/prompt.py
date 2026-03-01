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

_PROMPTS_DIR = Path(__file__).parent.parent.parent.parent / "prompts"
_SOUL_PATH = _PROMPTS_DIR / "SOUL.md"
_SOUL_COMPACT_PATH = _PROMPTS_DIR / "SOUL_COMPACT.md"
_PROMPT_TEMPLATE_PATH = _PROMPTS_DIR / "SYSTEM_PROMPT.md"
_SYSTEM_CORE_PATH = _PROMPTS_DIR / "SYSTEM_CORE.md"
_SYSTEM_CORE_COMPACT_PATH = _PROMPTS_DIR / "SYSTEM_CORE_COMPACT.md"
_PROMPT_MODULES_DIR = _PROMPTS_DIR / "modules"

# Separator used between prompt sections
_SECTION_SEP = "\n\n---\n\n"


def _load_soul(*, compact: bool = False) -> str:
    if compact and _SOUL_COMPACT_PATH.exists():
        return _SOUL_COMPACT_PATH.read_text(encoding="utf-8")
    if _SOUL_PATH.exists():
        return _SOUL_PATH.read_text(encoding="utf-8")
    return (
        "You are Lucy, an AI coworker. Direct, helpful, gets things done. "
        "Lives in Slack with access to tools and integrations."
    )


def _load_system_core(*, compact: bool = False) -> str:
    """Load system core prompt.

    Args:
        compact: If True, use SYSTEM_CORE_COMPACT.md (83% smaller).
                 The compact version strips verbose examples and redundant
                 formatting guides while keeping all essential instructions.
                 Used by default for tool_use to reduce token cost.
    """
    if compact and _SYSTEM_CORE_COMPACT_PATH.exists():
        return _SYSTEM_CORE_COMPACT_PATH.read_text(encoding="utf-8")
    if _SYSTEM_CORE_PATH.exists():
        return _SYSTEM_CORE_PATH.read_text(encoding="utf-8")
    if _PROMPT_TEMPLATE_PATH.exists():
        return _PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")
    return (
        "You are Lucy, an AI coworker in Slack.\n\n"
        "<available_skills>\n{available_skills}\n</available_skills>"
    )


def _load_prompt_modules(names: list[str], *, compact: bool = False) -> str:
    """Load and concatenate prompt module files by name.

    When *compact* is True, tries ``{name}_compact.md`` first, falling
    back to ``{name}.md`` if no compact version exists.
    """
    if not _PROMPT_MODULES_DIR.exists():
        return ""
    parts: list[str] = []
    for name in names:
        if compact:
            compact_path = _PROMPT_MODULES_DIR / f"{name}_compact.md"
            if compact_path.exists():
                parts.append(compact_path.read_text(encoding="utf-8"))
                continue
        path = _PROMPT_MODULES_DIR / f"{name}.md"
        if path.exists():
            parts.append(path.read_text(encoding="utf-8"))
    return "\n\n".join(parts)


# Modules loaded into the static prefix for all non-chat intents.
_COMMON_MODULES = ["tool_use", "memory"]




# ═══════════════════════════════════════════════════════════════════════════
# LIGHTWEIGHT PROMPT (for chat/lookup/confirmation — no tools needed)
# ═══════════════════════════════════════════════════════════════════════════

_SOUL_LITE_PATH = _PROMPTS_DIR / "SOUL_LITE.md"

async def build_lightweight_prompt(
    ws: WorkspaceFS,
    *,
    user_slack_id: str | None = None,
    workspace_id: str | None = None,
) -> str:
    """Build a minimal prompt for simple conversational messages.

    This skips the full 85KB system prompt, tool documentation, and
    integration blocks. Used for greetings, simple questions, and
    follow-ups that don't need tool access.

    Typical size: ~2-4KB vs ~85KB for the full prompt.
    """
    from datetime import datetime, timezone

    # Use SOUL_LITE if available, otherwise extract key personality from SOUL
    if _SOUL_LITE_PATH.exists():
        soul = _SOUL_LITE_PATH.read_text(encoding="utf-8")
    else:
        soul = _load_soul()

    # Minimal instructions
    core = (
        "You are Lucy, an AI coworker in Slack. You're direct, warm, "
        "and helpful. Keep responses concise and natural — like a smart "
        "colleague, not a customer service bot.\n\n"
        "For this message, you're having a casual conversation. No tools "
        "are needed. Just respond naturally.\n\n"
        "If the user is asking something that actually requires tools, "
        "data access, or complex work, tell them you can help and ask "
        "them to elaborate so you can assist properly."
    )

    # Current date/time — critical for "what day is it?" type questions
    # NOTE: Must be VERY prominent. Models sometimes hallucinate dates
    # from training data if the injection is subtle.
    utc_now = datetime.now(timezone.utc)
    time_block = (
        f"\n\n## Current Date & Time (AUTHORITATIVE — use this exactly)\n"
        f"Today is {utc_now.strftime('%A, %B %d, %Y')}.\n"
        f"Current time: {utc_now.strftime('%H:%M UTC')}\n"
        f"IMPORTANT: When asked about the date, day, or time, use ONLY "
        f"the values above. Do NOT calculate or guess dates."
    )

    # Try to get user's local timezone
    if user_slack_id and workspace_id:
        try:
            from lucy.workspace.timezone import (
                get_user_local_time,
                get_user_timezone_name,
            )
            tz_name = await get_user_timezone_name(workspace_id, user_slack_id)
            local_time = await get_user_local_time(workspace_id, user_slack_id)
            if local_time and tz_name:
                time_block = (
                    f"\n\n## Current Date & Time (AUTHORITATIVE — use this exactly)\n"
                    f"Today is {utc_now.strftime('%A, %B %d, %Y')}.\n"
                    f"UTC: {utc_now.strftime('%H:%M UTC')}\n"
                    f"User's local time ({tz_name}): "
                    f"{local_time.strftime('%A, %B %d, %Y %H:%M')}\n"
                    f"Respond with times in the user's timezone ({tz_name}).\n"
                    f"IMPORTANT: When asked about the date, day, or time, use ONLY "
                    f"the values above. Do NOT calculate or guess dates."
                )
        except Exception:
            pass  # Fall back to UTC only

    # Add basic workspace context if available
    key_content = await get_key_skill_content(ws)

    parts = [soul, core + time_block]
    if key_content:
        parts.append(f"<knowledge>\n{key_content}\n</knowledge>")

    prompt = "\n\n---\n\n".join(parts)
    logger.debug(
        "lightweight_prompt_built",
        workspace_id=ws.workspace_id,
        prompt_length=len(prompt),
    )
    return prompt


async def build_system_prompt(
    ws: WorkspaceFS,
    connected_services: list[str] | None = None,
    user_message: str | None = None,
    prompt_modules: list[str] | None = None,
    compact: bool = True,
) -> str:
    """Build the complete system prompt for a workspace.

    Content is ordered static-to-dynamic for prefix caching:

    STATIC PREFIX (identical across requests per workspace):
      1. SOUL.md — personality traits and voice
      2. SYSTEM_CORE.md — core instructions (compact by default)
      3. Common modules (tool_use + memory) for non-chat intents
      4. Connected services environment block

    DYNAMIC SUFFIX (varies per request):
      5. Intent-specific prompt modules (research, coding, etc.)
      6. Custom integrations block
      7. Skill descriptions + relevant skill content
      8. Knowledge blocks (company/team)

    Args:
        compact: Use SYSTEM_CORE_COMPACT (8KB vs 48KB). Strips verbose
                 examples while keeping all essential instructions.
                 Default True. Set False for research/document intents
                 where the extra formatting guidance helps.
    """
    soul = _load_soul(compact=compact)
    system_core = _load_system_core(compact=compact)
    skill_descriptions = await get_skill_descriptions_for_prompt(ws)
    key_content = await get_key_skill_content(ws)

    # ── STATIC PREFIX ────────────────────────────────────────────
    system_core_with_skills = system_core.replace(
        "{available_skills}", skill_descriptions,
    )

    common_modules_text = _load_prompt_modules(_COMMON_MODULES, compact=compact)

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
            "\n"
            "CRITICAL — Integration listing rules:\n"
            "• When a user asks what integrations they have, answer from THIS "
            "list. This is the authoritative source of truth.\n"
            "• Do NOT call COMPOSIO_MANAGE_CONNECTIONS to verify or list "
            "connected integrations. That tool only sees OAuth connections and "
            "misses custom integrations (Polar.sh, Clerk, etc.).\n"
            "• Only use COMPOSIO_MANAGE_CONNECTIONS when you need to CREATE a "
            "new connection or check a SPECIFIC service's auth status.\n"
            "• Always include BOTH Composio-managed AND custom integrations "
            "(lucy_custom_* tools) in your answer.\n"
            "\n"
            "Using Composio integrations (Google Meet, Gmail, Google Drive, etc.):\n"
            "• These services are ALREADY CONNECTED. Do NOT offer connection links.\n"
            "• To use them: call COMPOSIO_SEARCH_TOOLS to find the right action, "
            "then call COMPOSIO_MULTI_EXECUTE_TOOL with the action name and params.\n"
            "• For calendar/meetings: call COMPOSIO_SEARCH_TOOLS with use_case "
            "'list calendar events for today' (app='googlemeet'). Then execute "
            "the found action via COMPOSIO_MULTI_EXECUTE_TOOL. Do NOT narrate — "
            "search, execute, return results.\n"
            "• For email: use the built-in lucy_send_email / lucy_read_emails tools.\n"
            "• NEVER respond saying a connected integration is 'not connected'.\n"
            "• After calling COMPOSIO_SEARCH_TOOLS, ALWAYS follow up by calling "
            "COMPOSIO_MULTI_EXECUTE_TOOL — never stop after just searching.\n"
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
        intent_modules_text = _load_prompt_modules(prompt_modules, compact=compact)
        if intent_modules_text:
            dynamic_parts.append(intent_modules_text)

    from lucy.integrations.wrapper_generator import discover_saved_wrappers
    custom_wrappers = discover_saved_wrappers()

    keys_path = Path(settings.workspace_root).parent / "keys.json"
    keys_data: dict[str, Any] = {}
    if keys_path.exists():
        try:
            keys_data = json.loads(keys_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("keys_file_read_failed", error=str(e))

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
        compact=compact,
        connected_services=connected_services or [],
        has_relevant_skills=bool(relevant_skills),
        prompt_modules=prompt_modules or [],
    )
    return full_prompt
