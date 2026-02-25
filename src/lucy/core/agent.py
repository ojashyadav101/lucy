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
from lucy.core.trace import Trace

logger = structlog.get_logger()

MAX_TOOL_TURNS = 12
MAX_TOOL_TURNS_FRONTIER = 20  # Deep research gets more room
MAX_CONTEXT_MESSAGES = 40
TOOL_RESULT_MAX_CHARS = 16_000
TOOL_RESULT_SUMMARY_THRESHOLD = 8_000
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
    ) -> str:
        """Run the full agent loop and return the final response text."""
        self._current_slack_client = slack_client
        self._current_channel_id = ctx.channel_id
        self._current_thread_ts = ctx.thread_ts
        self._current_user_slack_id = ctx.user_slack_id

        trace = Trace.start()
        trace.user_message = message

        # 1. Classify intent and select model
        from lucy.core.router import classify_and_route

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
            except Exception:
                pass

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
        from lucy.core.prompt import build_system_prompt

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
            except Exception:
                pass

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
                except Exception:
                    pass

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

        # 6. Multi-turn LLM loop
        response_text = await self._agent_loop(
            system_prompt=system_prompt,
            messages=messages,
            tools=tools,
            ctx=ctx,
            model=model,
            trace=trace,
            route=route,
            slack_client=slack_client,
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
                trace.decision_log = trace.decision_log if hasattr(trace, "decision_log") else []
                corrected = await self._escalate_response(
                    message, response_text, gate["reason"], ctx,
                )
                if corrected:
                    response_text = corrected

        # 6c. Post-response: persist memorable facts to memory
        try:
            from lucy.workspace.memory import (
                add_session_fact,
                append_to_company_knowledge,
                append_to_team_knowledge,
                classify_memory_target,
                should_persist_memory,
            )
            if should_persist_memory(message):
                target = classify_memory_target(message)
                fact = message.strip()
                if len(fact) > 300:
                    fact = fact[:300] + "..."

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
        trace_record = trace.finish(
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
    ) -> str:
        """Multi-turn LLM <-> tool execution loop."""
        client = await self._get_client()
        all_messages = list(messages)
        response_text = ""
        repeated_sigs: dict[str, int] = {}
        tool_name_counts: dict[str, int] = {}
        self._empty_retries = 0
        self._narration_retries = 0
        self._edit_attempts = 0
        progress_sent = False
        progress_ts: str | None = None

        tool_names = {
            t.get("function", {}).get("name", "")
            for t in (tools or [])
            if isinstance(t, dict) and t.get("function", {}).get("name")
        }

        current_model = model

        # Frontier tasks get more turns for deep research
        is_frontier = "frontier" in (model or "")
        max_turns = MAX_TOOL_TURNS_FRONTIER if is_frontier else MAX_TOOL_TURNS

        for turn in range(max_turns):
            config = ChatConfig(
                model=current_model,
                system_prompt=system_prompt,
                tools=tools,
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
                if e.status_code == 400 and turn > 0:
                    logger.warning(
                        "400_recovery_trimming",
                        turn=turn,
                        messages_before=len(all_messages),
                    )
                    all_messages = await _trim_tool_results(all_messages)
                    from lucy.core.router import MODEL_TIERS
                    frontier = MODEL_TIERS.get("frontier", current_model)
                    if frontier != current_model:
                        current_model = frontier
                        logger.info(
                            "400_recovery_model_escalation",
                            to_model=frontier,
                        )
                    continue
                raise

            response_text = response.content or ""
            tool_calls = response.tool_calls

            # Empty response recovery: if the LLM returns nothing
            # after tool results, escalate model then nudge.
            if not tool_calls and not response_text.strip() and turn > 0:
                empty_retries = getattr(self, "_empty_retries", 0)
                if empty_retries < 2:
                    self._empty_retries = empty_retries + 1

                    if empty_retries == 1:
                        from lucy.core.router import MODEL_TIERS
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
                    all_messages.append(
                        {"role": "assistant", "content": ""}
                    )
                    all_messages.append({
                        "role": "user",
                        "content": (
                            "You found the right tools. Now use them to "
                            "get the data I asked for and give me the answer."
                        ),
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
                if (
                    turn <= 3
                    and tools
                    and narration_retries < 1
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
                tn = tc.get("name", "")
                tool_name_counts[tn] = tool_name_counts.get(tn, 0) + 1
            over_cap = [
                n for n, c in tool_name_counts.items()
                if c >= 4 and n not in _CAP_EXEMPT
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
                if cap_violations >= 3:
                    logger.warning(
                        "tool_cap_force_stop",
                        turn=turn,
                        tools=over_cap,
                    )
                    all_messages.append({
                        "role": "system",
                        "content": (
                            "CRITICAL: You have repeatedly ignored "
                            "instructions to stop calling the same tools. "
                            "You MUST respond to the user NOW with "
                            "whatever data you have. Do NOT call any more "
                            "tools. Summarize your findings."
                        ),
                    })
                    tools = None
                    continue
                all_messages.append({
                    "role": "system",
                    "content": (
                        f"You have called {', '.join(over_cap)} too many "
                        f"times. STOP calling it. Summarize the data you "
                        f"already collected and respond to the user now. "
                        f"If you don't have enough data, tell the user "
                        f"what you found so far and offer next steps."
                    ),
                })
                continue

            logger.info(
                "tool_turn",
                turn=turn + 1,
                calls=[tc.get("name") for tc in tool_calls],
                workspace_id=ctx.workspace_id,
            )

            should_update = (
                slack_client
                and ctx.channel_id
                and ctx.thread_ts
                and (
                    (turn == 2 and not progress_sent)
                    or (turn > 2 and turn % 3 == 0)
                )
            )
            if should_update:
                progress_sent = True
                completed_tools = list(trace.tool_calls_made)
                if completed_tools:
                    progress = _describe_progress(completed_tools, turn)
                    try:
                        if not progress_ts:
                            result = await slack_client.chat_postMessage(
                                channel=ctx.channel_id,
                                thread_ts=ctx.thread_ts,
                                text=progress,
                            )
                            progress_ts = result.get("ts")
                        else:
                            await slack_client.chat_update(
                                channel=ctx.channel_id,
                                ts=progress_ts,
                                text=progress,
                            )
                    except Exception:
                        pass

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
                    result_str = (
                        result_str[:TOOL_RESULT_SUMMARY_THRESHOLD]
                        + "...(trimmed for context efficiency)"
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
                all_messages = await _trim_tool_results(all_messages, max_result_chars=300)
                logger.info(
                    "payload_trimmed",
                    turn=turn,
                    before_chars=payload_size,
                )

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
                from lucy.core.router import MODEL_TIERS
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
                    from lucy.core.router import MODEL_TIERS
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

            # Trim context window
            if len(all_messages) > MAX_CONTEXT_MESSAGES:
                all_messages = all_messages[-MAX_CONTEXT_MESSAGES:]

        if not response_text.strip():
            partial = self._collect_partial_results(all_messages)
            if partial:
                response_text = partial
            else:
                from lucy.core.humanize import humanize
                response_text = await humanize(
                    "You couldn't find the answer. Ask the user "
                    "to clarify or rephrase what they need. Be "
                    "warm and brief, not robotic.",
                )

        return response_text

    @staticmethod
    def _collect_partial_results(
        messages: list[dict[str, Any]],
    ) -> str:
        """Produce a graceful fallback when the LLM returns an empty response.

        Never expose raw JSON, tool names, or internal data to the user.
        """
        has_tool_results = any(
            msg.get("role") == "tool"
            and msg.get("content", "")
            and '"error"' not in msg.get("content", "")
            for msg in messages
        )
        if has_tool_results:
            tool_data = []
            for msg in messages:
                if msg.get("role") == "tool" and msg.get("content", ""):
                    content = msg["content"]
                    if '"error"' not in content:
                        tool_data.append(content[:500])
            summary = "; ".join(tool_data[:3]) if tool_data else ""
            return (
                f"Here's what I found so far: {summary}\n\n"
                "I'm still piecing together the full picture. "
                "Let me know if this helps or if you need me "
                "to dig deeper."
            ) if summary else ""
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
            from lucy.core.edge_cases import should_deduplicate_tool_call
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
            from lucy.core.rate_limiter import get_rate_limiter
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
                from lucy.slack.hitl import is_destructive_tool_call
                actions = params.get("actions") or []
                for act in actions:
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
            *[_run_one(i, tc) for i, tc in enumerate(tool_calls)]
        )
        return list(results)

    # ── Tool execution ──────────────────────────────────────────────────

    async def _get_meta_tools(
        self, workspace_id: str
    ) -> list[dict[str, Any]]:
        """Fetch 5 Composio meta-tools for this workspace."""
        try:
            from lucy.integrations.composio_client import get_composio_client

            client = get_composio_client()
            return await client.get_tools(workspace_id)
        except Exception as e:
            logger.error("meta_tools_fetch_failed", error=str(e))
            return []

    async def _get_connected_services(
        self, workspace_id: str
    ) -> list[str]:
        """Fetch names of actively connected integrations."""
        try:
            from lucy.integrations.composio_client import get_composio_client

            client = get_composio_client()
            names = await client.get_connected_app_names_reliable(workspace_id)
            if names:
                logger.info(
                    "connected_services_fetched",
                    workspace_id=workspace_id,
                    services=names,
                )
            return names
        except Exception as e:
            logger.warning("connected_services_fetch_failed", error=str(e))
            return []

    async def _execute_tool(
        self,
        tool_name: str,
        parameters: dict[str, Any],
        workspace_id: str,
        ctx: AgentContext | None = None,
    ) -> dict[str, Any]:
        """Execute a single tool call.

        Internal tools (lucy_*) are handled locally.
        Everything else routes through Composio.
        """
        # ── Internal tools (no external API needed) ──────────────────
        if tool_name.startswith("lucy_"):
            return await self._execute_internal_tool(
                tool_name, parameters, workspace_id, ctx=ctx,
            )

        # ── Delegation to sub-agents ─────────────────────────────────
        if tool_name.startswith("delegate_to_") and tool_name.endswith("_agent"):
            return await self._handle_delegation(
                tool_name, parameters, workspace_id,
            )

        # ── External tools (via Composio) ────────────────────────────
        try:
            from lucy.integrations.composio_client import get_composio_client

            client = get_composio_client()
            result = await client.execute_tool_call(
                workspace_id=workspace_id,
                tool_name=tool_name,
                arguments=parameters,
            )

            logger.info(
                "tool_executed",
                tool=tool_name,
                workspace_id=workspace_id,
            )
            return result

        except Exception as e:
            logger.error(
                "tool_execution_failed",
                tool=tool_name,
                error=str(e),
            )
            return {"error": str(e)}

    async def _execute_internal_tool(
        self,
        tool_name: str,
        parameters: dict[str, Any],
        workspace_id: str,
        ctx: AgentContext | None = None,
    ) -> dict[str, Any]:
        """Execute an internal (lucy_*) tool — no Composio, no external API."""
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
                        except Exception:
                            pass
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
            return {"error": str(e)}

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
            except Exception:
                pass

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
            except Exception:
                pass

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

        try:
            result = await asyncio.wait_for(
                run_subagent(
                    task=task,
                    spec=spec,
                    workspace_id=workspace_id,
                    tool_registry=self._tool_registry,
                    progress_callback=self._on_subagent_progress,
                ),
                timeout=SUB_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
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

        return {"result": result}

    async def _on_subagent_progress(self, name: str, turn: int) -> None:
        """Progress callback for sub-agents — updates the Slack message."""
        slack_client = getattr(self, "_current_slack_client", None)
        channel_id = getattr(self, "_current_channel_id", None)
        thread_ts = getattr(self, "_current_thread_ts", None)

        if not slack_client or not channel_id or not thread_ts:
            return

        from lucy.core.humanize import pick
        try:
            desc = pick("progress_mid")
            progress_ts = getattr(self, "_progress_ts", None)
            if progress_ts:
                await slack_client.chat_update(
                    channel=channel_id, ts=progress_ts, text=desc,
                )
            else:
                result = await slack_client.chat_postMessage(
                    channel=channel_id, thread_ts=thread_ts, text=desc,
                )
                self._progress_ts = result.get("ts")
        except Exception:
            pass

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
        from lucy.core.router import MODEL_TIERS

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
                        max_tokens=800,
                    ),
                ),
                timeout=10.0,
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
            logger.warning("quality_gate_escalation_failed", error=str(exc))
            return None

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
    max_result_chars: int = 500,
) -> list[dict[str, Any]]:
    """Trim old tool results to reduce payload size.

    Uses the fast tier model to summarize older tool outputs if they are large,
    keeping the narrative intact without exploding the context window.
    """
    trimmed: list[dict[str, Any]] = []
    total_tool_results = sum(1 for m in messages if m.get("role") == "tool")
    keep_last_n = min(2, total_tool_results)
    trim_threshold = total_tool_results - keep_last_n
    tool_idx = 0

    from lucy.config import settings
    import httpx

    for msg in messages:
        if msg.get("role") == "tool":
            if tool_idx < trim_threshold:
                content = msg.get("content", "")
                if len(content) > max_result_chars:
                    try:
                        prompt = f"Summarize this tool output concisely, preserving key errors, file paths, and success/fail signals. Keep it under {max_result_chars} characters.\n\n{content[:10000]}"
                        async with httpx.AsyncClient(timeout=10.0) as http_client:
                            resp = await http_client.post(
                                "https://openrouter.ai/api/v1/chat/completions",
                                headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
                                json={
                                    "model": settings.model_tier_fast,
                                    "messages": [{"role": "user", "content": prompt}],
                                    "max_tokens": 150
                                }
                            )
                            summary = resp.json()["choices"][0]["message"]["content"]
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
    response_text: str,
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
        confidence -= 1

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


def _describe_progress(tool_calls: list[str], turn: int = 0) -> str:
    """Generate a natural, human-sounding progress message.

    Updates are edited in-place (same message gets updated) so the user
    sees a single evolving status, not a stream of separate messages.
    Responses come from LLM-generated pools (pre-warmed at startup).
    Never expose tool names or internal machinery.
    """
    from lucy.core.humanize import pick

    if turn <= 2:
        return pick("progress_early")
    if turn <= 4:
        return pick("progress_mid")
    if turn <= 7:
        return pick("progress_late")
    return pick("progress_final")


# ── Singleton ───────────────────────────────────────────────────────────

_agent: LucyAgent | None = None


def get_agent() -> LucyAgent:
    """Get or create the singleton agent."""
    global _agent
    if _agent is None:
        _agent = LucyAgent()
    return _agent
