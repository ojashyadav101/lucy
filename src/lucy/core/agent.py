"""LucyAgent — core orchestrator.

Flow:
1. Ensure workspace exists (onboard if first message)
2. Read relevant skills → build system prompt
3. Get Composio meta-tools (5 tools)
4. Build conversation from Slack thread history
5. Multi-turn LLM loop: call OpenClaw → execute tool calls → repeat
6. Send response to Slack
7. Log activity
"""

from __future__ import annotations

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
    OpenClawResponse,
    get_openclaw_client,
)

logger = structlog.get_logger()

MAX_TOOL_TURNS = 8
MAX_CONTEXT_MESSAGES = 40
TOOL_RESULT_MAX_CHARS = 12_000


@dataclass
class AgentContext:
    """Lightweight context for an agent run."""

    workspace_id: str
    channel_id: str | None = None
    thread_ts: str | None = None
    user_name: str | None = None


class LucyAgent:
    """Lean agent: read skills → prompt → LLM + meta-tools → respond."""

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
    ) -> str:
        """Run the full agent loop and return the final response text.

        This is the single entry point. Handlers call this directly.
        """
        t0 = time.monotonic()

        # 1. Ensure workspace (onboard if new)
        from lucy.workspace.onboarding import ensure_workspace

        ws = await ensure_workspace(ctx.workspace_id, slack_client)

        # 2. Build system prompt (SOUL + skills + instructions)
        from lucy.core.prompt import build_system_prompt

        system_prompt = await build_system_prompt(ws)

        # Inject current UTC time for timezone-aware operations
        utc_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        system_prompt += (
            f"\n\n## Current Time\nUTC: {utc_now}\n"
            f"Use team/SKILL.md timezone data to compute each user's local time."
        )

        # 3. Get Composio meta-tools
        tools = await self._get_meta_tools(ctx.workspace_id)

        # 4. Build conversation messages from Slack thread
        messages = await self._build_thread_messages(
            ctx, message, slack_client
        )

        # 5. Multi-turn LLM loop
        response_text = await self._agent_loop(
            system_prompt=system_prompt,
            messages=messages,
            tools=tools,
            ctx=ctx,
        )

        # 6. Log activity
        from lucy.workspace.activity_log import log_activity

        elapsed_ms = round((time.monotonic() - t0) * 1000)
        preview = message[:80].replace("\n", " ")
        await log_activity(
            ws,
            f"Responded to \"{preview}\" in {elapsed_ms}ms",
        )

        logger.info(
            "agent_run_complete",
            workspace_id=ctx.workspace_id,
            elapsed_ms=elapsed_ms,
            response_length=len(response_text),
        )
        return response_text

    # ── Agent loop ──────────────────────────────────────────────────────

    async def _agent_loop(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        ctx: AgentContext,
    ) -> str:
        """Multi-turn LLM ↔ tool execution loop."""
        client = await self._get_client()
        all_messages = list(messages)
        response_text = ""
        repeated_sigs: dict[str, int] = {}

        tool_names = {
            t.get("function", {}).get("name", "")
            for t in (tools or [])
            if isinstance(t, dict)
        }

        for turn in range(MAX_TOOL_TURNS):
            config = ChatConfig(
                model=settings.openclaw_model,
                system_prompt=system_prompt,
                tools=tools,
            )

            response = await client.chat_completion(
                messages=all_messages,
                config=config,
            )

            response_text = response.content or ""
            tool_calls = response.tool_calls

            # No tool calls → we're done
            if not tool_calls:
                # Catch false "no access" on first turn when tools exist
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
                    names = [n for n in list(tool_names)[:10] if n]
                    all_messages.append(
                        {"role": "assistant", "content": response_text}
                    )
                    all_messages.append({
                        "role": "user",
                        "content": (
                            f"You DO have access to these tools: "
                            f"{', '.join(names)}. "
                            f"Please use them instead of saying you "
                            f"don't have access."
                        ),
                    })
                    continue
                break

            # Loop detection
            sig = self._call_signature(tool_calls)
            repeated_sigs[sig] = repeated_sigs.get(sig, 0) + 1
            if repeated_sigs[sig] >= 3:
                logger.warning("tool_loop_detected", turn=turn)
                response_text = (
                    "I'm running into a loop with tool calls. "
                    "Could you rephrase your request?"
                )
                break

            logger.info(
                "tool_turn",
                turn=turn + 1,
                calls=[tc.get("name") for tc in tool_calls],
                workspace_id=ctx.workspace_id,
            )

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

            # Execute each tool call
            for i, tc in enumerate(tool_calls):
                name = tc.get("name", "")
                params = tc.get("parameters", {})
                call_id = tc.get("id", f"call_{i}")
                parse_error = tc.get("parse_error")

                if parse_error:
                    result_str = json.dumps({
                        "error": (
                            f"Failed to parse arguments for '{name}': "
                            f"{parse_error}. Please retry with valid JSON "
                            f"arguments."
                        ),
                    })
                elif name not in tool_names:
                    result_str = json.dumps({
                        "error": f"Tool '{name}' is not available."
                    })
                else:
                    result = await self._execute_tool(
                        name, params, ctx.workspace_id
                    )
                    result_str = self._serialize_result(result)

                all_messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": result_str,
                })

            # Trim context window
            if len(all_messages) > MAX_CONTEXT_MESSAGES:
                all_messages = all_messages[-MAX_CONTEXT_MESSAGES:]

        if not response_text.strip():
            response_text = (
                "I wasn't able to complete the request after several "
                "tool calls. Could you try rephrasing?"
            )

        return response_text

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
        return text


# ── Singleton ───────────────────────────────────────────────────────────

_agent: LucyAgent | None = None


def get_agent() -> LucyAgent:
    """Get or create the singleton agent."""
    global _agent
    if _agent is None:
        _agent = LucyAgent()
    return _agent
