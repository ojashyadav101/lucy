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
TOOL_RESULT_MAX_CHARS = 12_000
TOOL_RESULT_SUMMARY_THRESHOLD = 4_000
MAX_PAYLOAD_CHARS = 120_000

_INTERNAL_PATH_RE = re.compile(r"/home/user/[^\s\"',}\]]+")
_WORKSPACE_PATH_RE = re.compile(r"workspaces?/[^\s\"',}\]]+")


def _sanitize_tool_output(text: str) -> str:
    """Remove internal file paths from tool output."""
    text = _INTERNAL_PATH_RE.sub("[file]", text)
    text = _WORKSPACE_PATH_RE.sub("[workspace]", text)
    return text


@dataclass
class AgentContext:
    """Lightweight context for an agent run."""

    workspace_id: str
    channel_id: str | None = None
    thread_ts: str | None = None
    user_name: str | None = None


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
            tools, connected_services = await asyncio.gather(
                tools_coro, connections_coro,
            )

            # Inject internal tools (Slack history search + file generation)
            from lucy.workspace.history_search import get_history_tool_definitions
            tools.extend(get_history_tool_definitions())

            from lucy.tools.file_generator import get_file_tool_definitions
            tools.extend(get_file_tool_definitions())

        # 4. Build system prompt (SOUL + skills + instructions + environment)
        async with trace.span("build_prompt"):
            system_prompt = await build_system_prompt(
                ws,
                connected_services=connected_services,
                user_message=message,
            )
            utc_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            system_prompt += f"\n\n## Current Time\nUTC: {utc_now}\n"

        # 5. Build conversation messages from Slack thread
        async with trace.span("build_thread_messages"):
            messages = await self._build_thread_messages(
                ctx, message, slack_client
            )

        # 6. Multi-turn LLM loop
        response_text = await self._agent_loop(
            system_prompt=system_prompt,
            messages=messages,
            tools=tools,
            ctx=ctx,
            model=model,
            trace=trace,
            slack_client=slack_client,
        )

        # 6b. Post-response: persist memorable facts to memory
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

        return response_text

    # ── Agent loop ──────────────────────────────────────────────────────

    async def _agent_loop(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        ctx: AgentContext,
        model: str,
        trace: Trace,
        slack_client: Any | None = None,
    ) -> str:
        """Multi-turn LLM <-> tool execution loop."""
        client = await self._get_client()
        all_messages = list(messages)
        response_text = ""
        repeated_sigs: dict[str, int] = {}
        progress_sent = False
        progress_ts: str | None = None

        tool_names = {
            t.get("function", {}).get("name", "")
            for t in (tools or [])
            if isinstance(t, dict)
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
                    all_messages = _trim_tool_results(all_messages)
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
                break

            # Loop detection — redirect LLM instead of hardcoded string
            sig = self._call_signature(tool_calls)
            repeated_sigs[sig] = repeated_sigs.get(sig, 0) + 1
            if repeated_sigs[sig] >= 3:
                logger.warning("tool_loop_detected", turn=turn)
                all_messages.append({
                    "role": "system",
                    "content": (
                        "Your previous approach is not working — you have "
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
                    (turn == 1 and not progress_sent)
                    or (turn > 1 and turn % 3 == 0)
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
                all_messages = _trim_tool_results(all_messages, max_result_chars=300)
                logger.info(
                    "payload_trimmed",
                    turn=turn,
                    before_chars=payload_size,
                )

            # Mid-loop model upgrade: if tools suggest coding, switch
            called_names = {tc.get("name", "") for tc in tool_calls}
            if called_names & {"COMPOSIO_REMOTE_WORKBENCH", "COMPOSIO_REMOTE_BASH_TOOL"}:
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

            # Trim context window
            if len(all_messages) > MAX_CONTEXT_MESSAGES:
                all_messages = all_messages[-MAX_CONTEXT_MESSAGES:]

        if not response_text.strip():
            partial = self._collect_partial_results(all_messages)
            if partial:
                response_text = partial
            else:
                response_text = (
                    "Let me take a different angle on this — "
                    "could you tell me a bit more about what "
                    "you're looking for so I can narrow it down?"
                )

        return response_text

    @staticmethod
    def _collect_partial_results(
        messages: list[dict[str, Any]],
    ) -> str:
        """Extract meaningful partial results from tool messages."""
        partials: list[str] = []
        for msg in messages:
            if msg.get("role") != "tool":
                continue
            content = msg.get("content", "")
            if not content or '"error"' in content:
                continue
            if len(content) > 200:
                partials.append(content[:200])
            else:
                partials.append(content)
        if not partials:
            return ""
        return (
            "Here's what I've gathered so far — I'm still "
            "working on the rest and will follow up:\n\n"
            + "\n".join(partials[:3])
        )

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
                        f"Duplicate call to '{name}' blocked — "
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
                    name, params, ctx.workspace_id,
                )

            if name == "COMPOSIO_SEARCH_TOOLS":
                result = _filter_search_results(result)

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
            names = await client.get_connected_app_names(workspace_id)
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
    ) -> dict[str, Any]:
        """Execute a single tool call.

        Internal tools (lucy_*) are handled locally.
        Everything else routes through Composio.
        """
        # ── Internal tools (no external API needed) ──────────────────
        if tool_name.startswith("lucy_"):
            return await self._execute_internal_tool(
                tool_name, parameters, workspace_id
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
    ) -> dict[str, Any]:
        """Execute an internal (lucy_*) tool — no Composio, no external API."""
        from lucy.workspace.filesystem import get_workspace

        ws = get_workspace(workspace_id)

        try:
            if tool_name.startswith("lucy_search_slack_history") or \
               tool_name.startswith("lucy_get_channel_history"):
                from lucy.workspace.history_search import execute_history_tool
                result_text = await execute_history_tool(ws, tool_name, parameters)
                return {"result": result_text}

            if tool_name.startswith("lucy_generate_"):
                from lucy.tools.file_generator import execute_file_tool
                return await execute_file_tool(
                    tool_name=tool_name,
                    parameters=parameters,
                    slack_client=self._current_slack_client,
                    channel_id=self._current_channel_id,
                    thread_ts=self._current_thread_ts,
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
        """Serialize a tool result, compacting if too large."""
        if isinstance(result, (dict, list)):
            text = json.dumps(result, ensure_ascii=False, default=str)
        else:
            text = str(result)

        if len(text) > TOOL_RESULT_MAX_CHARS:
            text = text[:TOOL_RESULT_MAX_CHARS] + "...(truncated)"

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


def _trim_tool_results(
    messages: list[dict[str, Any]],
    max_result_chars: int = 500,
) -> list[dict[str, Any]]:
    """Trim old tool results to reduce payload size for 400 recovery.

    Keeps the last 2 tool results intact; older ones get truncated.
    """
    trimmed: list[dict[str, Any]] = []
    total_tool_results = sum(1 for m in messages if m.get("role") == "tool")
    keep_last_n = min(2, total_tool_results)
    trim_threshold = total_tool_results - keep_last_n
    tool_idx = 0

    for msg in messages:
        if msg.get("role") == "tool":
            if tool_idx < trim_threshold:
                content = msg.get("content", "")
                if len(content) > max_result_chars:
                    msg = {**msg, "content": content[:max_result_chars] + "...(summarized)"}
            tool_idx += 1
            trimmed.append(msg)
        else:
            trimmed.append(msg)

    return trimmed


def _describe_progress(tool_calls: list[str], turn: int = 0) -> str:
    """Generate a human-readable progress message from completed tool names.

    Updates are edited in-place (same message gets updated) so the user
    sees a single evolving status, not a stream of separate messages.
    """
    tool_map = {
        "COMPOSIO_SEARCH_TOOLS": "found the right tools",
        "COMPOSIO_MANAGE_CONNECTIONS": "checked your integrations",
        "COMPOSIO_MULTI_EXECUTE_TOOL": "executed some actions",
        "COMPOSIO_GET_TOOL_SCHEMAS": "gathered tool details",
        "COMPOSIO_REMOTE_WORKBENCH": "ran some code",
        "COMPOSIO_REMOTE_BASH_TOOL": "ran a script",
    }
    steps = []
    seen: set[str] = set()
    for tc in tool_calls:
        desc = tool_map.get(tc, tc.lower().replace("_", " "))
        if desc not in seen:
            steps.append(desc)
            seen.add(desc)
    if not steps:
        return "Still working on this..."

    prefix = "Working on it"
    if turn >= 6:
        prefix = "Still on it — this is a deep one"
    elif turn >= 3:
        prefix = "Making progress"

    return f"{prefix} — so far I've {', '.join(steps[:4])}. Almost there..."


# ── Singleton ───────────────────────────────────────────────────────────

_agent: LucyAgent | None = None


def get_agent() -> LucyAgent:
    """Get or create the singleton agent."""
    global _agent
    if _agent is None:
        _agent = LucyAgent()
    return _agent
