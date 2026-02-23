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

        tool_names = {
            t.get("function", {}).get("name", "")
            for t in (tools or [])
            if isinstance(t, dict)
        }

        current_model = model

        for turn in range(MAX_TOOL_TURNS):
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

            if (
                turn >= 1
                and not progress_sent
                and slack_client
                and ctx.channel_id
                and ctx.thread_ts
            ):
                progress_sent = True
                completed_tools = list(trace.tool_calls_made)
                if completed_tools:
                    progress = _describe_progress(completed_tools)
                    try:
                        await slack_client.chat_postMessage(
                            channel=ctx.channel_id,
                            thread_ts=ctx.thread_ts,
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
                tool_calls, tool_names, ctx, trace,
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
        """Execute a single tool call via Composio."""
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


def _describe_progress(tool_calls: list[str]) -> str:
    """Generate a human-readable progress message from completed tool names."""
    tool_map = {
        "COMPOSIO_SEARCH_TOOLS": "searched for available tools",
        "COMPOSIO_MANAGE_CONNECTIONS": "checked integrations",
        "COMPOSIO_MULTI_EXECUTE_TOOL": "ran some actions",
        "COMPOSIO_GET_TOOL_SCHEMAS": "looked up tool details",
        "COMPOSIO_REMOTE_WORKBENCH": "ran some code",
        "COMPOSIO_REMOTE_BASH_TOOL": "ran a script",
    }
    steps = []
    seen = set()
    for tc in tool_calls:
        desc = tool_map.get(tc, tc.lower().replace("_", " "))
        if desc not in seen:
            steps.append(desc)
            seen.add(desc)
    if not steps:
        return "Still working on this..."
    return f"Working on it — so far I've {', '.join(steps[:3])}. Almost there..."


# ── Singleton ───────────────────────────────────────────────────────────

_agent: LucyAgent | None = None


def get_agent() -> LucyAgent:
    """Get or create the singleton agent."""
    global _agent
    if _agent is None:
        _agent = LucyAgent()
    return _agent
