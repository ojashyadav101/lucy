"""LucyAgent — core orchestrator.

Flow:
1. Ensure workspace exists (onboard if first message)
2. Classify intent → select model (fast rule-based router)
3. Read relevant skills → build system prompt
4. Get Composio meta-tools (5 tools)
5. Build conversation from Slack thread history
6. Multi-turn LLM loop: call OpenRouter → execute tool calls → repeat
7. Send response to Slack
8. Write trace log
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from lucy.config import settings
from lucy.core.openclaw import (
    ChatConfig,
    OpenClawClient,
    OpenClawError,
    OpenClawResponse,
    get_openclaw_client,
)
from lucy.infra.trace import Trace

logger = structlog.get_logger()

MAX_TOOL_TURNS = 50  # Soft limit — supervisor is the real governor
MAX_CONTEXT_MESSAGES = 80
TOOL_RESULT_MAX_CHARS = 50_000
TOOL_RESULT_SUMMARY_THRESHOLD = 24_000
ABSOLUTE_MAX_SECONDS = 14_400  # 4-hour catastrophic safety net (supervisor governs real duration)
MAX_PAYLOAD_CHARS = 120_000

_INTERNAL_PATH_RE = re.compile(r"/home/user/[^\s\"',}\]]+")
_WORKSPACE_PATH_RE = re.compile(r"workspaces?/[^\s\"',}\]]+")
_COMPOSIO_NAME_RE = re.compile(r"COMPOSIO_\w+")
_AUTH_URL_RE = re.compile(
    r"https?://(?:connect|auth|app)\.composio\.dev/[^\s\"',}\]]+",
)

_DELEGATION_DESCRIPTIONS: dict[str, str] = {
    "research": (
        "Delegate research, analysis, or information gathering to a "
        "specialist. Use for web research, competitive analysis, market "
        "research, or deep dives that require multiple searches. Returns "
        "structured findings."
    ),
    "code": (
        "Delegate code writing, debugging, or modification to a code "
        "specialist. Use for writing scripts, fixing bugs, creating "
        "applications, or any programming task. Returns working code."
    ),
    "integrations": (
        "Delegate service connection tasks to an integrations specialist. "
        "Use when a user needs to connect a new service, troubleshoot "
        "connections, or build custom integrations. Returns connection "
        "status."
    ),
    "document": (
        "Delegate document creation to a specialist for professional, "
        "client-facing content. Use for PDFs, reports, spreadsheets, "
        "presentations, or any formatted document. Returns the completed "
        "document."
    ),
}

_NARRATION_RE = re.compile(
    r"(?:I'll (?:start|begin|proceed|go ahead|check|look|search|get|fetch|compile|find|now)"
    r"|I will (?:start|begin|proceed|check|look|search|get|fetch|compile|find|now)"
    r"|Let me (?:start|begin|check|look|search|get|fetch|find|now|first)"
    r"|Let's (?:start|begin|figure|check|look|search|get|find|now|first)"
    r"|I'll also (?:need to|check|look|search)"
    r"|I need to (?:first|check|look|search|get|find)"
    r"|I can (?:find|check|look|search|get|fetch)"
    r"|then I (?:can|will|'ll)\b"
    r"|I'm going to (?:start|check|look|search|get|find|fetch))",
    re.IGNORECASE,
)


_CONTROL_TOKEN_RE = re.compile(
    r"<\|[a-z_]+\|>"
    r"|<\|tool_call[^>]*\|>"
    r"|<\|tool_calls_section[^>]*\|>"
    r"|<\|im_[a-z]+\|>"
    r"|<\|end\|>"
    r"|<\|pad\|>"
    r"|<\|assistant\|>"
    r"|<\|user\|>"
    r"|<\|system\|>",
)

_TOOL_CALL_BLOCK_RE = re.compile(
    r"<\|tool_calls_section_begin\|>.*?(?:<\|tool_calls_section_end\|>|$)",
    re.DOTALL,
)


def _strip_control_tokens(text: str) -> str:
    """Remove raw model control tokens that leaked into output."""
    text = _TOOL_CALL_BLOCK_RE.sub("", text)
    text = _CONTROL_TOKEN_RE.sub("", text)
    return text.strip()


def _sanitize_tool_output(text: str) -> str:
    """Remove internal file paths and tool names from tool output."""
    text = _INTERNAL_PATH_RE.sub("[file]", text)
    text = _WORKSPACE_PATH_RE.sub("[workspace]", text)
    text = _COMPOSIO_NAME_RE.sub("[action]", text)
    return text


_NOISY_KEYS = frozenset({
    "public_metadata", "private_metadata", "unsafe_metadata",
    "external_accounts", "phone_numbers", "web3_wallets",
    "saml_accounts", "passkeys", "totp_enabled",
    "backup_code_enabled", "two_factor_enabled",
    "create_organization_enabled", "delete_self_enabled",
    "legal_accepted_at", "last_active_at",
    "profile_image_url", "image_url", "has_image",
    "updated_at", "last_sign_in_at", "object",
    "verification", "linked_to", "reserved",
})


def _compact_data(data: Any, depth: int = 0) -> Any:
    """Strip verbose/noisy fields from API results to fit more
    useful data within the context limit.  Operates recursively
    on dicts and lists up to depth 4.
    """
    if depth > 4:
        return data
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            if k in _NOISY_KEYS:
                continue
            out[k] = _compact_data(v, depth + 1)
        return out
    if isinstance(data, list):
        return [_compact_data(item, depth + 1) for item in data]
    return data


@dataclass
class AgentContext:
    """Lightweight context for an agent run."""

    workspace_id: str
    channel_id: str | None = None
    thread_ts: str | None = None
    user_name: str | None = None
    user_slack_id: str | None = None
    team_id: str | None = None
    is_cron_execution: bool = False
    budget_remaining_s: float = 14400.0
    budget_start_time: float = 0.0


def _check_budget(ctx: AgentContext) -> float:
    """Return remaining budget in seconds. Raises if exhausted."""
    elapsed = time.monotonic() - ctx.budget_start_time
    remaining = ctx.budget_remaining_s - elapsed
    if remaining <= 0:
        raise asyncio.TimeoutError("Request budget exhausted")
    return remaining


class LucyAgent:
    """Lean agent: classify → read skills → prompt → LLM + meta-tools → respond."""

    def __init__(self, openclaw: OpenClawClient | None = None) -> None:
        self.openclaw = openclaw
        self._recent_tool_calls: list[tuple[str, dict, float]] = []

    async def _get_client(self) -> OpenClawClient:
        if self.openclaw is None:
            self.openclaw = await get_openclaw_client()
        return self.openclaw

    # ── Public entry point ──────────────────────────────────────────────

    async def run(
        self,
        message: str,
        ctx: AgentContext,
        slack_client: Any | None = None,
        model_override: str | None = None,
        failure_context: str | None = None,
        _retry_depth: int = 0,
    ) -> str:
        """Run the full agent loop and return the final response text."""
        ctx.budget_start_time = time.monotonic()
        ctx.budget_remaining_s = ABSOLUTE_MAX_SECONDS
        self._capped_tools = set()

        self._current_slack_client = slack_client
        self._current_channel_id = ctx.channel_id
        self._current_thread_ts = ctx.thread_ts
        self._current_user_slack_id = ctx.user_slack_id
        self._uploaded_files: set[str] = set()

        trace = Trace.start()
        trace.user_message = message

        # 1. Classify intent and select model
        from lucy.pipeline.router import classify_and_route

        thread_depth = 0
        prev_had_tool_calls = False
        if ctx.thread_ts and ctx.channel_id and slack_client:
            try:
                result = await slack_client.conversations_replies(
                    channel=ctx.channel_id, ts=ctx.thread_ts, limit=50,
                )
                thread_msgs = result.get("messages", [])
                thread_depth = len(thread_msgs)
                for msg in reversed(thread_msgs):
                    if msg.get("bot_id") and msg.get("text", ""):
                        bot_text = msg.get("text", "").lower()
                        if any(kw in bot_text for kw in [
                            "working on", "checking", "looking into",
                            "i'll", "let me", "pulling", "found",
                        ]):
                            prev_had_tool_calls = True
                        break
            except Exception as e:
                logger.warning("thread_depth_fetch_failed", component="thread_depth_detection", error=str(e))

        async with trace.span("classify_route"):
            route = classify_and_route(message, thread_depth, prev_had_tool_calls)
            model = model_override or route.model
            trace.model_used = model
            trace.intent = route.intent

        logger.info(
            "model_routed",
            intent=route.intent,
            model=model,
            thread_depth=thread_depth,
            workspace_id=ctx.workspace_id,
        )

        # 2. Ensure workspace (onboard if new)
        from lucy.workspace.onboarding import ensure_workspace

        async with trace.span("ensure_workspace"):
            ws = await ensure_workspace(ctx.workspace_id, slack_client)

        # 3. Fetch connected services + meta-tools in parallel
        from lucy.pipeline.prompt import build_system_prompt

        connected_services: list[str] = []
        tools: list[dict[str, Any]] = []

        async with trace.span("fetch_tools_and_connections"):
            tools_coro = self._get_meta_tools(ctx.workspace_id)
            connections_coro = self._get_connected_services(ctx.workspace_id)
            cached_tools, connected_services = await asyncio.gather(
                tools_coro, connections_coro,
            )
            tools = list(cached_tools)  # copy — never mutate the cache

            # Inject internal tools (Slack history search + file generation)
            from lucy.workspace.history_search import get_history_tool_definitions
            tools.extend(get_history_tool_definitions())

            from lucy.tools.file_generator import get_file_tool_definitions
            tools.extend(get_file_tool_definitions())

            from lucy.tools.email_tools import get_email_tool_definitions
            if settings.agentmail_enabled and settings.agentmail_api_key:
                tools.extend(get_email_tool_definitions())

            from lucy.tools.spaces import get_spaces_tool_definitions
            if settings.spaces_enabled:
                tools.extend(get_spaces_tool_definitions())

            from lucy.tools.web_search import get_web_search_tool_definitions
            tools.extend(get_web_search_tool_definitions())

            from lucy.tools.services import get_services_tool_definitions
            if settings.openclaw_base_url and settings.openclaw_api_key:
                tools.extend(get_services_tool_definitions())

            tools.append({
                "type": "function",
                "function": {
                    "name": "lucy_list_crons",
                    "description": (
                        "List all scheduled tasks (cron jobs) for this "
                        "workspace. Returns the name, schedule, description, "
                        "and next run time for each active recurring task."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {},
                    },
                },
            })

            tools.append({
                "type": "function",
                "function": {
                    "name": "lucy_create_cron",
                    "description": (
                        "Create a new scheduled recurring task (cron job). "
                        "The task will run on a schedule and its result is "
                        "automatically delivered to Slack. By default it "
                        "posts to the current channel. Set delivery_mode "
                        "to 'dm' for personal reminders (DMs the user who "
                        "asked for it). Use standard 5-field cron expressions "
                        "(minute hour day month weekday). "
                        "Common examples: '0 9 * * 1-5' (weekdays 9am), "
                        "'*/30 * * * *' (every 30 min), '0 */2 * * *' (every 2h). "
                        "Write the description as what the task should PRODUCE "
                        "or CHECK, not 'send a message'. Delivery is automatic."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": (
                                    "Short slug name for the cron "
                                    "(e.g. 'stock-checker', 'daily-report')"
                                ),
                            },
                            "cron_expression": {
                                "type": "string",
                                "description": (
                                    "Standard 5-field cron expression "
                                    "(minute hour day-of-month month day-of-week)"
                                ),
                            },
                            "title": {
                                "type": "string",
                                "description": "Human-readable title for the task",
                            },
                            "description": {
                                "type": "string",
                                "description": (
                                    "Detailed instructions for what the task "
                                    "should PRODUCE each time it runs. Write "
                                    "this as the task itself, not as 'send a "
                                    "message'. Be specific: include data "
                                    "sources, output format, and conditions "
                                    "to skip (return SKIP if nothing to report)."
                                ),
                            },
                            "timezone": {
                                "type": "string",
                                "description": (
                                    "IANA timezone for schedule evaluation "
                                    "(e.g. 'Asia/Kolkata', 'America/New_York'). "
                                    "If omitted, uses server timezone."
                                ),
                            },
                            "delivery_mode": {
                                "type": "string",
                                "enum": ["channel", "dm"],
                                "description": (
                                    "Where to deliver results. 'channel' posts "
                                    "to the channel where it was created "
                                    "(default). 'dm' sends a direct message "
                                    "to the user who requested it. Use 'dm' "
                                    "for personal reminders, notifications, "
                                    "or anything meant for one person."
                                ),
                            },
                            "type": {
                                "type": "string",
                                "enum": ["agent", "script"],
                                "description": (
                                    "Type of cron job. 'agent' spins up a full LLM "
                                    "session with your personality and tools (default). "
                                    "'script' runs a deterministic python script without "
                                    "invoking the LLM. If type is 'script', the description "
                                    "field MUST be the exact path to the python script to run "
                                    "(e.g. 'scripts/report.py')."
                                ),
                            },
                            "condition_script_path": {
                                "type": "string",
                                "description": (
                                    "Optional path to a python script to run before "
                                    "the main cron job. If the script exits with code 0, "
                                    "the cron job proceeds. If non-zero, it skips execution "
                                    "entirely. Perfect for saving LLM costs on high-frequency "
                                    "checks."
                                ),
                            },
                            "max_runs": {
                                "type": "integer",
                                "description": (
                                    "Optional integer. If set > 0, the cron job will automatically "
                                    "delete itself after successfully running this many times."
                                ),
                            },
                            "depends_on": {
                                "type": "string",
                                "description": (
                                    "Optional string. The name or slug of another cron job that "
                                    "MUST have successfully run today before this one can execute. "
                                    "E.g., 'data-sync' or 'daily-revenue'."
                                ),
                            },
                        },
                        "required": ["name", "cron_expression", "title", "description"],
                    },
                },
            })

            tools.append({
                "type": "function",
                "function": {
                    "name": "lucy_delete_cron",
                    "description": (
                        "Delete an existing scheduled task by name. "
                        "Removes it from the scheduler immediately."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "cron_name": {
                                "type": "string",
                                "description": "Name/slug of the cron to delete",
                            },
                        },
                        "required": ["cron_name"],
                    },
                },
            })

            tools.append({
                "type": "function",
                "function": {
                    "name": "lucy_modify_cron",
                    "description": (
                        "Update an existing scheduled task's schedule, "
                        "title, or description. Only provide the fields "
                        "you want to change."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "cron_name": {
                                "type": "string",
                                "description": "Name/slug of the cron to modify",
                            },
                            "new_cron_expression": {
                                "type": "string",
                                "description": "New cron expression (if changing schedule)",
                            },
                            "new_title": {
                                "type": "string",
                                "description": "New title (if changing)",
                            },
                            "new_description": {
                                "type": "string",
                                "description": "New task instructions (if changing)",
                            },
                            "new_timezone": {
                                "type": "string",
                                "description": "New IANA timezone (if changing)",
                            },
                        },
                        "required": ["cron_name"],
                    },
                },
            })

            tools.append({
                "type": "function",
                "function": {
                    "name": "lucy_trigger_cron",
                    "description": (
                        "Immediately trigger a scheduled task to run right "
                        "now, regardless of its schedule. Useful for testing "
                        "or when a user asks 'run my X task now'."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "cron_name": {
                                "type": "string",
                                "description": "Name/slug of the cron to trigger",
                            },
                        },
                        "required": ["cron_name"],
                    },
                },
            })

            # Heartbeat monitor tools
            tools.append({
                "type": "function",
                "function": {
                    "name": "lucy_create_heartbeat",
                    "description": (
                        "Create a heartbeat monitor that checks a condition "
                        "at a set interval and alerts immediately when triggered. "
                        "Use this instead of cron jobs when the user needs "
                        "INSTANT alerting (e.g. 'tell me as soon as this page "
                        "goes live', 'alert me if the API goes down'). "
                        "Heartbeats check every 30s-5min and fire alerts "
                        "the moment a condition is met. Condition types:\n"
                        "- api_health: checks if a URL returns a healthy "
                        "HTTP status (config: {url, expected_status})\n"
                        "- page_content: checks if a page contains or lacks "
                        "specific text (config: {url, contains, not_contains, regex})\n"
                        "- metric_threshold: checks a JSON API value against "
                        "a threshold (config: {url, json_path, operator, threshold})\n"
                        "- custom: runs a Python script that returns "
                        "{triggered: true/false} (config: {script_path})"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": (
                                    "Short descriptive name for this monitor "
                                    "(e.g. 'api-health-check', 'product-availability')"
                                ),
                            },
                            "condition_type": {
                                "type": "string",
                                "enum": [
                                    "api_health", "page_content",
                                    "metric_threshold", "custom",
                                ],
                                "description": "Type of condition to check",
                            },
                            "condition_config": {
                                "type": "object",
                                "description": (
                                    "Configuration for the condition. "
                                    "Depends on condition_type. Examples:\n"
                                    "api_health: {\"url\": \"https://...\", \"expected_status\": 200}\n"
                                    "page_content: {\"url\": \"https://...\", \"contains\": \"In Stock\"}\n"
                                    "metric_threshold: {\"url\": \"https://api.../metrics\", "
                                    "\"json_path\": \"data.error_rate\", \"operator\": \">\", "
                                    "\"threshold\": 5.0}\n"
                                    "custom: {\"script_path\": \"scripts/check.py\"}"
                                ),
                            },
                            "check_interval_seconds": {
                                "type": "integer",
                                "description": (
                                    "How often to check (seconds). Default 300 (5 min). "
                                    "Minimum 30. Use 60-120 for urgent monitors, "
                                    "300-600 for standard monitors."
                                ),
                            },
                            "alert_channel_id": {
                                "type": "string",
                                "description": (
                                    "Slack channel ID to post alerts to. "
                                    "If omitted, uses the current channel."
                                ),
                            },
                            "alert_template": {
                                "type": "string",
                                "description": (
                                    "Alert message template. Use {name} and "
                                    "{detail} placeholders. Default: "
                                    "'Condition triggered: {name}'"
                                ),
                            },
                            "alert_cooldown_seconds": {
                                "type": "integer",
                                "description": (
                                    "Minimum seconds between alerts to prevent "
                                    "spam. Default 3600 (1 hour). Use 300 for "
                                    "critical monitors."
                                ),
                            },
                            "description": {
                                "type": "string",
                                "description": "Human-readable description of what this monitors",
                            },
                        },
                        "required": ["name", "condition_type", "condition_config"],
                    },
                },
            })

            tools.append({
                "type": "function",
                "function": {
                    "name": "lucy_delete_heartbeat",
                    "description": (
                        "Delete a heartbeat monitor by name. "
                        "Stops monitoring immediately."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "Name of the heartbeat monitor to delete",
                            },
                        },
                        "required": ["name"],
                    },
                },
            })

            tools.append({
                "type": "function",
                "function": {
                    "name": "lucy_list_heartbeats",
                    "description": (
                        "List all heartbeat monitors for this workspace. "
                        "Shows name, condition, interval, status, and statistics."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {},
                    },
                },
            })

            from lucy.integrations.custom_wrappers import load_custom_wrapper_tools
            tools.extend(load_custom_wrapper_tools())

            tools.append({
                "type": "function",
                "function": {
                    "name": "lucy_resolve_custom_integration",
                    "description": (
                        "Build a custom API integration for a service that "
                        "Composio does not support natively. Call this when: "
                        "(1) COMPOSIO_MANAGE_CONNECTIONS could not find the "
                        "toolkit, AND (2) the user has agreed to attempt a "
                        "custom connection. This tool researches the service's "
                        "API, generates a Python wrapper, tests it, and "
                        "deploys it as a new set of callable tools. After it "
                        "succeeds, ask the user for their API key and store "
                        "it with lucy_store_api_key. NEVER use Bright Data, "
                            "web scraping, or any other workaround. This is the "
                        "ONLY correct path for unsupported services."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "services": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "List of service names to attempt custom "
                                    "integration for (e.g. ['Clerk', 'Polar'])"
                                ),
                            },
                        },
                        "required": ["services"],
                    },
                },
            })

            tools.append({
                "type": "function",
                "function": {
                    "name": "lucy_delete_custom_integration",
                    "description": (
                        "Delete a custom integration that was previously "
                        "built. Removes the wrapper code, tools, and "
                        "stored API key. ALWAYS ask the user for "
                        "confirmation before calling this with "
                        "confirmed=true. First call with confirmed=false "
                        "to get a summary of what will be removed."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "service_slug": {
                                "type": "string",
                                "description": (
                                    "The slug of the integration to delete "
                                    "(e.g. 'polarsh', 'clerk')"
                                ),
                            },
                            "confirmed": {
                                "type": "boolean",
                                "description": (
                                    "Set to true only after the user has "
                                    "explicitly confirmed they want to "
                                    "delete. Set to false for a preview."
                                ),
                            },
                        },
                        "required": ["service_slug"],
                    },
                },
            })

        # 3b. Add delegation tools for sub-agent system
        from lucy.core.sub_agents import REGISTRY as _SUB_REGISTRY
        for agent_type, spec in _SUB_REGISTRY.items():
            tools.append({
                "type": "function",
                "function": {
                    "name": f"delegate_to_{agent_type}_agent",
                    "description": _DELEGATION_DESCRIPTIONS.get(
                        agent_type,
                        f"Delegate a task to the {agent_type} specialist.",
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "task": {
                                "type": "string",
                                "description": (
                                    "Clear description of what to accomplish. "
                                    "Include all relevant context."
                                ),
                            },
                        },
                        "required": ["task"],
                    },
                },
            })

        # 3c. Build tool registry for sub-agent use
        self._tool_registry: dict[str, dict[str, Any]] = {
            t["function"]["name"]: t
            for t in tools
            if isinstance(t, dict) and "function" in t
        }

        # 4. Build system prompt (SOUL + skills + instructions + environment)
        async with trace.span("build_prompt"):
            system_prompt = await build_system_prompt(
                ws,
                connected_services=connected_services,
                user_message=message,
                prompt_modules=route.prompt_modules,
            )
            utc_now = datetime.now(timezone.utc)
            time_block = (
                f"\n\n## Current Time\nUTC: "
                f"{utc_now.strftime('%Y-%m-%d %H:%M UTC')}\n"
            )

            if ctx.user_slack_id:
                try:
                    from lucy.workspace.timezone import (
                        get_user_local_time,
                        get_user_timezone_name,
                    )
                    tz_name = await get_user_timezone_name(
                        ctx.workspace_id, ctx.user_slack_id,
                    )
                    local_time = await get_user_local_time(
                        ctx.workspace_id, ctx.user_slack_id,
                    )
                    if local_time and tz_name:
                        local_str = local_time.strftime("%Y-%m-%d %H:%M")
                        time_block = (
                            f"\n\n## Current Time\n"
                            f"UTC: {utc_now.strftime('%Y-%m-%d %H:%M UTC')}\n"
                            f"User's local time ({tz_name}): {local_str}\n"
                            f"IMPORTANT: Always respond with times in the "
                            f"user's timezone ({tz_name}) unless they "
                            f"specifically ask for UTC.\n"
                        )
                except Exception as e:
                    logger.warning("timezone_resolution_failed", error=str(e))

            system_prompt += time_block

            # Inject user preferences if available
            if ctx.user_slack_id:
                try:
                    from lucy.workspace.preferences import (
                        extract_preferences_from_message,
                        format_preferences_for_prompt,
                        load_user_preferences,
                    )
                    extract_preferences_from_message(ctx.user_slack_id, message, ws)
                    prefs = load_user_preferences(ws, ctx.user_slack_id)
                    prefs_text = format_preferences_for_prompt(prefs)
                    if prefs_text:
                        system_prompt += f"\n\n<user_preferences>\n{prefs_text}\n</user_preferences>"
                except Exception as e:
                    logger.debug("preferences_inject_failed", error=str(e))

            # Inject channel context (purpose, boundaries, DM flag)
            if ctx.channel_id:
                try:
                    from lucy.workspace.channel_registry import (
                        format_channel_context_for_prompt,
                    )
                    channel_ctx = format_channel_context_for_prompt(
                        ws, ctx.channel_id,
                    )
                    if channel_ctx:
                        system_prompt += f"\n\n{channel_ctx}"
                except Exception as e:
                    logger.debug("channel_context_inject_failed", error=str(e))

        # 5. Build conversation messages from Slack thread
        async with trace.span("build_thread_messages"):
            messages = await self._build_thread_messages(
                ctx, message, slack_client
            )

        # 5b. Pre-flight context injection
        async with trace.span("preflight_context"):
            preflight_parts: list[str] = []

            try:
                from lucy.workspace.memory import get_session_context_for_prompt
                session_ctx = await get_session_context_for_prompt(
                    ws, thread_ts=ctx.thread_ts,
                )
                if session_ctx:
                    preflight_parts.append(session_ctx)
            except Exception as e:
                logger.warning("component_failed", component="session_context_preflight", error=str(e))

            if self._is_history_reference(message) and not ctx.thread_ts:
                try:
                    from lucy.workspace.history_search import (
                        format_search_results,
                        search_slack_history,
                    )
                    search_terms = self._extract_search_terms(message)
                    for term in search_terms[:2]:
                        results = await search_slack_history(
                            ws, term, days_back=30, max_results=5,
                        )
                        if results:
                            preflight_parts.append(
                                f"### Relevant Slack History\n"
                                f"{format_search_results(results)}"
                            )
                except Exception as e:
                    logger.warning("component_failed", component="history_search_preflight", error=str(e))

            if preflight_parts:
                context_block = "\n\n".join(preflight_parts)
                messages.insert(-1, {
                    "role": "system",
                    "content": (
                        f"<preflight_context>\n"
                        f"The following context was automatically loaded "
                        f"from the workspace. Use it to personalize and "
                        f"ground your response.\n\n"
                        f"{context_block}\n"
                        f"</preflight_context>"
                    ),
                })

        # 5c. Custom integration thread detection
        # If the thread shows Lucy offered a custom integration and the
        # current message is consent or provides an API key, inject an
        # explicit instruction so the LLM calls the right tool.
        nudge = self._detect_custom_integration_context(messages, message)
        if nudge:
            messages.insert(-1, {
                "role": "system",
                "content": nudge,
            })

        # 5d. Inject failure context from previous attempt (replan-on-failure)
        if failure_context:
            messages.insert(-1, {
                "role": "system",
                "content": (
                    "<previous_attempt_failed>\n"
                    f"{failure_context}\n"
                    "You MUST take a different approach this time. "
                    "Analyze what went wrong and fix the root cause. "
                    "Do NOT repeat the same strategy.\n"
                    "</previous_attempt_failed>"
                ),
            })

        # 5e. Thinking Model — explicit LLM planning step for complex tasks
        #
        # The planner needs context to understand the REAL need behind the
        # request, not just the literal words. We gather a compact snapshot:
        #   - Who is this person? (role, preferences)
        #   - What does the company do? (company knowledge)
        #   - What's been discussed? (session memory, thread summary)
        # This is kept short (~200 tokens) to minimize planner cost.
        from lucy.core.supervisor import create_plan as _create_plan

        tool_names_for_plan = [
            t.get("function", {}).get("name", "")
            for t in tools
            if isinstance(t, dict) and t.get("function", {}).get("name")
        ]

        planner_context_parts: list[str] = []

        # User preferences (brief/detailed, format, domains)
        if ctx.user_slack_id:
            try:
                from lucy.workspace.preferences import (
                    format_preferences_for_prompt,
                    load_user_preferences,
                )
                prefs = load_user_preferences(ws, ctx.user_slack_id)
                pref_text = format_preferences_for_prompt(prefs)
                if pref_text:
                    planner_context_parts.append(pref_text)
            except Exception as e:
                logger.warning("component_failed", component="planner_preferences", error=str(e))

        # Company + team knowledge (truncated to keep costs low)
        try:
            from lucy.workspace.skills import get_key_skill_content
            knowledge = await get_key_skill_content(ws)
            if knowledge:
                planner_context_parts.append(knowledge[:1000])
        except Exception as e:
            logger.warning("component_failed", component="planner_company_knowledge", error=str(e))

        # Session memory (recent facts from this conversation)
        try:
            from lucy.workspace.memory import get_session_context_for_prompt
            session_ctx = await get_session_context_for_prompt(
                ws, thread_ts=ctx.thread_ts,
            )
            if session_ctx:
                planner_context_parts.append(session_ctx[:1000])
        except Exception as e:
            logger.warning("component_failed", component="planner_session_memory", error=str(e))

        # Thread summary (condense earlier messages into a one-liner)
        if len(messages) > 2:
            thread_summary_parts: list[str] = []
            for m in messages[:-1]:
                role = m.get("role", "")
                content = m.get("content", "")
                if isinstance(content, str) and content.strip() and role in ("user", "assistant"):
                    snippet = content.strip()[:80]
                    thread_summary_parts.append(f"{role}: {snippet}")
            if thread_summary_parts:
                planner_context_parts.append(
                    "Thread so far: " + " | ".join(thread_summary_parts[-4:])
                )

        user_context_for_plan = "\n".join(planner_context_parts)

        task_plan = await _create_plan(
            user_message=message,
            available_tools=tool_names_for_plan,
            intent=route.intent,
            user_context=user_context_for_plan,
        )
        if task_plan:
            logger.info(
                "task_plan_created",
                goal=task_plan.goal,
                ideal_outcome=task_plan.ideal_outcome[:80] if task_plan.ideal_outcome else "",
                steps=len(task_plan.steps),
                risks=task_plan.risks[:80] if task_plan.risks else "",
                workspace_id=ctx.workspace_id,
            )

        # 6. Multi-turn LLM loop (supervisor-governed, no hard timeout)
        try:
            response_text = await asyncio.wait_for(
                self._agent_loop(
                    system_prompt=system_prompt,
                    messages=messages,
                    tools=tools,
                    ctx=ctx,
                    model=model,
                    trace=trace,
                    route=route,
                    slack_client=slack_client,
                    task_plan=task_plan,
                ),
                timeout=ABSOLUTE_MAX_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.critical(
                "absolute_safety_net_hit",
                workspace_id=ctx.workspace_id,
                channel=ctx.channel_id,
                timeout_seconds=ABSOLUTE_MAX_SECONDS,
            )
            partial = self._collect_partial_results(messages)
            if partial:
                response_text = partial
            else:
                response_text = (
                    "This task ran for an unusually long time and I had to "
                    "stop as a safety measure. Let me know if you'd like me "
                    "to continue from where I left off."
                )

        # 6b. Quality gate: catch service confusion and escalate if needed
        if route.tier != "frontier":
            gate = _assess_response_quality(message, response_text)
            if gate["should_escalate"]:
                logger.info(
                    "quality_gate_escalation",
                    reason=gate["reason"],
                    confidence=gate["confidence"],
                    original_model=model,
                )
                corrected = await self._escalate_response(
                    message, response_text, gate["reason"], ctx,
                )
                if corrected:
                    response_text = corrected

        # 6b2. Verification gate: check completeness and retry if needed
        _MAX_RETRY_DEPTH = 1
        if not failure_context and _retry_depth < _MAX_RETRY_DEPTH:
            verification = _verify_output(
                message, response_text or "", route.intent,
            )
            if not verification["passed"] and verification["should_retry"]:
                failure_desc = "; ".join(verification["issues"])
                logger.warning(
                    "verification_gate_retry",
                    workspace_id=ctx.workspace_id,
                    issues=verification["issues"],
                    intent=route.intent,
                    retry_depth=_retry_depth,
                )
                from lucy.pipeline.router import MODEL_TIERS
                escalated_model = MODEL_TIERS.get("code", model)
                try:
                    retried = await self.run(
                        message=message,
                        ctx=ctx,
                        slack_client=slack_client,
                        model_override=escalated_model,
                        failure_context=(
                            f"Previous attempt failed verification: "
                            f"{failure_desc}"
                        ),
                        _retry_depth=_retry_depth + 1,
                    )
                    if retried and len(retried) > len(response_text or ""):
                        response_text = retried
                        logger.info(
                            "verification_retry_succeeded",
                            workspace_id=ctx.workspace_id,
                        )
                except Exception as e:
                    logger.warning(
                        "verification_retry_failed",
                        workspace_id=ctx.workspace_id,
                        error=str(e),
                    )

        # 6b3. Self-critique gate: LLM reviews complex responses before delivery
        _CRITIQUE_INTENTS = {"data", "research", "document", "code", "reasoning"}
        if (
            response_text
            and route.intent in _CRITIQUE_INTENTS
            and not failure_context
            and _retry_depth == 0
            and len(response_text) > 200
        ):
            try:
                response_text = await self._self_critique(
                    message, response_text, route.intent, model, ctx,
                )
            except Exception as exc:
                logger.warning(
                    "self_critique_failed",
                    workspace_id=ctx.workspace_id,
                    error=str(exc) or type(exc).__name__,
                )

        # 6c. Post-response: persist memorable facts to memory
        try:
            from lucy.workspace.memory import (
                add_session_fact,
                append_to_company_knowledge,
                append_to_team_knowledge,
                check_fact_contradictions,
                classify_memory_target,
                should_persist_memory,
            )
            if should_persist_memory(message):
                target = classify_memory_target(message)
                fact = message.strip()
                if len(fact) > 500:
                    fact = fact[:500] + "..."

                if target in ("company", "team"):
                    warning = await check_fact_contradictions(
                        ws, fact, target,
                    )
                    if warning:
                        logger.warning(
                            "memory_contradiction_detected",
                            fact=fact[:100],
                            warning=warning,
                            target=target,
                            workspace_id=ctx.workspace_id,
                        )

                if target == "company":
                    await append_to_company_knowledge(ws, fact)
                elif target == "team":
                    await append_to_team_knowledge(ws, fact)
                else:
                    await add_session_fact(
                        ws, fact, source="conversation", category=target,
                        thread_ts=ctx.thread_ts,
                    )
                logger.debug(
                    "memory_persisted",
                    target=target,
                    workspace_id=ctx.workspace_id,
                )
        except Exception as e:
            logger.warning("memory_persist_error", error=str(e))

        # 7. Log activity
        from lucy.workspace.activity_log import log_activity

        elapsed_ms = trace.total_ms
        preview = message[:80].replace("\n", " ")
        await log_activity(
            ws,
            f"Responded to \"{preview}\" in {round(elapsed_ms)}ms "
            f"[model={model}, intent={route.intent}]",
        )

        if not response_text:
            response_text = ""

        logger.info(
            "agent_run_complete",
            workspace_id=ctx.workspace_id,
            elapsed_ms=round(elapsed_ms),
            response_length=len(response_text),
            model=model,
            intent=route.intent,
            tool_calls=len(trace.tool_calls_made),
        )

        # 8. Write trace
        trace.finish(
            user_message=message,
            response_text=response_text,
        )
        await trace.write_to_thread_log(
            settings.workspace_root, ctx.workspace_id, ctx.thread_ts,
        )

        return _strip_control_tokens(response_text)

    # ── Agent loop ──────────────────────────────────────────────────────

    async def _agent_loop(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        ctx: AgentContext,
        model: str,
        trace: Trace,
        route: Any,
        slack_client: Any | None = None,
        task_plan: Any | None = None,
    ) -> str:
        """Multi-turn LLM <-> tool execution loop.

        The loop is governed by the Supervisor agent, not hard timeouts.
        The supervisor evaluates progress every few turns and decides
        whether to continue, intervene, replan, escalate, or abort.
        """
        from lucy.core.supervisor import (
            SupervisorDecision,
            TurnReport,
            build_turn_report,
            create_plan as _create_plan_fn,
            evaluate_progress,
            should_check as _sv_should_check,
        )

        client = await self._get_client()
        all_messages = list(messages)
        response_text = ""
        repeated_sigs: dict[str, int] = {}
        tool_name_counts: dict[str, int] = {}
        self._empty_retries = 0
        self._narration_retries = 0
        self._edit_attempts = 0
        self._400_recovery_count = 0
        self._504_frontier_retried = False
        _last_visible_msg_time = time.monotonic()
        _silence_update_sent = False
        _SILENCE_THRESHOLD_S = 480.0  # 8 minutes

        # Supervisor state
        sv_turn_reports: list[TurnReport] = []
        sv_last_check_time = time.monotonic()
        sv_start_time = time.monotonic()
        sv_consecutive_failures = 0

        # Inject the thinking model's output into context
        if task_plan:
            plan_text = task_plan.to_prompt_text()
            all_messages.insert(-1, {
                "role": "system",
                "content": (
                    f"<execution_plan>\n{plan_text}\n</execution_plan>\n"
                    "Follow this plan. The AMAZING OUTCOME is your target, "
                    "not just the minimum. Actively AVOID the underwhelming "
                    "response pattern. If a step fails, check RISKS and try "
                    "the fallback. Present results using FORMAT. Consider "
                    "WHO is asking when choosing depth and tone."
                ),
            })

        tool_names = {
            t.get("function", {}).get("name", "")
            for t in (tools or [])
            if isinstance(t, dict) and t.get("function", {}).get("name")
        }

        current_model = model

        max_turns = MAX_TOOL_TURNS

        base_max_tokens = 16_384

        for turn in range(max_turns):
            try:
                remaining = _check_budget(ctx)
            except asyncio.TimeoutError:
                logger.warning(
                    "request_budget_exhausted",
                    turn=turn,
                    workspace_id=ctx.workspace_id,
                )
                partial = self._collect_partial_results(all_messages)
                if partial:
                    return partial
                return (
                    "This task used up the available time budget. "
                    "Here's what I managed to complete so far."
                )

            # Main agent loop calls use streaming for intelligent silence
            # detection. Internal calls (planner, supervisor) stay non-streaming.
            use_streaming = bool(tools)
            config = ChatConfig(
                model=current_model,
                system_prompt=system_prompt,
                tools=tools,
                max_tokens=base_max_tokens,
                stream=use_streaming,
                wallclock_timeout=min(remaining, 1200.0),
            )

            try:
                async with trace.span(
                    f"llm_call_{turn}", model=current_model
                ) as llm_span:
                    response = await client.chat_completion(
                        messages=all_messages,
                        config=config,
                    )
                    if response.usage:
                        for k, v in response.usage.items():
                            if isinstance(v, (int, float)):
                                trace.usage[k] = trace.usage.get(k, 0) + v
            except OpenClawError as e:
                if e.status_code == 504:
                    from lucy.pipeline.router import MODEL_TIERS
                    frontier = MODEL_TIERS.get("frontier", current_model)
                    if frontier != current_model:
                        logger.warning(
                            "silence_detected_model_escalation",
                            from_model=current_model,
                            to_model=frontier,
                            turn=turn,
                            reason=str(e)[:200],
                            workspace_id=ctx.workspace_id,
                        )
                        current_model = frontier
                        continue
                    if not getattr(self, "_504_frontier_retried", False):
                        self._504_frontier_retried = True
                        logger.warning(
                            "504_frontier_retry",
                            turn=turn,
                            workspace_id=ctx.workspace_id,
                        )
                        await asyncio.sleep(5)
                        continue
                    logger.error(
                        "504_frontier_exhausted",
                        turn=turn,
                        workspace_id=ctx.workspace_id,
                    )
                    partial = self._collect_partial_results(all_messages)
                    if partial:
                        return partial
                    return (
                        "I'm experiencing connectivity issues with the AI service. "
                        "Let me know if you'd like me to try again in a moment."
                    )
                if e.status_code == 400:
                    _400_recovery_count = getattr(
                        self, "_400_recovery_count", 0,
                    ) + 1
                    self._400_recovery_count = _400_recovery_count
                    if _400_recovery_count > 3:
                        logger.error(
                            "400_recovery_limit_exceeded",
                            turn=turn,
                            attempts=_400_recovery_count,
                            workspace_id=ctx.workspace_id,
                        )
                        raise
                    if turn == 0:
                        logger.warning(
                            "400_turn0_recovery",
                            turn=turn,
                            tool_count=len(tools) if tools else 0,
                            workspace_id=ctx.workspace_id,
                        )
                        if tools and len(tools) > 50:
                            tools = tools[:50]
                            logger.info(
                                "400_turn0_tools_trimmed",
                                new_count=len(tools),
                            )
                            continue
                        from lucy.pipeline.router import MODEL_TIERS
                        frontier = MODEL_TIERS.get("frontier", current_model)
                        if frontier != current_model:
                            current_model = frontier
                            logger.info(
                                "400_turn0_model_escalation",
                                to_model=frontier,
                            )
                            continue
                        raise
                    logger.warning(
                        "400_recovery_trimming",
                        turn=turn,
                        messages_before=len(all_messages),
                        attempt=_400_recovery_count,
                    )
                    all_messages = await _trim_tool_results(all_messages)
                    from lucy.pipeline.router import MODEL_TIERS
                    frontier = MODEL_TIERS.get("frontier", current_model)
                    if frontier != current_model:
                        current_model = frontier
                        logger.info(
                            "400_recovery_model_escalation",
                            to_model=frontier,
                        )
                    continue
                raise

            # Detect output truncation: if the model used nearly all
            # available tokens and returned no tool_calls, ask it to continue.
            if (
                response.content
                and not response.tool_calls
                and response.usage
                and response.usage.get("completion_tokens", 0)
                >= base_max_tokens * 0.9
            ):
                all_messages.append(
                    {"role": "assistant", "content": response.content}
                )
                all_messages.append({
                    "role": "system",
                    "content": (
                        "Your previous response was truncated. "
                        "Continue from where you left off."
                    ),
                })
                logger.info(
                    "output_truncation_detected",
                    turn=turn,
                    tokens=response.usage.get("completion_tokens"),
                )
                continue

            response_text = response.content or ""
            tool_calls = response.tool_calls

            # Normalize tool names: some models return leading whitespace
            if tool_calls:
                for tc in tool_calls:
                    if "name" in tc:
                        tc["name"] = tc["name"].strip()

            # Empty response recovery: if the LLM returns nothing
            # after tool results, escalate model then nudge.
            if not tool_calls and not response_text.strip() and turn > 0:
                empty_retries = getattr(self, "_empty_retries", 0)
                if empty_retries < 2:
                    self._empty_retries = empty_retries + 1

                    if empty_retries == 1:
                        from lucy.pipeline.router import MODEL_TIERS
                        stronger = MODEL_TIERS.get("frontier", current_model)
                        if stronger != current_model:
                            current_model = stronger
                            logger.warning(
                                "empty_response_model_escalation",
                                turn=turn,
                                from_model=model,
                                to_model=stronger,
                                workspace_id=ctx.workspace_id,
                            )

                    logger.warning(
                        "empty_response_retry",
                        turn=turn,
                        attempt=empty_retries + 1,
                        model=current_model,
                        workspace_id=ctx.workspace_id,
                    )
                    all_messages.append({
                        "role": "system",
                        "content": (
                            "Your previous response was empty. You must "
                            "either call a tool or provide a substantive "
                            "answer to the user."
                        ),
                    })
                    _recovery_intent = getattr(route, "intent", "")
                    if _recovery_intent == "monitoring":
                        _recovery_nudge = (
                            "The user wants you to SET UP monitoring. "
                            "For instant alerts ('tell me as soon as', "
                            "'alert me if'), use lucy_create_heartbeat. "
                            "For periodic reports ('daily report', 'weekly "
                            "summary'), use lucy_create_cron. Do NOT just "
                            "fetch data once."
                        )
                    else:
                        _recovery_nudge = (
                            "You found the right tools. Now use them to "
                            "complete the user's request and give the answer."
                        )
                    all_messages.append({
                        "role": "user",
                        "content": _recovery_nudge,
                    })
                    continue

            if not tool_calls:
                if (
                    turn == 0
                    and tools
                    and self._claims_no_access(response_text)
                ):
                    logger.warning(
                        "false_no_access_detected",
                        workspace_id=ctx.workspace_id,
                        tool_count=len(tools),
                    )
                    all_messages.append(
                        {"role": "assistant", "content": response_text}
                    )
                    all_messages.append({
                        "role": "user",
                        "content": (
                            "You DO have tools available to help with "
                            "this. Please use them to search for what's "
                            "needed and execute the request directly, "
                            "rather than saying you don't have access."
                        ),
                    })
                    continue

                narration_retries = getattr(self, "_narration_retries", 0)
                _is_setup_intent = getattr(route, "intent", "") in (
                    "monitoring", "command",
                )
                if (
                    turn <= 3
                    and tools
                    and narration_retries < 1
                    and not _is_setup_intent
                    and _NARRATION_RE.search(response_text)
                ):
                    self._narration_retries = narration_retries + 1
                    logger.warning(
                        "narration_detected",
                        turn=turn,
                        workspace_id=ctx.workspace_id,
                    )
                    all_messages.append(
                        {"role": "assistant", "content": response_text}
                    )
                    all_messages.append({
                        "role": "user",
                        "content": (
                            "I need the actual data, not a plan. "
                            "Please call the tools now and give me the results."
                        ),
                    })
                    continue

                break

            # Loop detection — exact-signature repeats
            sig = self._call_signature(tool_calls)
            repeated_sigs[sig] = repeated_sigs.get(sig, 0) + 1
            if repeated_sigs[sig] >= 3:
                logger.warning("tool_loop_detected", turn=turn)
                all_messages.append({
                    "role": "system",
                    "content": (
                        "Your previous approach is not working. You have "
                        "called the same tool with the same parameters 3 "
                        "times. DO NOT retry the same tool or approach. "
                        "Consider: a completely different search query, "
                        "sharing partial results you already have, or "
                        "asking the user one specific clarifying question. "
                        "NEVER mention tool calls, loops, retries, or "
                        "internal execution to the user."
                    ),
                })
                repeated_sigs.clear()
                continue

            # Per-tool-name call cap: prevents the model from calling
            # the same tool 4+ times even with varied parameters.
            _CAP_EXEMPT = {
                "lucy_web_search", "COMPOSIO_SEARCH_TOOLS",
                "COMPOSIO_REMOTE_WORKBENCH",
                "COMPOSIO_MULTI_EXECUTE_TOOL",
                "COMPOSIO_REMOTE_BASH_TOOL",
                "COMPOSIO_GET_TOOL_SCHEMAS",
                "COMPOSIO_MANAGE_CONNECTIONS",
            }
            for tc in tool_calls:
                tn = tc.get("name", "").strip()
                tool_name_counts[tn] = tool_name_counts.get(tn, 0) + 1

            def _is_cap_exempt(name: str) -> bool:
                if name in _CAP_EXEMPT:
                    return True
                if name.startswith("lucy_custom_"):
                    if any(s in name for s in ("_list_", "_get_metrics", "_get_stats", "_get_user_stats")):
                        return True
                if name in ("lucy_get_channel_history", "lucy_search_slack_history"):
                    return True
                return False

            over_cap = [
                n for n, c in tool_name_counts.items()
                if c >= 4 and not _is_cap_exempt(n)
            ]
            if over_cap:
                cap_violations = sum(
                    tool_name_counts[n] - 3 for n in over_cap
                )
                logger.warning(
                    "tool_name_cap_hit",
                    tools=over_cap,
                    counts={n: tool_name_counts[n] for n in over_cap},
                    turn=turn,
                    violations=cap_violations,
                )
                # Force stop immediately on second cap hit
                if cap_violations >= 2:
                    logger.warning(
                        "tool_cap_force_stop",
                        turn=turn,
                        tools=over_cap,
                    )
                    _capped_tools: set[str] = getattr(self, "_capped_tools", set())
                    _capped_tools.update(over_cap)
                    self._capped_tools = _capped_tools
                    if tools:
                        tools = [
                            t for t in tools
                            if t.get("function", {}).get("name", "") not in _capped_tools
                        ]
                        if not tools:
                            tools = None
                    all_messages.append({
                        "role": "system",
                        "content": (
                            "CRITICAL: You have repeatedly ignored "
                            "instructions to stop calling the same tools. "
                            f"The following tools have been disabled: "
                            f"{', '.join(over_cap)}. "
                            "You MUST respond to the user NOW with "
                            "whatever you have. If you were building an app, call "
                            "lucy_spaces_deploy with what you have so far."
                        ),
                    })
                    continue
                all_messages.append({
                    "role": "system",
                    "content": (
                        f"You have called {', '.join(over_cap)} too many "
                        f"times. STOP calling it. If you were writing app "
                        f"code, the file is written — now call "
                        f"lucy_spaces_deploy to deploy it. Do NOT write "
                        f"more files."
                    ),
                })
                continue

            logger.info(
                "tool_turn",
                turn=turn + 1,
                calls=[tc.get("name") for tc in tool_calls],
                workspace_id=ctx.workspace_id,
            )

            _elapsed_silent = time.monotonic() - _last_visible_msg_time
            _remaining_turns = max_turns - turn
            if (
                _elapsed_silent > _SILENCE_THRESHOLD_S
                and not _silence_update_sent
                and slack_client
                and ctx.channel_id
                and ctx.thread_ts
                and not ctx.is_cron_execution
                and _remaining_turns > 2
            ):
                _silence_update_sent = True
                all_messages.append({
                    "role": "system",
                    "content": (
                        "You have been working for over 8 minutes. "
                        "Send the user a brief 1-sentence progress update "
                        "about what you've done so far and how much longer. "
                        "Keep it casual and specific to the task — no generic "
                        "filler. Then continue working."
                    ),
                })

            # Append assistant message with tool_calls
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": response_text,
                "tool_calls": [
                    {
                        "id": tc.get("id", f"call_{i}"),
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(
                                tc.get("parameters", {})
                            ),
                        },
                    }
                    for i, tc in enumerate(tool_calls)
                ],
            }
            all_messages.append(assistant_msg)

            # Execute tool calls in parallel
            tool_results = await self._execute_tools_parallel(
                tool_calls, tool_names, ctx, trace, slack_client,
            )

            for call_id, result_str in tool_results:
                if len(result_str) > TOOL_RESULT_SUMMARY_THRESHOLD:
                    head_chars = 4000
                    tail_chars = 2000
                    trimmed_count = len(result_str) - head_chars - tail_chars
                    result_str = (
                        result_str[:head_chars]
                        + f"...(trimmed {trimmed_count} chars)..."
                        + result_str[-tail_chars:]
                    )
                all_messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": result_str,
                })

            payload_size = sum(
                len(m.get("content", "")) for m in all_messages
            )
            if payload_size > MAX_PAYLOAD_CHARS:
                all_messages = await _trim_tool_results(all_messages, max_result_chars=1000)
                logger.info(
                    "payload_trimmed",
                    turn=turn,
                    before_chars=payload_size,
                )

            # Build turn reports for supervisor
            sv_turn_reports.extend(
                build_turn_report(turn, tool_calls, tool_results)
            )

            # Stuck detection: analyze error patterns across recent turns
            # (kept as fast heuristic — feeds into supervisor context)
            stuck = _detect_stuck_state(all_messages, turn)
            if stuck["is_stuck"]:
                logger.warning(
                    "stuck_state_detected",
                    turn=turn,
                    reason=stuck["reason"],
                    workspace_id=ctx.workspace_id,
                )
                all_messages.append({
                    "role": "system",
                    "content": stuck["intervention"],
                })
                if stuck.get("escalate_model"):
                    from lucy.pipeline.router import MODEL_TIERS
                    _ESCALATION_ORDER = ["default", "code", "research", "frontier"]
                    current_tier_idx = -1
                    for i, tier in enumerate(_ESCALATION_ORDER):
                        if MODEL_TIERS.get(tier) == current_model:
                            current_tier_idx = i
                            break
                    next_tier_idx = min(current_tier_idx + 1, len(_ESCALATION_ORDER) - 1)
                    escalated = MODEL_TIERS.get(
                        _ESCALATION_ORDER[next_tier_idx], current_model,
                    )
                    if escalated != current_model:
                        logger.info(
                            "stuck_model_escalation",
                            from_model=current_model,
                            to_model=escalated,
                        )
                        current_model = escalated

            # ── Supervisor checkpoint ────────────────────────────────
            sv_elapsed = time.monotonic() - sv_start_time
            if _sv_should_check(turn, sv_last_check_time, sv_elapsed):
                sv_last_check_time = time.monotonic()
                try:
                    sv_user_msg = ""
                    for _m in reversed(messages):
                        if _m.get("role") == "user":
                            sv_user_msg = _m.get("content", "")
                            if isinstance(sv_user_msg, str):
                                break
                            sv_user_msg = ""

                    sv_result = await evaluate_progress(
                        plan=task_plan,
                        turn_reports=sv_turn_reports,
                        user_message=sv_user_msg,
                        elapsed_seconds=sv_elapsed,
                        current_model=current_model,
                        response_text_length=len(response_text),
                        intent=getattr(route, "intent", ""),
                    )
                    logger.info(
                        "supervisor_checkpoint",
                        turn=turn,
                        decision=sv_result.decision.value,
                        guidance=sv_result.guidance[:80] if sv_result.guidance else "",
                        elapsed_s=int(sv_elapsed),
                        workspace_id=ctx.workspace_id,
                    )

                    if sv_result.decision == SupervisorDecision.INTERVENE:
                        all_messages.append({
                            "role": "system",
                            "content": (
                                f"<supervisor_guidance>\n"
                                f"{sv_result.guidance}\n"
                                f"</supervisor_guidance>"
                            ),
                        })

                    elif sv_result.decision == SupervisorDecision.REPLAN:
                        user_msg = ""
                        for m in all_messages:
                            if m.get("role") == "user":
                                content = m.get("content", "")
                                if isinstance(content, str) and content.strip():
                                    user_msg = content.strip()
                        tool_name_list = list(tool_names)
                        new_plan = await _create_plan_fn(
                            user_message=user_msg or "complete the task",
                            available_tools=tool_name_list,
                            intent=getattr(route, "intent", ""),
                        )
                        if new_plan:
                            task_plan = new_plan
                            all_messages.append({
                                "role": "system",
                                "content": (
                                    f"<revised_plan>\n"
                                    f"The previous approach had issues. "
                                    f"Follow this revised plan:\n"
                                    f"{new_plan.to_prompt_text()}\n"
                                    f"</revised_plan>"
                                ),
                            })
                            logger.info(
                                "supervisor_replan",
                                new_steps=len(new_plan.steps),
                                workspace_id=ctx.workspace_id,
                            )

                    elif sv_result.decision == SupervisorDecision.ESCALATE:
                        from lucy.pipeline.router import MODEL_TIERS
                        _ESC_ORDER = ["fast", "default", "code", "research", "frontier"]
                        cur_idx = -1
                        for i, tier in enumerate(_ESC_ORDER):
                            if MODEL_TIERS.get(tier) == current_model:
                                cur_idx = i
                                break
                        nxt = min(cur_idx + 1, len(_ESC_ORDER) - 1)
                        esc_model = MODEL_TIERS.get(_ESC_ORDER[nxt], current_model)
                        if esc_model != current_model:
                            logger.info(
                                "supervisor_escalation",
                                from_model=current_model,
                                to_model=esc_model,
                            )
                            current_model = esc_model

                    elif sv_result.decision == SupervisorDecision.ASK_USER:
                        if slack_client and ctx.channel_id and ctx.thread_ts:
                            try:
                                await slack_client.chat_postMessage(
                                    channel=ctx.channel_id,
                                    thread_ts=ctx.thread_ts,
                                    text=sv_result.guidance or (
                                        "I need a bit of clarification to "
                                        "continue — could you provide more details?"
                                    ),
                                )
                            except Exception as ask_exc:
                                logger.warning(
                                    "supervisor_ask_user_failed",
                                    error=str(ask_exc),
                                )
                        break

                    elif sv_result.decision == SupervisorDecision.ABORT:
                        logger.warning(
                            "supervisor_abort",
                            reason=sv_result.guidance,
                            turn=turn,
                            workspace_id=ctx.workspace_id,
                        )
                        if sv_result.guidance:
                            response_text = sv_result.guidance
                        break

                    sv_consecutive_failures = 0

                except Exception as sv_exc:
                    sv_consecutive_failures += 1
                    logger.warning(
                        "supervisor_checkpoint_error",
                        error=str(sv_exc),
                        turn=turn,
                        consecutive_failures=sv_consecutive_failures,
                    )
                    if sv_consecutive_failures >= 3:
                        logger.warning(
                            "supervisor_fallback_heuristic",
                            turn=turn,
                            elapsed_s=int(sv_elapsed),
                            workspace_id=ctx.workspace_id,
                        )
                        if sv_elapsed > 300:
                            all_messages.append({
                                "role": "system",
                                "content": (
                                    "You have been running for over 5 minutes. "
                                    "Wrap up with what you have and respond now."
                                ),
                            })
                        elif turn > max_turns * 0.75:
                            all_messages.append({
                                "role": "system",
                                "content": (
                                    "You are running low on turns. Finish up "
                                    "and deliver your best answer now."
                                ),
                            })

            # Mid-loop model upgrade: only switch to code model
            # when the original routing intent was code-related.
            # Avoids slow DeepSeek calls for calendar/email tasks
            # that happen to trigger REMOTE_WORKBENCH.
            called_names = {tc.get("name", "") for tc in tool_calls}
            if (
                called_names & {"COMPOSIO_REMOTE_WORKBENCH", "COMPOSIO_REMOTE_BASH_TOOL"}
                and route.intent in ("code", "code_reasoning")
                and getattr(self, "_edit_attempts", 0) < 2
            ):
                from lucy.pipeline.router import MODEL_TIERS
                code_model = MODEL_TIERS.get("code", current_model)
                if code_model != current_model:
                    logger.info(
                        "model_upgrade_mid_loop",
                        from_model=current_model,
                        to_model=code_model,
                        reason="code_execution_detected",
                    )
                    current_model = code_model

            # Fail-Up Escalation: if lucy_edit_file has been called repeatedly,
            # escalate to frontier model for deeper debugging capability
            if "lucy_edit_file" in called_names:
                edit_attempts = getattr(self, "_edit_attempts", 0) + 1
                self._edit_attempts = edit_attempts
                if edit_attempts >= 2:
                    from lucy.pipeline.router import MODEL_TIERS
                    frontier_model = MODEL_TIERS.get(
                        "frontier", current_model,
                    )
                    if frontier_model != current_model:
                        logger.info(
                            "fail_up_escalation",
                            from_model=current_model,
                            to_model=frontier_model,
                            edit_attempts=edit_attempts,
                        )
                        current_model = frontier_model

            # Trim context window — smart trimming that preserves key messages
            if len(all_messages) > MAX_CONTEXT_MESSAGES:
                pinned_indices: set[int] = set()
                # Pin first message (system/user)
                pinned_indices.add(0)
                # Pin messages containing execution plan
                for _i, _m in enumerate(all_messages):
                    if "<execution_plan>" in (_m.get("content") or ""):
                        pinned_indices.add(_i)
                # Pin the last 5 messages
                tail_start = max(0, len(all_messages) - 5)
                for _i in range(tail_start, len(all_messages)):
                    pinned_indices.add(_i)

                trimmed_msgs: list[dict[str, Any]] = []
                budget = MAX_CONTEXT_MESSAGES - len(pinned_indices)
                non_pinned = [
                    (_i, _m) for _i, _m in enumerate(all_messages)
                    if _i not in pinned_indices
                ]
                # Keep newest non-pinned first; drop tool results from middle oldest-first
                tool_indices = [
                    _i for _i, _m in non_pinned if _m.get("role") == "tool"
                ]
                drop_set: set[int] = set()
                for _i in tool_indices:
                    if len(drop_set) >= len(non_pinned) - budget:
                        break
                    drop_set.add(_i)
                # If still over budget, drop oldest non-pinned non-tool messages
                if len(non_pinned) - len(drop_set) > budget:
                    for _i, _ in non_pinned:
                        if _i not in drop_set:
                            drop_set.add(_i)
                            if len(non_pinned) - len(drop_set) <= budget:
                                break

                for _i, _m in enumerate(all_messages):
                    if _i not in drop_set:
                        trimmed_msgs.append(_m)
                all_messages = trimmed_msgs

        if not response_text.strip():
            partial = self._collect_partial_results(all_messages)
            if partial:
                response_text = partial
            else:
                from lucy.pipeline.humanize import humanize
                response_text = await humanize(
                    "You tried multiple approaches but couldn't get the "
                    "result yet. Don't give up — ask the user one specific "
                    "clarifying question that would help you try a different "
                    "angle. Be warm and action-oriented, not apologetic.",
                )

        return response_text

    _TOOL_HUMAN_NAMES: dict[str, str] = {
        "lucy_spaces_init": "setting up the project",
        "lucy_write_file": "writing code",
        "lucy_edit_file": "editing code",
        "lucy_check_errors": "checking for errors",
        "lucy_spaces_deploy": "deploying your app",
        "lucy_read_file": "reading project files",
        "lucy_run_script": "running a script",
        "lucy_generate_excel": "creating a spreadsheet",
        "lucy_generate_csv": "creating a CSV",
        "lucy_send_email": "sending an email",
        "lucy_create_heartbeat": "setting up a monitor",
        "lucy_delete_heartbeat": "removing a monitor",
        "lucy_list_heartbeats": "listing monitors",
        "lucy_create_cron": "creating a scheduled task",
        "lucy_delete_cron": "removing a scheduled task",
        "lucy_modify_cron": "updating a scheduled task",
        "lucy_trigger_cron": "triggering a scheduled task",
        "lucy_start_service": "starting a background service",
        "lucy_stop_service": "stopping a background service",
        "lucy_list_services": "listing background services",
        "lucy_service_logs": "fetching service logs",
    }

    @staticmethod
    def _collect_partial_results(
        messages: list[dict[str, Any]],
    ) -> str:
        """Produce a graceful fallback when the LLM returns an empty response.

        Never expose raw JSON, tool names, or internal data to the user.
        Returns a warm, human-readable status message with task context
        when available.
        """
        tool_count = 0
        has_errors = False
        last_tool_name = ""
        error_hint = ""
        for msg in messages:
            if msg.get("role") == "tool" and msg.get("content", ""):
                tool_count += 1
                content = msg.get("content", "")
                if '"error"' in content:
                    has_errors = True
                    content_lower = content.lower()
                    if "timeout" in content_lower or "timed out" in content_lower:
                        error_hint = "a timeout"
                    elif "rate limit" in content_lower or "429" in content_lower:
                        error_hint = "a rate limit"
                    elif (
                        "connection" in content_lower
                        or "connect" in content_lower
                        or "unreachable" in content_lower
                    ):
                        error_hint = "a connection issue"
                    elif (
                        "permission" in content_lower
                        or "forbidden" in content_lower
                        or "401" in content_lower
                        or "403" in content_lower
                    ):
                        error_hint = "a permissions issue"
                    elif "not found" in content_lower or "404" in content_lower:
                        error_hint = "a missing resource"
                    else:
                        error_hint = "a hiccup"
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    fn = tc.get("function", {})
                    name = fn.get("name", "") if isinstance(fn, dict) else ""
                    if name:
                        last_tool_name = name

        if tool_count == 0:
            return ""

        if has_errors:
            task_desc = ""
            if last_tool_name:
                if last_tool_name in LucyAgent._TOOL_HUMAN_NAMES:
                    task_desc = LucyAgent._TOOL_HUMAN_NAMES[last_tool_name]
                elif last_tool_name.startswith("lucy_custom_"):
                    slug = last_tool_name.removeprefix("lucy_custom_").split("_")[0]
                    task_desc = f"pulling data from {slug.title()}"

            if task_desc and error_hint:
                return (
                    f"I was {task_desc} but hit {error_hint}. "
                    "Let me try a different approach."
                )
            if task_desc:
                return (
                    f"I ran into an issue while {task_desc}. "
                    "Let me try a different approach."
                )
            return (
                "I ran into a hiccup while processing your request. "
                "Let me try a different approach."
            )

        has_tool_results = any(
            msg.get("role") == "tool"
            and msg.get("content", "")
            and '"error"' not in msg.get("content", "")
            for msg in messages
        )
        if has_tool_results:
            tool_data: list[str] = []
            for msg in messages:
                if msg.get("role") == "tool" and msg.get("content", ""):
                    content = msg["content"]
                    if '"error"' not in content:
                        sanitized = _sanitize_tool_output(content[:500])
                        # Try to extract a human-readable summary from JSON
                        try:
                            parsed = json.loads(sanitized)
                            if isinstance(parsed, dict):
                                if "result" in parsed:
                                    sanitized = str(parsed["result"])[:300]
                                elif "message" in parsed:
                                    sanitized = str(parsed["message"])[:300]
                                elif "data" in parsed:
                                    sanitized = str(parsed["data"])[:300]
                        except (json.JSONDecodeError, ValueError):
                            pass
                        tool_data.append(sanitized)
            if not tool_data:
                return ""
            summary = "; ".join(tool_data[:3])
            return (
                f"Here's what I found so far: {summary}\n\n"
                "I'm still piecing together the full picture. "
                "Let me know if this helps or if you need me "
                "to dig deeper."
            )
        return ""

    # ── Parallel tool execution ─────────────────────────────────────────

    async def _execute_tools_parallel(
        self,
        tool_calls: list[dict[str, Any]],
        tool_names: set[str],
        ctx: AgentContext,
        trace: Trace,
        slack_client: Any | None = None,
    ) -> list[tuple[str, str]]:
        """Execute all tool calls from a single LLM turn in parallel."""
        async def _run_one(i: int, tc: dict[str, Any]) -> tuple[str, str]:
            name = tc.get("name", "")
            
            # Register internal tools into tool_names dynamically if missed
            if name.startswith("lucy_") and name not in tool_names:
                tool_names.add(name)

            params = tc.get("parameters", {})
            call_id = tc.get("id", f"call_{i}")
            parse_error = tc.get("parse_error")

            if parse_error:
                return call_id, json.dumps({
                    "error": (
                        f"Failed to parse arguments for '{name}': "
                        f"{parse_error}. Please retry with valid JSON "
                        f"arguments."
                    ),
                })

            if name not in tool_names:
                return call_id, json.dumps({
                    "error": f"Tool '{name}' is not available."
                })

            # ── Duplicate mutating call protection ────────────────────
            from lucy.pipeline.edge_cases import should_deduplicate_tool_call
            if should_deduplicate_tool_call(
                name, params, self._recent_tool_calls
            ):
                return call_id, json.dumps({
                    "error": (
                        f"Duplicate call to '{name}' blocked. "
                        f"this exact call was made <5 seconds ago. "
                        f"If you need to retry, wait a moment."
                    ),
                })

            # Track this call for dedup
            import time as _time
            self._recent_tool_calls.append((name, params, _time.monotonic()))
            # Prune old entries (keep last 30 seconds)
            cutoff = _time.monotonic() - 30.0
            self._recent_tool_calls = [
                c for c in self._recent_tool_calls if c[2] > cutoff
            ]

            # ── External API rate limiting ────────────────────────────
            from lucy.infra.rate_limiter import get_rate_limiter
            limiter = get_rate_limiter()
            api_name = limiter.classify_api_from_tool(name, params)
            if api_name:
                acquired = await limiter.acquire_api(api_name, timeout=15.0)
                if not acquired:
                    return call_id, json.dumps({
                        "error": (
                            f"Rate limited by {api_name}. "
                            f"Wait a moment and try again, or use a "
                            f"different approach."
                        ),
                    })

            if name == "COMPOSIO_MULTI_EXECUTE_TOOL":
                tools_list = params.get("tools") or params.get("actions") or []
                tools_key = "tools" if "tools" in params else "actions"
                misrouted: list[dict[str, Any]] = []
                clean_actions: list[Any] = []
                for act in tools_list:
                    act_name = ""
                    if isinstance(act, str):
                        act_name = act
                    elif isinstance(act, dict):
                        act_name = (
                            act.get("tool_slug") or act.get("action")
                            or act.get("tool") or ""
                        )
                    act_name_lower = str(act_name).lower()
                    if act_name_lower.startswith("lucy_custom_") or act_name_lower.startswith("lucy_"):
                        misrouted.append(act if isinstance(act, dict) else {"tool_slug": act})
                    else:
                        clean_actions.append(act)
                if misrouted:
                    logger.info(
                        "multi_execute_misroute_intercepted",
                        misrouted=[
                            (m.get("tool_slug") or m.get("action") or m.get("tool") or "?")
                            if isinstance(m, dict) else str(m)
                            for m in misrouted
                        ],
                    )
                    local_results = {}
                    for m in misrouted:
                        if isinstance(m, dict):
                            m_name = m.get("tool_slug") or m.get("action") or m.get("tool") or ""
                            m_params = m.get("arguments") or m.get("params") or m.get("input") or {}
                        else:
                            m_name = str(m)
                            m_params = {}
                        try:
                            r = await self._execute_internal_tool(
                                m_name, m_params, ctx.workspace_id,
                            )
                            local_results[m_name] = r
                        except Exception as e:
                            local_results[m_name] = {"error": str(e)}
                    if not clean_actions:
                        return call_id, json.dumps(local_results, default=str)
                    params[tools_key] = clean_actions

                from lucy.slack.hitl import is_destructive_tool_call
                for act in clean_actions:
                    act_name = act if isinstance(act, str) else (act.get("action") or act.get("tool") or "")
                    if is_destructive_tool_call(str(act_name)):
                        from lucy.slack.hitl import create_pending_action
                        action_id = create_pending_action(
                            tool_name=name,
                            parameters=params,
                            description=f"Destructive action detected: {act_name}",
                            workspace_id=ctx.workspace_id,
                        )
                        return call_id, json.dumps({
                            "status": "pending_approval",
                            "action_id": action_id,
                            "message": (
                                "This action requires user confirmation before execution. "
                                "Present an approval prompt to the user describing what "
                                "you're about to do. Include the action_id in your response."
                            ),
                        })

            async with trace.span(f"tool_exec_{name}", tool=name):
                result = await self._execute_tool(
                    name, params, ctx.workspace_id, ctx=ctx,
                )

            if name == "COMPOSIO_SEARCH_TOOLS":
                search_query = params.get("query") or params.get("search") or ""
                result = _filter_search_results(result)
                result = _validate_search_relevance(
                    result, search_query, trace.user_message,
                )

            if name == "COMPOSIO_MANAGE_CONNECTIONS" and isinstance(result, dict):
                toolkits_requested = params.get("toolkits") or []
                result = _validate_connection_relevance(
                    result, toolkits_requested, trace.user_message,
                )

            # Annotate unresolved services for the LLM to surface
            if name == "COMPOSIO_MANAGE_CONNECTIONS" and isinstance(result, dict):
                unresolved = result.pop("_unresolved_services", None)
                if unresolved:
                    svc_list = ", ".join(unresolved)
                    result["_dynamic_integration_hint"] = {
                        "unresolved_services": unresolved,
                        "instruction": (
                            f"These services ({svc_list}) do NOT exist in Composio. "
                            "Tell the user honestly: 'These services don't have "
                            "a native integration that I can connect to directly. "
                            "However, I can try to build a custom connection for "
                            "you. I can't guarantee it will work, but I'll do my "
                            "best. Want me to give it a shot?' "
                            "Do NOT attempt web scraping, Bright Data, or any "
                            "other workaround. Do NOT generate fake connection "
                            "links. When the user says yes, call "
                            "lucy_resolve_custom_integration with the service "
                            "names. That is the ONLY correct next step."
                        ),
                    }

            trace.tool_calls_made.append(name)
            return call_id, self._serialize_result(result)

        results = await asyncio.gather(
            *[_run_one(i, tc) for i, tc in enumerate(tool_calls)],
            return_exceptions=True,
        )
        safe_results: list[tuple[str, str]] = []
        for idx, r in enumerate(results):
            if isinstance(r, Exception):
                call_id = tool_calls[idx].get("id", f"call_{idx}")
                logger.warning(
                    "tool_gather_exception",
                    call_id=call_id,
                    error_type=type(r).__name__,
                    error=str(r)[:200],
                )
                safe_results.append((
                    call_id,
                    json.dumps({
                        "error": (
                            f"Tool execution failed: "
                            f"{type(r).__name__}: {str(r)[:200]}"
                        ),
                    }),
                ))
            else:
                safe_results.append(r)
        return safe_results

    # ── Tool execution ──────────────────────────────────────────────────

    async def _get_meta_tools(
        self, workspace_id: str
    ) -> list[dict[str, Any]]:
        """Fetch 5 Composio meta-tools for this workspace."""
        try:
            from lucy.integrations.composio_client import get_composio_client

            client = get_composio_client()
            tools = await client.get_tools(workspace_id)
            if tools is None:
                logger.warning("meta_tools_unavailable", workspace_id=workspace_id)
                return []
            return tools
        except Exception as e:
            logger.error("meta_tools_fetch_failed", error=str(e))
            return []

    async def _get_connected_services(
        self, workspace_id: str
    ) -> list[str]:
        """Fetch names of actively connected integrations.

        Merges Composio-managed connections with custom wrappers
        (Polar, Clerk, etc.) so the LLM sees the full picture.
        """
        names: list[str] = []
        try:
            from lucy.integrations.composio_client import get_composio_client

            client = get_composio_client()
            names = await client.get_connected_app_names_reliable(workspace_id)
        except Exception as e:
            logger.warning("composio_connections_fetch_failed", error=str(e))

        try:
            from lucy.integrations.wrapper_generator import discover_saved_wrappers

            for wrapper in discover_saved_wrappers():
                service = wrapper.get("service_name", "")
                if service and service not in names:
                    names.append(service)
        except Exception as e:
            logger.warning("custom_wrapper_discovery_failed", error=str(e))

        if names:
            logger.info(
                "connected_services_fetched",
                workspace_id=workspace_id,
                services=names,
            )
        return names

    # Per-tool timeout config (seconds). Longer for heavy operations.
    _TOOL_TIMEOUTS: dict[str, float] = {
        "lucy_start_service": 30.0,
        "lucy_stop_service": 15.0,
        "lucy_service_logs": 20.0,
        "lucy_create_cron": 15.0,
        "lucy_generate_excel": 120.0,
        "lucy_generate_csv": 120.0,
        "lucy_spaces_init": 60.0,
        "lucy_spaces_deploy": 180.0,
        "lucy_create_heartbeat": 20.0,
        "COMPOSIO_REMOTE_WORKBENCH": 300.0,
        "COMPOSIO_REMOTE_BASH_TOOL": 180.0,
    }
    _DEFAULT_TOOL_TIMEOUT = 60.0

    # Simple in-memory circuit breaker: tool -> consecutive failure count
    _tool_failure_counts: dict[str, int] = {}
    _tool_circuit_opened_at: dict[str, float] = {}
    _CIRCUIT_OPEN_THRESHOLD = 3
    _CIRCUIT_COOLDOWN_SECONDS = 60.0

    async def _execute_tool(
        self,
        tool_name: str,
        parameters: dict[str, Any],
        workspace_id: str,
        ctx: AgentContext | None = None,
    ) -> dict[str, Any]:
        """Execute a single tool call with per-tool timeout and circuit breaker.

        Internal tools (lucy_*) are handled locally.
        Everything else routes through Composio.
        """
        # ── Circuit breaker: skip tools that have repeatedly failed ──
        failure_count = LucyAgent._tool_failure_counts.get(tool_name, 0)
        if failure_count >= LucyAgent._CIRCUIT_OPEN_THRESHOLD:
            opened_at = LucyAgent._tool_circuit_opened_at.get(tool_name)
            now = time.monotonic()
            if opened_at is None:
                LucyAgent._tool_circuit_opened_at[tool_name] = now
                logger.warning(
                    "tool_circuit_open",
                    tool=tool_name,
                    failures=failure_count,
                )
                human_name = LucyAgent._TOOL_HUMAN_NAMES.get(tool_name, tool_name)
                return {
                    "error": (
                        f"The tool for '{human_name}' has failed {failure_count} times "
                        f"in a row and has been temporarily paused. Try a different "
                        f"approach or ask the user if they want to retry."
                    ),
                    "_circuit_breaker": True,
                }
            elapsed_since_open = now - opened_at
            if elapsed_since_open < LucyAgent._CIRCUIT_COOLDOWN_SECONDS:
                human_name = LucyAgent._TOOL_HUMAN_NAMES.get(tool_name, tool_name)
                remaining = int(LucyAgent._CIRCUIT_COOLDOWN_SECONDS - elapsed_since_open)
                return {
                    "error": (
                        f"The tool for '{human_name}' is still paused "
                        f"(~{remaining}s remaining). Try a different approach."
                    ),
                    "_circuit_breaker": True,
                }
            # Half-open: allow one probe call after cooldown
            logger.info(
                "tool_circuit_half_open",
                tool=tool_name,
                elapsed_since_open=int(elapsed_since_open),
            )
            LucyAgent._tool_failure_counts[tool_name] = 0
            LucyAgent._tool_circuit_opened_at.pop(tool_name, None)

        timeout = LucyAgent._TOOL_TIMEOUTS.get(tool_name, LucyAgent._DEFAULT_TOOL_TIMEOUT)

        # ── Internal tools (no external API needed) ──────────────────
        if tool_name.startswith("lucy_"):
            try:
                result = await asyncio.wait_for(
                    self._execute_internal_tool(
                        tool_name, parameters, workspace_id, ctx=ctx,
                    ),
                    timeout=timeout,
                )
                has_error = isinstance(result, dict) and bool(result.get("error"))
                logger.info(
                    "tool_executed",
                    tool=tool_name,
                    workspace_id=workspace_id,
                    has_error=has_error,
                    result_preview=(
                        str(result.get("error", ""))[:200]
                        if has_error and isinstance(result, dict) else ""
                    ),
                )
                LucyAgent._tool_failure_counts.pop(tool_name, None)
                return result
            except asyncio.TimeoutError:
                LucyAgent._tool_failure_counts[tool_name] = (
                    LucyAgent._tool_failure_counts.get(tool_name, 0) + 1
                )
                human_name = LucyAgent._TOOL_HUMAN_NAMES.get(tool_name, tool_name)
                logger.warning(
                    "tool_timeout",
                    tool=tool_name,
                    timeout=timeout,
                    workspace_id=workspace_id if ctx else "unknown",
                )
                return {
                    "error": (
                        f"'{human_name}' timed out after {int(timeout)}s. "
                        f"Try a lighter approach or break the task into smaller steps."
                    ),
                    "_timed_out": True,
                }

        # ── Delegation to sub-agents ─────────────────────────────────
        if tool_name.startswith("delegate_to_") and tool_name.endswith("_agent"):
            return await self._handle_delegation(
                tool_name, parameters, workspace_id,
            )

        # ── External tools (via Composio) ────────────────────────────
        try:
            from lucy.integrations.composio_client import get_composio_client

            client = get_composio_client()
            result = await asyncio.wait_for(
                client.execute_tool_call(
                    workspace_id=workspace_id,
                    tool_name=tool_name,
                    arguments=parameters,
                ),
                timeout=timeout,
            )

            result_str = json.dumps(result)[:500] if isinstance(result, dict) else str(result)[:500]
            has_real_error = False
            if isinstance(result, dict):
                err_val = result.get("error")
                if err_val and str(err_val).strip():
                    has_real_error = True
                data = result.get("data", {})
                if isinstance(data, dict):
                    data_err = data.get("error")
                    if data_err and str(data_err).strip():
                        has_real_error = True
            logger.info(
                "tool_executed",
                tool=tool_name,
                workspace_id=workspace_id,
                has_error=has_real_error,
                result_preview=result_str[:200] if has_real_error else "",
            )
            # Reset circuit breaker on success
            LucyAgent._tool_failure_counts.pop(tool_name, None)
            return result

        except asyncio.TimeoutError:
            LucyAgent._tool_failure_counts[tool_name] = (
                LucyAgent._tool_failure_counts.get(tool_name, 0) + 1
            )
            human_name = LucyAgent._TOOL_HUMAN_NAMES.get(tool_name, tool_name)
            logger.warning(
                "composio_tool_timeout",
                tool=tool_name,
                timeout=timeout,
            )
            return {
                "error": (
                    f"'{human_name}' timed out after {int(timeout)}s. "
                    f"Try a lighter approach or break the task into smaller steps."
                ),
                "_timed_out": True,
            }
        except Exception as e:
            LucyAgent._tool_failure_counts[tool_name] = (
                LucyAgent._tool_failure_counts.get(tool_name, 0) + 1
            )
            logger.error(
                "tool_execution_failed",
                tool=tool_name,
                error=str(e),
            )
            sanitized_error = _INTERNAL_PATH_RE.sub("[path]", str(e))
            sanitized_error = _WORKSPACE_PATH_RE.sub("[path]", sanitized_error)
            if len(sanitized_error) > 300:
                sanitized_error = sanitized_error[:300] + "..."
            human_name = LucyAgent._TOOL_HUMAN_NAMES.get(tool_name, tool_name)
            return {
                "error": (
                    f"'{human_name}' encountered an error: {sanitized_error}"
                ),
            }

    _CRON_MANAGEMENT_TOOLS = frozenset({
        "lucy_create_cron", "lucy_delete_cron",
        "lucy_modify_cron", "lucy_trigger_cron",
        "lucy_create_heartbeat", "lucy_delete_heartbeat",
    })

    async def _execute_internal_tool(
        self,
        tool_name: str,
        parameters: dict[str, Any],
        workspace_id: str,
        ctx: AgentContext | None = None,
    ) -> dict[str, Any]:
        """Execute an internal (lucy_*) tool — no Composio, no external API."""
        if ctx and ctx.is_cron_execution and tool_name in self._CRON_MANAGEMENT_TOOLS:
            return {
                "error": (
                    f"Cron management tool '{tool_name}' cannot be called "
                    f"during cron execution. This prevents recursive cron chains."
                ),
            }

        from lucy.workspace.filesystem import get_workspace

        ws = get_workspace(workspace_id)

        try:
            if tool_name == "lucy_list_crons":
                from lucy.crons.scheduler import get_scheduler
                scheduler = get_scheduler()
                jobs = scheduler.list_jobs()
                if not jobs:
                    return {"result": "No scheduled tasks are currently active."}

                _SYSTEM_CRONS = {
                    "slack sync", "slack message sync",
                    "memory consolidation", "humanize pool refresh",
                }
                user_jobs = []
                system_jobs = []
                for j in jobs:
                    name = j.get("name", "Unknown")
                    name = re.sub(
                        r"\s*\([0-9a-f-]{36}\)", "", name,
                    )
                    next_run = j.get("next_run")
                    if next_run:
                        try:
                            from datetime import datetime as _dt
                            dt = _dt.fromisoformat(next_run)
                            next_run = dt.strftime("%A, %B %d at %I:%M %p")
                        except Exception as e:
                            logger.warning("component_failed", component="cron_date_format", error=str(e))
                    entry: dict[str, Any] = {
                        "name": name,
                        "next_run": next_run or "Not scheduled",
                    }
                    if "schedule" in j:
                        entry["schedule"] = j["schedule"]
                    if "description" in j:
                        entry["what_it_does"] = j["description"]
                    if "timezone" in j:
                        entry["timezone"] = j["timezone"]
                    if "created_at" in j and j["created_at"]:
                        entry["created_at"] = j["created_at"]

                    if name.lower().strip() in _SYSTEM_CRONS:
                        system_jobs.append(entry)
                    else:
                        user_jobs.append(entry)

                return {
                    "user_tasks": user_jobs,
                    "system_tasks_count": len(system_jobs),
                    "note": (
                        "user_tasks are tasks set up by or for the user. "
                        "system_tasks are internal Lucy maintenance tasks "
                        "(message sync, memory, etc.) that run automatically. "
                        "Only mention system tasks if the user specifically "
                        "asks about internal/system tasks."
                    ),
                }

            if tool_name == "lucy_create_cron":
                from lucy.crons.scheduler import get_scheduler
                scheduler = get_scheduler()
                channel = (ctx.channel_id if ctx else None) or ""
                user_id = (ctx.user_slack_id if ctx else None) or ""
                mode = parameters.get("delivery_mode", "channel")
                cron_type = parameters.get("type", "agent")
                condition_script_path = parameters.get("condition_script_path", "")
                max_runs = parameters.get("max_runs", 0)
                depends_on = parameters.get("depends_on", "")
                
                return await scheduler.create_cron(
                    workspace_id=workspace_id,
                    name=parameters.get("name", ""),
                    cron_expr=parameters.get("cron_expression", ""),
                    title=parameters.get("title", ""),
                    description=parameters.get("description", ""),
                    tz=parameters.get("timezone", ""),
                    delivery_channel=channel,
                    requesting_user_id=user_id,
                    delivery_mode=mode,
                    type=cron_type,
                    condition_script_path=condition_script_path,
                    max_runs=max_runs,
                    depends_on=depends_on,
                )

            if tool_name == "lucy_delete_cron":
                from lucy.crons.scheduler import get_scheduler
                scheduler = get_scheduler()
                return await scheduler.delete_cron(
                    workspace_id=workspace_id,
                    cron_name=parameters.get("cron_name", ""),
                )

            if tool_name == "lucy_modify_cron":
                from lucy.crons.scheduler import get_scheduler
                scheduler = get_scheduler()
                return await scheduler.modify_cron(
                    workspace_id=workspace_id,
                    cron_name=parameters.get("cron_name", ""),
                    new_cron_expr=parameters.get("new_cron_expression"),
                    new_description=parameters.get("new_description"),
                    new_title=parameters.get("new_title"),
                    new_tz=parameters.get("new_timezone"),
                )

            if tool_name == "lucy_trigger_cron":
                from lucy.crons.scheduler import get_scheduler
                scheduler = get_scheduler()
                cron_name = parameters.get("cron_name", "")
                slug = cron_name.lower().replace(" ", "-")
                slug = "".join(
                    c for c in slug if c.isalnum() or c == "-"
                )
                triggered = await scheduler.trigger_now(
                    workspace_id, f"/{slug}",
                )
                if triggered:
                    return {
                        "success": True,
                        "message": f"Task '{cron_name}' triggered and running now.",
                    }
                return {
                    "success": False,
                    "error": f"Task '{cron_name}' not found.",
                }

            # ── Heartbeat monitor tools ──────────────────────────────
            if tool_name == "lucy_create_heartbeat":
                from lucy.crons.heartbeat import create_heartbeat
                channel_id = parameters.pop("alert_channel_id", None)
                if not channel_id and ctx:
                    channel_id = ctx.channel_id
                return await create_heartbeat(
                    workspace_id=workspace_id,
                    alert_channel_id=channel_id,
                    **parameters,
                )

            if tool_name == "lucy_delete_heartbeat":
                from lucy.crons.heartbeat import delete_heartbeat
                return await delete_heartbeat(
                    workspace_id=workspace_id,
                    name=parameters.get("name", ""),
                )

            if tool_name == "lucy_list_heartbeats":
                from lucy.crons.heartbeat import list_heartbeats
                heartbeats = await list_heartbeats(workspace_id)
                if not heartbeats:
                    return {"heartbeats": [], "message": "No heartbeat monitors configured."}
                return {"heartbeats": heartbeats, "count": len(heartbeats)}

            if tool_name.startswith("lucy_search_slack_history") or \
               tool_name.startswith("lucy_get_channel_history"):
                from lucy.workspace.history_search import execute_history_tool
                result_text = await execute_history_tool(ws, tool_name, parameters)
                return {"result": result_text}

            if tool_name in ["lucy_write_file", "lucy_edit_file"] or tool_name.startswith("lucy_generate_"):
                from lucy.tools.file_generator import execute_file_tool
                return await execute_file_tool(
                    tool_name=tool_name,
                    parameters=parameters,
                    slack_client=self._current_slack_client,
                    channel_id=self._current_channel_id,
                    thread_ts=self._current_thread_ts,
                )

            if tool_name == "lucy_resolve_custom_integration":
                from lucy.integrations.resolver import resolve_multiple
                services = parameters.get("services", [])
                if not services:
                    return {"error": "No service names provided"}
                results = await resolve_multiple(services)
                formatted = []
                for r in results:
                    entry: dict[str, Any] = {
                        "service": r.service_name,
                        "success": r.success,
                        "stage": r.stage.value,
                        "needs_api_key": r.needs_api_key,
                        "timing_ms": r.timing_ms,
                        **r.result_data,
                    }
                    if r.success and r.needs_api_key:
                        slug = r.service_name.lower().replace(
                            " ", ""
                        ).replace(".", "").replace("-", "")
                        entry["next_step"] = (
                            f"Describe what you built and what you can now "
                            f"help with IN YOUR OWN WORDS based on the data "
                            f"above. Then ask for their {r.service_name} API "
                            f"key. When they provide it, call "
                            f"lucy_store_api_key with service_slug='{slug}' "
                            f"and the key. Do NOT skip this step."
                        )
                    elif r.success:
                        entry["next_step"] = (
                            "Describe the result to the user IN YOUR OWN "
                            "WORDS. Mention what capabilities are now "
                            "available based on the data above."
                        )
                    else:
                        entry["next_step"] = (
                            "Explain honestly that you could not build the "
                            "integration. Use the reasons from the data "
                            "above. Offer alternatives if appropriate."
                        )
                    formatted.append(entry)
                return {"results": formatted}

            if tool_name == "lucy_store_api_key":
                return await self._store_api_key(parameters)

            if tool_name == "lucy_delete_custom_integration":
                return self._delete_custom_integration(parameters)

            from lucy.tools.spaces import is_spaces_tool
            if is_spaces_tool(tool_name):
                from lucy.tools.spaces import execute_spaces_tool
                return await execute_spaces_tool(
                    tool_name, parameters, workspace_id,
                )

            from lucy.tools.email_tools import is_email_tool
            if is_email_tool(tool_name):
                from lucy.tools.email_tools import execute_email_tool
                return await execute_email_tool(tool_name, parameters)

            from lucy.tools.web_search import is_web_search_tool
            if is_web_search_tool(tool_name):
                from lucy.tools.web_search import execute_web_search
                return await execute_web_search(parameters)

            from lucy.tools.services import is_service_tool
            if is_service_tool(tool_name):
                from lucy.tools.services import execute_service_tool
                return await execute_service_tool(tool_name, parameters)

            if tool_name.startswith("lucy_custom_"):
                return await self._execute_custom_wrapper_tool(
                    tool_name, parameters, workspace_id,
                )

            logger.warning("unknown_internal_tool", tool=tool_name)
            return {"error": f"Unknown internal tool: {tool_name}"}

        except Exception as e:
            logger.error(
                "internal_tool_failed",
                tool=tool_name,
                error=str(e),
            )
            sanitized = _INTERNAL_PATH_RE.sub("[path]", str(e))
            sanitized = _WORKSPACE_PATH_RE.sub("[path]", sanitized)
            if len(sanitized) > 300:
                sanitized = sanitized[:300] + "..."
            return {"error": sanitized}

    # ── Custom wrapper tool execution ────────────────────────────────

    async def _execute_custom_wrapper_tool(
        self,
        tool_name: str,
        parameters: dict[str, Any],
        workspace_id: str,
    ) -> dict[str, Any]:
        """Execute a custom wrapper tool (lucy_custom_<slug>_<action>)."""
        stripped = tool_name.removeprefix("lucy_custom_")

        from lucy.integrations.custom_wrappers import execute_custom_tool

        keys_path = Path(settings.workspace_root).parent / "keys.json"
        api_key = ""
        if keys_path.exists():
            try:
                keys_data = json.loads(keys_path.read_text(encoding="utf-8"))
                slug = stripped.split("_", 1)[0]
                api_key = (
                    keys_data
                    .get("custom_integrations", {})
                    .get(slug, {})
                    .get("api_key", "")
                )
            except Exception as e:
                logger.warning("component_failed", component="custom_wrapper_key_load", error=str(e))

        if not api_key:
            return {
                "error": (
                    "No API key configured for this custom integration. "
                    "Ask the user to provide their API key, then store it "
                    "using the lucy_store_api_key tool."
                ),
            }

        result = execute_custom_tool(stripped, parameters, api_key)

        if asyncio.iscoroutine(result):
            result = await result

        logger.info(
            "custom_wrapper_tool_executed",
            tool=tool_name,
            workspace_id=workspace_id,
        )
        out = result if isinstance(result, dict) else {"result": result}

        out = self._validate_custom_tool_result(out, tool_name, parameters)

        excel_path = out.get("excel_file_path")
        if not hasattr(self, "_uploaded_files"):
            self._uploaded_files: set[str] = set()
        if (
            excel_path
            and self._current_slack_client
            and self._current_channel_id
            and excel_path not in self._uploaded_files
        ):
            try:
                from lucy.tools.file_generator import upload_file_to_slack
                upload_res = await upload_file_to_slack(
                    slack_client=self._current_slack_client,
                    file_path=Path(excel_path),
                    channel_id=self._current_channel_id,
                    thread_ts=self._current_thread_ts,
                    title=Path(excel_path).stem.replace("_", " "),
                )
                out["upload_status"] = "uploaded_to_slack"
                self._uploaded_files.add(excel_path)
                out.pop("excel_file_path", None)
                logger.info(
                    "auto_uploaded_excel",
                    file=Path(excel_path).name,
                    channel=self._current_channel_id,
                )
            except Exception as e:
                logger.warning("auto_upload_failed", error=str(e))
                out["upload_status"] = f"upload failed: {e}"

        return out

    @staticmethod
    def _validate_custom_tool_result(
        result: dict[str, Any],
        tool_name: str,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        """Add data quality warnings for custom tool results."""
        if "error" in result:
            return result

        data = result.get("result") or result.get("users_shown") or result
        if isinstance(data, list):
            count = len(data)
            limit = parameters.get("limit")
            if limit and count >= int(limit):
                result["_data_warning"] = (
                    f"IMPORTANT: The API returned exactly {count} items, "
                    f"which equals the requested limit of {limit}. This "
                    f"likely means there are MORE items available. Tell "
                    f"the user the total might be higher and offer to "
                    f"fetch more pages if they need a complete count."
                )
            if count <= 5 and not parameters.get("query"):
                result["_accuracy_note"] = (
                    f"Only {count} items returned. If the user expected "
                    f"more, the API query or parameters may need adjustment. "
                    f"Mention the count honestly and ask if it seems right."
                )
        return result

    async def _store_api_key(
        self,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        """Store a user-provided API key for a custom integration."""
        slug = parameters.get("service_slug", "").strip()
        api_key = parameters.get("api_key", "").strip()

        if not slug:
            return {"error": "service_slug is required"}
        if not api_key:
            return {"error": "api_key is required"}

        keys_path = Path(settings.workspace_root).parent / "keys.json"
        keys_data: dict[str, Any] = {}
        if keys_path.exists():
            try:
                keys_data = json.loads(keys_path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning("component_failed", component="keys_file_read", error=str(e))

        custom = keys_data.setdefault("custom_integrations", {})
        custom.setdefault(slug, {})["api_key"] = api_key

        keys_path.write_text(
            json.dumps(keys_data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        logger.info("api_key_stored", slug=slug)
        return {
            "result": f"API key for '{slug}' stored successfully.",
            "slug": slug,
        }

    def _delete_custom_integration(
        self,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        """Handle delete integration requests with confirmation flow."""
        slug = parameters.get("service_slug", "").strip().lower()
        confirmed = parameters.get("confirmed", False)

        if not slug:
            return {"error": "service_slug is required"}

        from lucy.integrations.wrapper_generator import (
            delete_custom_wrapper,
            discover_saved_wrappers,
        )

        wrappers = discover_saved_wrappers()
        match = next((w for w in wrappers if w.get("slug") == slug), None)

        if not match:
            return {
                "error": f"No custom integration found for '{slug}'",
                "instruction": (
                    "Tell the user this integration does not exist. "
                    "If they meant a different service, ask them to clarify."
                ),
            }

        service_name = match.get("service_name", slug)
        tool_count = match.get("total_tools", 0)
        tool_samples = match.get("tools", [])[:5]
        sample_str = ", ".join(tool_samples)

        if not confirmed:
            return {
                "preview": True,
                "service_name": service_name,
                "slug": slug,
                "tool_count": tool_count,
                "instruction": (
                    f"Ask the user to confirm deletion. Tell them: "
                    f"'Removing the {service_name} integration will "
                    f"delete {tool_count} capabilities (like {sample_str}"
                    f"{'...' if tool_count > 5 else ''}). "
                    f"You will no longer be able to ask me to do anything "
                    f"with {service_name} until we rebuild it. "
                    f"Are you sure you want to proceed?'"
                ),
            }

        result = delete_custom_wrapper(slug)
        if "error" in result:
            return result

        return {
            "deleted": True,
            "service_name": service_name,
            "instruction": (
                f"Tell the user: 'Done, I've removed the {service_name} "
                f"integration and all {tool_count} capabilities. "
                f"If you ever want to reconnect, just ask me to connect "
                f"with {service_name} again and I'll rebuild it.'"
            ),
        }

    # ── Sub-agent delegation ──────────────────────────────────────────

    async def _handle_delegation(
        self,
        tool_name: str,
        parameters: dict[str, Any],
        workspace_id: str,
    ) -> dict[str, Any]:
        """Handle a delegate_to_*_agent tool call."""
        from lucy.core.sub_agents import REGISTRY, SUB_TIMEOUT_SECONDS, run_subagent

        agent_type = tool_name.removeprefix("delegate_to_").removesuffix("_agent")
        spec = REGISTRY.get(agent_type)
        if not spec:
            return {"error": f"Unknown agent type: {agent_type}"}

        task = parameters.get("task", "")
        if not task:
            return {"error": "No task description provided"}

        # Store task hint for progress messages
        self._current_task_hint = task[:80] if task else None

        # Post delegation start message so user isn't left in silence
        _DELEGATION_LABELS = {
            "research": "Doing a deep dive on this",
            "code": "Writing and testing the code",
            "integrations": "Setting up the integration",
            "document": "Putting the document together",
        }
        start_label = _DELEGATION_LABELS.get(agent_type, "Working on this")
        task_hint = self._current_task_hint
        start_msg = (
            f"{start_label} — {task_hint[:60]}..." if task_hint
            else f"{start_label}..."
        )
        slack_client = getattr(self, "_current_slack_client", None)
        channel_id = getattr(self, "_current_channel_id", None)
        thread_ts = getattr(self, "_current_thread_ts", None)
        if slack_client and channel_id and thread_ts:
            try:
                await slack_client.chat_postMessage(
                    channel=channel_id, thread_ts=thread_ts, text=start_msg,
                )
            except Exception as e:
                logger.warning("component_failed", component="delegation_start_message", error=str(e))

        try:
            result = await asyncio.wait_for(
                run_subagent(
                    task=task,
                    spec=spec,
                    workspace_id=workspace_id,
                    tool_registry=self._tool_registry,
                    progress_callback=None,
                ),
                timeout=SUB_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            self._current_task_hint = None
            logger.warning(
                "subagent_timeout",
                agent=agent_type,
                workspace_id=workspace_id,
            )
            return {"error": f"The {agent_type} specialist took too long. Try a simpler task."}

        # Memory extraction from sub-agent result
        if result:
            try:
                from lucy.workspace.filesystem import get_workspace
                from lucy.workspace.memory import add_session_fact, should_persist_memory

                if should_persist_memory(result):
                    ws = get_workspace(workspace_id)
                    fact = result[:300]
                    await add_session_fact(
                        ws, fact,
                        source=f"sub_agent:{agent_type}",
                        category="session",
                        thread_ts=getattr(self, "_current_thread_ts", None),
                    )
            except Exception as e:
                logger.warning("subagent_memory_persist_error", error=str(e))

        self._current_task_hint = None
        return {"result": result}

    # ── Quality gate escalation ────────────────────────────────────────

    async def _escalate_response(
        self,
        user_message: str,
        original_response: str,
        issues: str,
        ctx: AgentContext,
    ) -> str | None:
        """Ask frontier model to correct a response flagged by the quality gate.

        Single LLM call (~$0.003) — only triggered when heuristics detect
        a likely error. Returns corrected text, or None if original is fine.
        """
        from lucy.pipeline.router import MODEL_TIERS

        correction_prompt = (
            f"A user sent this message:\n\"{user_message}\"\n\n"
            f"An AI assistant responded:\n\"{original_response}\"\n\n"
            f"Quality check detected these issues:\n{issues}\n\n"
            f"If the response has real problems (wrong services, "
            f"incorrect information, service name confusion), provide "
            f"a CORRECTED response that addresses the user's actual "
            f"request. Keep the same tone and style.\n\n"
            f"If the original response is actually fine and the issues "
            f"are false positives, respond with exactly: RESPONSE_OK"
        )

        try:
            client = await get_openclaw_client()
            result = await asyncio.wait_for(
                client.chat_completion(
                    messages=[{"role": "user", "content": correction_prompt}],
                    config=ChatConfig(
                        model=MODEL_TIERS["frontier"],
                        system_prompt=(
                            "You are a quality auditor for an AI assistant "
                            "named Lucy. Your job is to catch and fix errors "
                            "in her responses, especially service name "
                            "confusion (e.g., Clerk ≠ MoonClerk), wrong "
                            "suggestions, and hallucinated capabilities. "
                            "Be concise. If the response is fine, say "
                            "RESPONSE_OK."
                        ),
                        max_tokens=4096,
                    ),
                ),
                timeout=15.0,
            )

            corrected = (result.content or "").strip()
            if "RESPONSE_OK" in corrected:
                logger.info("quality_gate_original_ok")
                return None

            logger.info(
                "quality_gate_corrected",
                original_len=len(original_response),
                corrected_len=len(corrected),
            )
            return corrected

        except Exception as exc:
            logger.warning(
                "quality_gate_escalation_failed",
                error=str(exc) or type(exc).__name__,
            )
            return None

    # ── Self-critique gate ──────────────────────────────────────────────

    async def _self_critique(
        self,
        user_message: str,
        response_text: str,
        intent: str,
        model: str,
        ctx: "AgentContext",
    ) -> str:
        """Cheap LLM self-review for complex responses before delivery.

        Uses the fast model to check completeness and value-first framing.
        Returns the improved response if changes are warranted, or the
        original if it passes. Never blocks delivery on failure.
        """
        from lucy.pipeline.router import MODEL_TIERS

        critique_prompt = (
            f"A user asked:\n\"{user_message[:400]}\"\n\n"
            f"Here is the response that was generated (intent: {intent}):\n"
            f"\"\"\"\n{response_text[:1500]}\n\"\"\"\n\n"
            f"Quickly review this response. Check:\n"
            f"1. Does it answer EVERY part of the user's question?\n"
            f"2. Does it lead with the most important information (not preamble)?\n"
            f"3. Is anything obviously missing or incomplete?\n"
            f"4. Does it give actual data/results, not just say what was done?\n"
            f"5. HIGH-AGENCY CHECK: Does the response end with a dead end "
            f"anywhere? Does it say 'I can't' or 'I wasn't able to' without "
            f"offering an alternative path? A high-agency response always "
            f"provides a next step, a workaround, or what would unblock it.\n\n"
            f"If the response is solid, reply with exactly: RESPONSE_OK\n"
            f"If there's a clear, specific issue, reply with: ISSUE: [one sentence "
            f"describing what's missing or wrong, and how to fix it]\n\n"
            f"Be strict but fair. Don't flag minor style issues. Only flag substantive "
            f"completeness or accuracy problems, and dead-end responses."
        )

        try:
            client = await get_openclaw_client()
            result = await asyncio.wait_for(
                client.chat_completion(
                    messages=[{"role": "user", "content": critique_prompt}],
                    config=ChatConfig(
                        model=MODEL_TIERS.get("fast", model),
                        system_prompt=(
                            "You are a strict quality reviewer for AI responses. "
                            "Your job: catch substantive failures (incomplete answers, "
                            "missing deliverables, preamble without results). "
                            "Ignore minor style issues. Be decisive and concise."
                        ),
                        max_tokens=500,
                        temperature=0.1,
                    ),
                ),
                timeout=12.0,
            )

            critique = (result.content or "").strip()

            if "RESPONSE_OK" in critique or not critique.startswith("ISSUE:"):
                logger.debug(
                    "self_critique_passed",
                    workspace_id=ctx.workspace_id,
                    intent=intent,
                )
                return response_text

            issue = critique.replace("ISSUE:", "").strip()
            logger.info(
                "self_critique_issue_detected",
                workspace_id=ctx.workspace_id,
                issue=issue[:200],
                intent=intent,
            )

            # One retry pass with the critique injected
            from lucy.pipeline.router import MODEL_TIERS
            retried = await self.run(
                message=user_message,
                ctx=ctx,
                slack_client=getattr(self, "_current_slack_client", None),
                model_override=MODEL_TIERS.get("code", model),
                failure_context=(
                    f"Self-review found an issue with the previous response: {issue}. "
                    f"Fix this specific problem and deliver a complete answer."
                ),
                _retry_depth=1,
            )
            if retried and len(retried) > len(response_text) * 0.5:
                logger.info(
                    "self_critique_retry_succeeded",
                    workspace_id=ctx.workspace_id,
                )
                return retried

        except Exception as exc:
            logger.warning(
                "self_critique_error",
                workspace_id=ctx.workspace_id,
                error=str(exc) or type(exc).__name__,
            )

        return response_text

    # ── Slack thread history ────────────────────────────────────────────

    async def _build_thread_messages(
        self,
        ctx: AgentContext,
        current_text: str,
        slack_client: Any | None = None,
    ) -> list[dict[str, Any]]:
        """Build LLM messages from Slack thread history."""
        messages: list[dict[str, Any]] = []

        if ctx.thread_ts and ctx.channel_id and slack_client:
            try:
                result = await slack_client.conversations_replies(
                    channel=ctx.channel_id,
                    ts=ctx.thread_ts,
                    limit=20,
                )
                thread_msgs = result.get("messages", [])

                for msg in thread_msgs[:-1]:
                    text = msg.get("text", "").strip()
                    if not text:
                        continue

                    is_bot = bool(msg.get("bot_id")) or bool(
                        msg.get("app_id")
                    )
                    if is_bot:
                        messages.append(
                            {"role": "assistant", "content": text}
                        )
                    else:
                        cleaned = re.sub(
                            r"<@[A-Z0-9]+>\s*", "", text
                        ).strip()
                        if cleaned:
                            messages.append(
                                {"role": "user", "content": cleaned}
                            )

                logger.debug(
                    "thread_history_loaded",
                    thread_ts=ctx.thread_ts,
                    count=len(messages),
                )

            except Exception as e:
                logger.warning("thread_history_error", error=str(e))

        messages.append({"role": "user", "content": current_text})
        return messages

    # ── Helpers ──────────────────────────────────────────────────────────

    _CUSTOM_INTEGRATION_OFFER_PHRASES = (
        "custom connection",
        "custom integration",
        "build a custom",
        "try to build",
        "i can try to build",
    )

    _USER_CONSENT_PHRASES = (
        "yes",
        "go ahead",
        "please build",
        "build it",
        "do it",
        "sure",
        "let's do it",
        "try it",
        "give it a shot",
        "go for it",
    )

    _API_KEY_PATTERNS = re.compile(
        r"(api[_ ]?key|token|secret|credential)s?\s*[:=]?\s*\S+|"
        r"\b[a-zA-Z0-9_]{20,}\b",
        re.IGNORECASE,
    )

    def _detect_custom_integration_context(
        self,
        messages: list[dict[str, Any]],
        current_text: str,
    ) -> str | None:
        """Detect custom integration consent or API key in thread context.

        Returns a system-level nudge message if the context warrants it,
        otherwise None.
        """
        lower_current = current_text.lower()

        offered_service: str | None = None
        for msg in messages:
            if msg.get("role") != "assistant":
                continue
            content = (msg.get("content") or "").lower()
            if any(p in content for p in self._CUSTOM_INTEGRATION_OFFER_PHRASES):
                for m in messages:
                    if m.get("role") == "user":
                        user_text = (m.get("content") or "").lower()
                        for kw in ("connect with", "connect to", "connect"):
                            if kw in user_text:
                                after = user_text.split(kw, 1)[-1].strip()
                                offered_service = after.split("\n")[0].strip(
                                    " .!?,;:"
                                )
                                break
                        if offered_service:
                            break
                break

        if not offered_service:
            return None

        is_consent = any(p in lower_current for p in self._USER_CONSENT_PHRASES)
        has_api_key = bool(self._API_KEY_PATTERNS.search(current_text))

        if is_consent and has_api_key:
            return (
                f"<custom_integration_directive>\n"
                f"The user has consented to building a custom integration for "
                f"\"{offered_service}\" AND provided an API key in this message.\n"
                f"You MUST do TWO things in this turn:\n"
                f"1. Call lucy_store_api_key with service_slug and the key.\n"
                f"2. Call lucy_resolve_custom_integration with [\"{offered_service}\"].\n"
                f"Do NOT use Bright Data. Do NOT scrape. Do NOT suggest alternatives.\n"
                f"</custom_integration_directive>"
            )

        if is_consent:
            return (
                f"<custom_integration_directive>\n"
                f"The user has consented to building a custom integration for "
                f"\"{offered_service}\". You MUST call "
                f"lucy_resolve_custom_integration([\"{offered_service}\"]) NOW.\n"
                f"Do NOT ask for the API key yet. The resolver will handle "
                f"research and code generation first. Ask for the key AFTER "
                f"the resolver completes.\n"
                f"Do NOT use Bright Data. Do NOT scrape. Do NOT suggest alternatives.\n"
                f"</custom_integration_directive>"
            )

        if has_api_key:
            return (
                f"<custom_integration_directive>\n"
                f"The user is providing an API key for \"{offered_service}\". "
                f"Extract the key from their message and call "
                f"lucy_store_api_key with service_slug and the key.\n"
                f"Then call lucy_resolve_custom_integration([\"{offered_service}\"]) "
                f"to build the integration.\n"
                f"Do NOT use Bright Data or any scraping tool.\n"
                f"</custom_integration_directive>"
            )

        return None

    @staticmethod
    def _claims_no_access(text: str) -> bool:
        lower = text.lower()
        return any(
            phrase in lower
            for phrase in (
                "don't have access",
                "do not have access",
                "not connected",
                "need to connect",
                "no access to",
            )
        )

    @staticmethod
    def _is_simple_greeting(text: str) -> bool:
        """Check if response is a simple greeting/acknowledgment."""
        lower = text.strip().lower()
        return len(lower) < 80 and any(
            w in lower
            for w in ("hey", "hi", "hello", "how can i help", "what do you need")
        )

    @staticmethod
    def _is_history_reference(message: str) -> bool:
        """Check if a message references past context or discussions."""
        lower = message.lower()
        return any(phrase in lower for phrase in (
            "what did we", "last time", "remember", "you mentioned",
            "we discussed", "earlier", "previously", "follow up",
            "didn't we", "wasn't there", "what about the",
            "we agreed", "we decided", "you said", "that conversation",
            "that decision", "status of",
        ))

    @staticmethod
    def _extract_search_terms(message: str) -> list[str]:
        """Extract meaningful search terms from a message."""
        cleaned = re.sub(
            r"\b(what|did|we|the|about|you|remember|last|time|earlier|"
            r"previously|that|this|when|where)\b",
            "",
            message.lower(),
        )
        words = [w.strip() for w in cleaned.split() if len(w.strip()) > 3]
        return words[:3]

    @staticmethod
    def _call_signature(tool_calls: list[dict[str, Any]]) -> str:
        parts = []
        for tc in tool_calls:
            name = tc.get("name", "")
            params = tc.get("parameters", {}) or {}
            try:
                p = json.dumps(params, sort_keys=True, separators=(",", ":"))
            except Exception:
                p = str(params)
            parts.append(f"{name}:{p}")
        return "||".join(sorted(parts))

    @staticmethod
    def _serialize_result(result: Any) -> str:
        """Serialize a tool result, compacting if too large.

        Auth/redirect URLs are extracted and preserved even when the
        result body is truncated.  For dict/list results that exceed
        the limit, strip verbose nested fields before falling back
        to hard truncation so the LLM sees more useful data.
        """
        raw = result

        if isinstance(raw, (dict, list)):
            text = json.dumps(raw, ensure_ascii=False, default=str)
        else:
            text = str(raw)

        if len(text) > TOOL_RESULT_MAX_CHARS and isinstance(raw, (dict, list)):
            compact = _compact_data(raw)
            text = json.dumps(compact, ensure_ascii=False, default=str)

        if len(text) > TOOL_RESULT_MAX_CHARS:
            auth_urls = _AUTH_URL_RE.findall(text)
            text = text[:TOOL_RESULT_MAX_CHARS] + "...(truncated)"
            if auth_urls:
                preserved = "\n".join(
                    f"AUTH_URL: {url}" for url in auth_urls
                )
                text += f"\n{preserved}"

        text = _sanitize_tool_output(text)
        return text


def _filter_search_results(
    result: dict[str, Any],
    max_results: int = 5,
) -> dict[str, Any]:
    """Pre-filter COMPOSIO_SEARCH_TOOLS results to top-N relevant items.

    Prevents the LLM from dumping 50 tools to the user.
    Only connected/relevant tools are kept.
    """
    if not isinstance(result, dict):
        return result

    items = result.get("items") or result.get("tools") or result.get("results")
    if not isinstance(items, list) or len(items) <= max_results:
        return result

    connected_items = [
        item for item in items
        if isinstance(item, dict) and item.get("connected")
    ]
    disconnected_items = [
        item for item in items
        if isinstance(item, dict) and not item.get("connected")
    ]

    filtered = connected_items[:max_results]
    remaining = max_results - len(filtered)
    if remaining > 0:
        filtered.extend(disconnected_items[:remaining])

    key = "items" if "items" in result else ("tools" if "tools" in result else "results")
    return {**result, key: filtered, "_filtered_from": len(items)}


def _normalize_service_name(name: str) -> str:
    """Normalize service name for comparison."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _is_genuine_service_match(query: str, result_name: str) -> bool:
    """Check if a search result genuinely matches the queried service.

    Prevents Composio's fuzzy search from confusing different services:
    - "Clerk" ≠ "MoonClerk" (auth platform vs payment processor)
    - "Clerk" ≠ "Metabase" (completely unrelated)
    - "GitHub" = "GitHub" (exact match)
    - "google_calendar" ≈ "Google Calendar" (formatting variant)
    """
    q = _normalize_service_name(query)
    r = _normalize_service_name(result_name)

    if not q or not r:
        return True

    if q == r:
        return True

    # query is a formatting variant (google_calendar ≈ googlecalendar)
    if q.replace("_", "") == r.replace("_", ""):
        return True

    # Result name starts with query (e.g., "github" → "githubactions")
    if r.startswith(q) and len(r) - len(q) <= 8:
        return True

    # Query starts with result (e.g., "googledrive" → "google")
    if q.startswith(r) and len(q) - len(r) <= 8:
        return True

    # Reject: query is a SUBSTRING of a longer, different name
    # e.g., "clerk" in "moonclerk" — different service
    if q in r and r != q:
        prefix = r[:r.index(q)]
        if prefix:
            return False

    return len(set(q) & set(r)) / max(len(set(q)), 1) > 0.7


def _validate_search_relevance(
    result: dict[str, Any],
    search_query: str,
    user_message: str,
) -> dict[str, Any]:
    """Validate search results against what the user actually asked for.

    Injects relevance warnings into results so the LLM doesn't blindly
    act on fuzzy matches from Composio's search.
    """
    if not isinstance(result, dict) or not search_query:
        return result

    items_key = next(
        (k for k in ("items", "tools", "results") if k in result), None,
    )
    if not items_key:
        return result

    items = result.get(items_key, [])
    if not isinstance(items, list):
        return result

    has_exact = False
    mismatched: list[str] = []

    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("app") or item.get("appName") or ""
        if _is_genuine_service_match(search_query, name):
            has_exact = True
        else:
            mismatched.append(name)

    if mismatched and not has_exact:
        mismatch_str = ", ".join(f"'{m}'" for m in mismatched[:5])
        result["_relevance_warning"] = (
            f"IMPORTANT: You searched for '{search_query}' but the results "
            f"returned are for different services: {mismatch_str}. "
            f"These are NOT the same as '{search_query}'. Do NOT suggest "
            f"connecting to these services. Instead, acknowledge that "
            f"'{search_query}' is not available as a native integration "
            f"and offer to build a custom connection."
        )
        logger.warning(
            "search_relevance_mismatch",
            query=search_query,
            mismatched=mismatched,
        )
    elif mismatched:
        bad_names = ", ".join(f"'{m}'" for m in mismatched[:5])
        result["_relevance_note"] = (
            f"Note: Some results ({bad_names}) may not match the user's "
            f"request for '{search_query}'. Only use results that exactly "
            f"match the requested service."
        )

    return result


def _validate_connection_relevance(
    result: dict[str, Any],
    toolkits_requested: list[str],
    user_message: str,
) -> dict[str, Any]:
    """Validate connection results — catch when Composio returns wrong services.

    When the user asks to connect "Clerk" but Composio resolves it to
    "MoonClerk", this injects a correction so the LLM doesn't present
    the wrong service to the user.
    """
    if not isinstance(result, dict) or not toolkits_requested:
        return result

    user_lower = user_message.lower()
    connections = result.get("connections") or result.get("results") or []
    if not isinstance(connections, list):
        return result

    corrections: list[str] = []
    for req in toolkits_requested:
        req_norm = _normalize_service_name(req)
        for conn in connections:
            if not isinstance(conn, dict):
                continue
            resolved_name = (
                conn.get("app") or conn.get("name")
                or conn.get("appName") or ""
            )
            if not _is_genuine_service_match(req, resolved_name):
                corrections.append(
                    f"'{req}' was matched to '{resolved_name}' which is a "
                    f"DIFFERENT service. Do NOT present this to the user as "
                    f"'{req}'."
                )

    if corrections:
        result["_connection_corrections"] = corrections
        result["_correction_instruction"] = (
            "WARNING: Some service name matches are INCORRECT. "
            + " ".join(corrections)
            + " If the correct service is not available, acknowledge "
            "honestly and offer to build a custom integration."
        )

    return result


async def _trim_tool_results(
    messages: list[dict[str, Any]],
    max_result_chars: int = 2000,
) -> list[dict[str, Any]]:
    """Trim old tool results to reduce payload size.

    Uses the fast tier model via OpenClaw to summarize older tool outputs
    if they are large, keeping the narrative intact without exploding the
    context window.
    """
    trimmed: list[dict[str, Any]] = []
    total_tool_results = sum(1 for m in messages if m.get("role") == "tool")
    keep_last_n = min(2, total_tool_results)
    trim_threshold = total_tool_results - keep_last_n
    tool_idx = 0

    from lucy.config import settings

    for msg in messages:
        if msg.get("role") == "tool":
            if tool_idx < trim_threshold:
                content = msg.get("content", "")
                if len(content) > max_result_chars:
                    try:
                        prompt = (
                            f"Summarize this tool output concisely, preserving "
                            f"key errors, file paths, and success/fail signals. "
                            f"Keep it under {max_result_chars} characters."
                            f"\n\n{content[:10000]}"
                        )
                        client = await get_openclaw_client()
                        result = await asyncio.wait_for(
                            client.chat_completion(
                                messages=[{"role": "user", "content": prompt}],
                                config=ChatConfig(
                                    model=settings.model_tier_fast,
                                    system_prompt="You are a concise summarizer.",
                                    max_tokens=500,
                                ),
                            ),
                            timeout=10.0,
                        )
                        summary = result.content or ""
                        msg = {**msg, "content": f"[LLM SUMMARIZED]: {summary}"}
                    except Exception as e:
                        logger.warning("llm_condensation_failed", error=str(e))
                        msg = {**msg, "content": content[:max_result_chars] + "...(summarized)"}
            tool_idx += 1
            trimmed.append(msg)
        else:
            trimmed.append(msg)

    return trimmed


_KNOWN_SERVICE_PAIRS: dict[str, list[str]] = {
    "clerk": ["moonclerk", "metabase"],
    "linear": ["linearb"],
    "notion": ["notionhq"],
    "stripe": ["stripe atlas"],
}


def _assess_response_quality(
    user_message: str,
    response_text: str | None,
) -> dict[str, Any]:
    """Heuristic confidence scoring for the agent's response.

    Checks for common error patterns WITHOUT an LLM call (zero cost).
    Returns a quality assessment dict with:
        - confidence: 1-10 score
        - should_escalate: whether to re-run with frontier model
        - reason: why escalation was triggered (if any)
        - issues: list of detected issues
    """
    issues: list[str] = []
    confidence = 10
    response_text = response_text or ""
    resp_lower = response_text.lower()
    user_lower = user_message.lower()

    # 1. Service name confusion detection
    for correct, wrong_matches in _KNOWN_SERVICE_PAIRS.items():
        if correct in user_lower:
            for wrong in wrong_matches:
                if wrong in resp_lower and correct not in resp_lower.replace(wrong, ""):
                    issues.append(
                        f"Service confusion: user asked about '{correct}' "
                        f"but response mentions '{wrong}'"
                    )
                    confidence -= 4

    # 2. Suggesting services the user didn't ask about
    service_suggestions = re.findall(
        r"(?:connect|link|authorize|integration for)\s+(?:\*\*?)?(\w[\w\s]{2,20}?)(?:\*\*?)?",
        resp_lower,
    )
    for suggested in service_suggestions:
        suggested_clean = suggested.strip()
        if (
            len(suggested_clean) > 2
            and suggested_clean not in user_lower
            and not any(
                _is_genuine_service_match(w, suggested_clean)
                for w in user_lower.split()
                if len(w) > 3
            )
        ):
            issues.append(
                f"Suggesting unrequested service: '{suggested_clean}'"
            )
            confidence -= 2

    # 3. "I can't find" when user expects action
    cant_patterns = [
        "i don't have", "i can't", "i couldn't", "i wasn't able",
        "no direct", "no native", "not available",
    ]
    if any(p in resp_lower for p in cant_patterns):
        action_words = ["check", "get", "show", "list", "pull", "create", "report"]
        if any(w in user_lower for w in action_words):
            issues.append(
                "Response says 'can't' but user expected action"
            )
            confidence -= 1

    # 4. Response is very short for a complex question
    if len(user_message) > 60 and len(response_text) < 100:
        issues.append("Suspiciously short response for complex question")
        confidence -= 4  # Aggressive penalty for clearly broken responses

    confidence = max(1, min(10, confidence))
    should_escalate = confidence <= 6 and len(issues) > 0

    if issues:
        logger.info(
            "quality_gate_assessment",
            confidence=confidence,
            issues=issues,
            should_escalate=should_escalate,
        )

    return {
        "confidence": confidence,
        "should_escalate": should_escalate,
        "reason": "; ".join(issues) if issues else "",
        "issues": issues,
    }


def _detect_stuck_state(
    messages: list[dict[str, Any]],
    current_turn: int,
) -> dict[str, Any]:
    """Analyze recent tool results to detect if the agent is stuck.

    Looks for patterns like:
    - Same error appearing in consecutive tool results
    - Repeated calls to the same tool with same/similar args
    - Tools returning errors in multiple consecutive turns
    """
    result: dict[str, Any] = {
        "is_stuck": False,
        "reason": "",
        "intervention": "",
        "escalate_model": False,
    }

    if current_turn < 3:
        return result

    recent_tool_results: list[str] = []
    recent_tool_names: list[str] = []

    for msg in messages[-12:]:
        if msg.get("role") == "tool":
            content = msg.get("content", "")
            recent_tool_results.append(content)
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls", []):
                fn = tc.get("function", {})
                recent_tool_names.append(fn.get("name", ""))

    def _has_real_error(content: str) -> bool:
        """Check if tool result contains a genuine error, not just an empty error field."""
        try:
            data = json.loads(content) if content.strip().startswith("{") else {}
        except (json.JSONDecodeError, ValueError):
            data = {}
        if isinstance(data, dict):
            err = data.get("error", "")
            if err and str(err).strip():
                return True
            nested = data.get("data", {})
            if isinstance(nested, dict):
                nerr = nested.get("error", "")
                if nerr and str(nerr).strip():
                    return True
        if "traceback" in content.lower() or "exception" in content.lower():
            return True
        return False

    error_count = sum(
        1 for r in recent_tool_results[-4:]
        if _has_real_error(r)
    )
    if error_count >= 3:
        result["is_stuck"] = True
        result["reason"] = f"{error_count} consecutive tool errors detected"
        result["intervention"] = (
            "ATTENTION: Multiple consecutive tool calls have returned errors. "
            "STOP repeating the same approach. Take a step back and try a "
            "completely different strategy. If an API call keeps failing, "
            "use lucy_web_search to look up the correct API usage. If a "
            "script keeps erroring, read the error message carefully and "
            "fix the root cause before retrying."
        )
        result["escalate_model"] = True
        return result

    if len(recent_tool_names) >= 4:
        last_four = recent_tool_names[-4:]
        _STUCK_EXEMPT = {
            "COMPOSIO_REMOTE_WORKBENCH", "COMPOSIO_REMOTE_BASH_TOOL",
        }
        if last_four[0] not in _STUCK_EXEMPT and not (
            last_four[0].startswith("lucy_custom_") and "_list_" in last_four[0]
        ) and len(set(last_four)) == 1:
            result["is_stuck"] = True
            result["reason"] = f"Same tool ({last_four[0]}) called 4x in a row"
            result["intervention"] = (
                f"ATTENTION: You have called {last_four[0]} four times in a "
                f"row. This looks like a loop. If it keeps failing, try a "
                f"different approach entirely. Consider using "
                f"COMPOSIO_REMOTE_WORKBENCH to write a script instead of "
                f"repeated tool calls."
            )
            result["escalate_model"] = True
            return result

    return result


def _verify_output(
    user_message: str,
    response_text: str,
    intent: str,
) -> dict[str, Any]:
    """Heuristic verification that the response addresses the request.

    Zero-cost check (no LLM call) that catches common completeness failures.
    Returns a dict with:
        - passed: whether all checks passed
        - issues: list of specific failures detected
        - should_retry: whether a retry with failure context is warranted
    """
    issues: list[str] = []
    user_lower = user_message.lower()
    resp_lower = response_text.lower()

    all_data_signals = [
        "all users", "all customers", "all data", "all records",
        "every user", "every customer", "complete list", "complete report",
        "full report", "full list", "raw data", "entire", "user base",
    ]
    wants_all = any(s in user_lower for s in all_data_signals)

    if wants_all:
        sample_signals = [
            "showing first 20", "sample of", "here are 20",
            "first 20 users", "showing a sample", "top 20",
        ]
        if any(s in resp_lower for s in sample_signals):
            issues.append(
                "User asked for ALL data but response only contains a sample. "
                "Use COMPOSIO_REMOTE_WORKBENCH to write a script that "
                "paginates through the API and fetches every record."
            )

    multi_part_markers = {
        "excel": [r"\bexcel\b", r"\bspreadsheet\b", r"\bworkbook\b"],
        "google_drive": [r"\bgoogle\s+drive\b", r"upload\b.{0,10}\bto\s+drive\b"],
        "email_send": [r"send\b.*\bemail\b", r"\bemail\b.{0,15}\breport\b.*\bto\b.*@"],
        "summary": [r"\bpost\b.{0,10}\bsummary\b", r"\bgive\b.{0,10}\bsummary\b"],
    }
    requested_parts: list[str] = []
    for part_name, keywords in multi_part_markers.items():
        if any(re.search(kw, user_lower) for kw in keywords):
            requested_parts.append(part_name)

    if len(requested_parts) >= 2:
        delivered_signals = {
            "excel": ["excel", "spreadsheet", ".xlsx", "openpyxl", "file.*upload"],
            "google_drive": ["drive", "uploaded", "shared", "link"],
            "email_send": [
                "email sent", "emailed", "sent.*email",
                "email.*to.*@", "✅.*email", ":white_check_mark:.*email",
            ],
            "summary": [
                "summary", "total", "breakdown", "here's", "overview",
                "results", "report", "findings",
            ],
        }
        for part in requested_parts:
            signals = delivered_signals.get(part, [])
            if signals and not any(
                re.search(s, resp_lower) for s in signals
            ):
                issues.append(
                    f"User requested '{part}' but it appears missing "
                    f"from the response."
                )

    if intent == "data" and len(response_text) < 100:
        issues.append(
            "Data task produced a very short response. "
            "Expected detailed output with counts and deliverables."
        )

    degradation_phrases = [
        "ran into a hiccup",
        "ran into an issue",
        "let me try a different approach",
        "couldn't complete",
        "unable to process",
        "something went wrong",
    ]
    if any(p in resp_lower for p in degradation_phrases):
        issues.append(
            "Response is a degradation/error fallback, not an actual answer. "
            "Retry with a fresh approach using the available tools."
        )

    if len(user_lower) > 150 and len(response_text) < 150:
        issues.append(
            "Complex multi-step request received a very short response. "
            "Break the task into steps and execute each one."
        )

    # High-agency check: detect surrender/dead-end patterns
    low_agency_patterns = [
        "i can't access",
        "i'm unable to",
        "unfortunately i cannot",
        "i don't have access to",
        "i'm not able to",
        "i cannot access",
        "i am unable to",
        "that's outside my",
        "falls outside my",
        "i don't have the ability",
        "i'm afraid i can't",
        "this took longer than expected and i had to stop",
        "the request may have been too complex",
        "want me to try a simpler approach",
        "i wasn't able to complete",
        "i'm having trouble with",
        "something went wrong",
        "i hit a snag",
    ]
    action_words = [
        "get", "show", "pull", "fetch", "create", "build", "generate",
        "send", "connect", "set up", "check", "find", "run", "analyze",
        "monitor", "track", "report", "scan", "deploy", "start", "write",
    ]
    has_action_intent = any(w in user_lower for w in action_words)
    has_low_agency = any(p in resp_lower for p in low_agency_patterns)
    if has_low_agency and has_action_intent:
        issues.append(
            "CRITICAL: Response contains a dead-end or surrender pattern. You are a "
            "high-agency problem solver. Every problem is solvable. Rewrite to: "
            "(1) Offer the closest thing you CAN do right now, "
            "(2) Explain specifically what you'd need to do the full thing, "
            "(3) Provide at least one alternative path forward. "
            "Never leave the user with a dead end. Never suggest they 'try a simpler "
            "approach' — YOU find the simpler approach and execute it. "
            "Example: instead of 'I can't access Figma directly' say "
            "'I can't pull from Figma directly, but two options: drop the file here "
            "and I'll extract the content, or I can build a custom connection. "
            "Which works better?'"
        )
    elif has_low_agency:
        issues.append(
            "Response contains a 'can't do' pattern without offering an alternative. "
            "Even if the user didn't explicitly ask for action, always follow 'I can't X' "
            "with 'but I can Y' or 'here's what would make it possible'. No dead ends."
        )

    return {
        "passed": len(issues) == 0,
        "issues": issues,
        "should_retry": len(issues) > 0,
    }


# ── Singleton ───────────────────────────────────────────────────────────

_agent: LucyAgent | None = None


def get_agent() -> LucyAgent:
    """Get or create the singleton agent."""
    global _agent
    if _agent is None:
        _agent = LucyAgent()
    return _agent
