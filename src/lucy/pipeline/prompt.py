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
_SOUL_LITE_PATH = _PROMPTS_DIR / "SOUL_LITE.md"
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
        compact: If True, use SYSTEM_CORE_COMPACT.md (~80% smaller).
                 Strips verbose examples while keeping all essential
                 instructions. Used for tool_use intents by default.
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


def _load_prompt_modules(
    names: list[str], *, compact: bool = False,
) -> str:
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


def _load_memory_context_template() -> str:
    """Load the memory context prompt module template."""
    path = _PROMPT_MODULES_DIR / "memory_context.md"
    if path and path.exists():
        return path.read_text(encoding="utf-8")
    return (
        "## What You Remember\n"
        "{memory_items}\n\n"
        "Use this context naturally in your responses. "
        "Don't explicitly reference \"remembering\" unless asked. "
        "Reference past context with phrases like \"as we discussed\" "
        "or \"building on what you mentioned\"."
    )


# Modules loaded into the static prefix for all non-chat intents.
_COMMON_MODULES = ["tool_use", "memory"]


async def _build_memory_context(
    ws: WorkspaceFS,
    *,
    user_id: str | None = None,
    thread_ts: str | None = None,
    topic_hint: str | None = None,
) -> str:
    """Build formatted memory context for prompt injection.

    Uses scored retrieval: same-thread > same-user > topic-relevant > recent.
    Returns empty string if no memories found.
    """
    try:
        from lucy.workspace.memory import load_relevant_memories

        formatted = await load_relevant_memories(
            ws,
            user_id=user_id,
            thread_ts=thread_ts,
            topic_hint=topic_hint,
        )
        if not formatted:
            return ""

        template = _load_memory_context_template()
        return template.replace("{memory_items}", formatted)
    except Exception as e:
        logger.warning("memory_context_build_failed", error=str(e))
        return ""


# ═══════════════════════════════════════════════════════════════════════════
# LIGHTWEIGHT PROMPT (for chat/lookup/confirmation — no tools needed)
# ═══════════════════════════════════════════════════════════════════════════


async def build_lightweight_prompt(
    ws: WorkspaceFS,
    *,
    user_slack_id: str | None = None,
    is_composition: bool = False,
    user_message: str | None = None,
    thread_ts: str | None = None,
) -> str:
    """Build a minimal prompt for simple conversational messages.

    This skips the full system prompt, tool documentation, and integration
    blocks. Used for greetings, simple questions, and follow-ups that
    don't need tool access.

    Typical size: ~2-4KB vs ~85KB for the full prompt.
    """
    from datetime import datetime, timedelta, timezone as tz

    if _SOUL_LITE_PATH.exists():
        soul = _SOUL_LITE_PATH.read_text(encoding="utf-8")
    else:
        # Fall back to SOUL_COMPACT rather than full SOUL to keep
        # lightweight prompts actually lightweight.
        soul = _load_soul(compact=True)

    if is_composition:
        core = (
            "The user is asking you to WRITE or DRAFT content. Focus on "
            "producing high-quality, ready-to-use text. Use the company "
            "knowledge below for context. Write in a natural, professional "
            "voice that matches the team's culture.\n\n"
            "If the user provided specific data (numbers, names, dates), "
            "use those exactly. If they didn't provide data and you don't "
            "know the real numbers, either use reasonable placeholders "
            "marked [X] or note what data you'd need.\n\n"
            "DO NOT try to send, post, or share the content anywhere. "
            "Just write it and present it in your response."
        )
    else:
        core = ""

    _utc_now = datetime.now(tz.utc)
    _ist_now = _utc_now.replace(tzinfo=None) + timedelta(hours=5, minutes=30)
    time_block = (
        f"\n\n## Current Date & Time (AUTHORITATIVE)\n"
        f"Today is {_utc_now.strftime('%A, %B %d, %Y')}.\n"
        f"Current time: {_utc_now.strftime('%I:%M %p UTC')} / "
        f"{_ist_now.strftime('%I:%M %p IST')}\n"
        f"IMPORTANT: When asked about the date, day, or time, use ONLY "
        f"the values above."
    )

    key_content = await get_key_skill_content(ws)

    memory_context = await _build_memory_context(
        ws,
        user_id=user_slack_id,
        thread_ts=thread_ts,
        topic_hint=user_message,
    )

    parts = [soul, core + time_block]
    if key_content:
        parts.append(f"<knowledge>\n{key_content}\n</knowledge>")
    if memory_context:
        parts.append(f"<memory>\n{memory_context}\n</memory>")

    prompt = _SECTION_SEP.join(p for p in parts if p.strip())
    logger.debug(
        "lightweight_prompt_built",
        workspace_id=ws.workspace_id,
        prompt_length=len(prompt),
        has_memory=bool(memory_context),
    )
    return prompt


async def build_system_prompt(
    ws: WorkspaceFS,
    connected_services: list[str] | None = None,
    user_message: str | None = None,
    prompt_modules: list[str] | None = None,
    compact: bool = False,
    user_id: str | None = None,
    thread_ts: str | None = None,
    invocation_context: dict[str, str] | None = None,
) -> str:
    """Build the complete system prompt for a workspace.

    Content is ordered static-to-dynamic for prefix caching:

    STATIC PREFIX (identical across requests per workspace):
      1. SOUL.md — personality traits and voice
      2. SYSTEM_CORE.md — core instructions (compact variant available)
      3. Common modules (tool_use + memory) for non-chat intents
      4. Connected services environment block

    DYNAMIC SUFFIX (varies per request):
      5. Intent-specific prompt modules (research, coding, etc.)
      6. Custom integrations block
      7. Skill descriptions + relevant skill content
      8. Knowledge blocks (company/team)
      9. Memory context (session memories relevant to current request)

    Args:
        compact: Use SYSTEM_CORE_COMPACT (~80% smaller). Strips verbose
                 examples while keeping essential instructions.
        user_id: Slack user ID for memory personalization.
        thread_ts: Thread timestamp for same-thread memory filtering.
        invocation_context: Dict with trigger, channel_id, user_id,
                           cron_path for trigger-aware behavior.
    """
    soul = _load_soul(compact=compact)
    system_core = _load_system_core(compact=compact)
    skill_descriptions = await get_skill_descriptions_for_prompt(ws)
    key_content = await get_key_skill_content(ws)

    # ── STATIC PREFIX ────────────────────────────────────────────
    system_core_with_skills = system_core.replace(
        "{available_skills}", skill_descriptions,
    )

    common_modules_text = _load_prompt_modules(
        _COMMON_MODULES, compact=compact,
    )

    static_parts: list[str] = [soul, system_core_with_skills]
    if common_modules_text:
        static_parts.append(common_modules_text)

    if connected_services:
        services_str = ", ".join(connected_services)
        from datetime import datetime, timedelta, timezone as tz

        _utc_now = datetime.now(tz.utc)
        _ist_now = _utc_now.replace(tzinfo=None) + timedelta(hours=5, minutes=30)
        date_str = _utc_now.strftime("%A, %B %d, %Y")
        utc_str = _utc_now.strftime("%I:%M %p UTC")
        ist_str = _ist_now.strftime("%I:%M %p IST")
        env_block = (
            "<current_environment>\n"
            f"Current date: {date_str}\n"
            f"Current time: {utc_str} / {ist_str}\n"
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
            "WHEN A USER ASKS ABOUT A SERVICE NOT IN THIS LIST:\n"
            "Do NOT just say it is not connected and list alternatives. "
            "That is a dead end and violates your high-agency principle.\n"
            "1. Use COMPOSIO_MANAGE_CONNECTIONS with "
            'toolkits: ["service_name"] to check availability and '
            "generate an auth link.\n"
            "2. If Composio supports it: share the auth link and tell "
            "the user what you will do once they connect. Example: "
            '"Notion isn\'t connected yet. Connect it here: [link]. '
            'Once you do, I\'ll pull your recent files right away."\n'
            "3. If Composio does not support it: offer to build a custom "
            'integration. Example: "Notion doesn\'t have a native '
            "integration, but I can try building a custom connection. "
            'Want me to give it a shot?"\n'
            "4. ALWAYS provide a path forward. "
            '"I don\'t have access to X" is never a complete response.\n'
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

    if connected_services:
        try:
            from lucy.workspace.identity import (
                ensure_identity,
                format_identity_for_prompt,
            )

            identity = await ensure_identity(ws, connected_services)
            identity_block = format_identity_for_prompt(identity)
            if identity_block:
                static_parts.append(identity_block)
        except Exception as exc:
            logger.debug("workspace_identity_inject_failed", error=str(exc))

    static_prefix = _SECTION_SEP.join(static_parts)

    # ── DYNAMIC SUFFIX ───────────────────────────────────────────
    dynamic_parts: list[str] = []

    if prompt_modules:
        intent_modules_text = _load_prompt_modules(
            prompt_modules, compact=compact,
        )
        if intent_modules_text:
            dynamic_parts.append(intent_modules_text)

    try:
        from lucy.integrations.wrapper_generator import discover_saved_wrappers
        custom_wrappers = discover_saved_wrappers()
    except Exception as _wrappers_err:
        logger.warning("custom_wrappers_discover_failed", error=str(_wrappers_err))
        custom_wrappers = []

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

    # ── MCP connections context ──────────────────────────────────
    try:
        from lucy.workspace.connections import load_mcp_connections
        mcp_conns = await load_mcp_connections(ws)
        if mcp_conns:
            mcp_lines = [
                "<mcp_connections>",
                "You have active MCP connections. Their tools appear in your tool list "
                "prefixed with mcp_{service}_. Call them directly for any task related "
                "to these services — do NOT use Composio or custom wrappers for them.",
            ]
            for conn in mcp_conns:
                mcp_lines.append(
                    f"- {conn.service} ({conn.tool_count} tools, "
                    f"transport={conn.transport}): use mcp_{conn.service}_* tools"
                )
            mcp_lines.append(
                "\nIf a user asks to connect a service you don't recognise, "
                "use lucy_resolve_custom_integration — many modern tools support "
                "MCP. If it does, ask for their URL and call lucy_connect_mcp."
            )
            mcp_lines.append("</mcp_connections>")
            dynamic_parts.append("\n".join(mcp_lines))
    except Exception as _mcp_ctx_err:
        logger.debug("mcp_context_inject_failed", error=str(_mcp_ctx_err))

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

    # ── Invocation context: tell the LLM how it was triggered ──────
    if invocation_context:
        ctx_lines = ["<invocation_context>"]
        if invocation_context.get("trigger"):
            ctx_lines.append(f"Trigger: {invocation_context['trigger']}")
        if invocation_context.get("channel_id"):
            ctx_lines.append(f"Channel: {invocation_context['channel_id']}")
        if invocation_context.get("user_id"):
            ctx_lines.append(f"User: {invocation_context['user_id']}")
        if invocation_context.get("cron_path"):
            ctx_lines.append(f"Cron: {invocation_context['cron_path']}")
        ctx_lines.append("</invocation_context>")
        dynamic_parts.append("\n".join(ctx_lines))

    # ── Memory context injection ─────────────────────────────────
    memory_context = await _build_memory_context(
        ws,
        user_id=user_id,
        thread_ts=thread_ts,
        topic_hint=user_message,
    )
    if memory_context:
        dynamic_parts.append(f"<memory>\n{memory_context}\n</memory>")

    _REFLECTION_SUFFIX = (
        "\n\n<response_format_requirement>\n"
        "MANDATORY: Every final response MUST start with a "
        "<lucy_reflection> block (stripped before delivery). Example:\n\n"
        "<lucy_reflection>\n"
        "HELPFUL: yes\n"
        "VALUE_FIRST: yes\n"
        "ACCURATE: yes\n"
        "COMPLETE: yes\n"
        "PERSONALIZED: yes\n"
        "INSIGHT_BEYOND_QUESTION: yes\n"
        "CONFIDENCE: 8\n"
        "WEAKNESS: Could add week-over-week comparison.\n"
        "</lucy_reflection>\n\n"
        "Then your actual response after the closing tag. "
        "This is not optional. Include it on EVERY response.\n"
        "</response_format_requirement>"
    )

    if dynamic_parts:
        full_prompt = (
            static_prefix + _SECTION_SEP
            + "\n\n".join(dynamic_parts)
            + _REFLECTION_SUFFIX
        )
    else:
        full_prompt = static_prefix + _REFLECTION_SUFFIX

    logger.debug(
        "system_prompt_built",
        workspace_id=ws.workspace_id,
        prompt_length=len(full_prompt),
        static_prefix_length=len(static_prefix),
        compact=compact,
        connected_services=connected_services or [],
        has_relevant_skills=bool(relevant_skills),
        has_memory=bool(memory_context),
        prompt_modules=prompt_modules or [],
    )
    return full_prompt
