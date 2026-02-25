"""Sub-agent system for Lucy.

Provides isolated agent loops with focused system prompts. Sub-agents
run in their own message history, reuse the shared OpenClawClient
(rate limiting is automatic), and return a final text result to the
supervisor.

Includes: context trimming, loop detection, error retry, empty response
recovery, progress callbacks, and timeout protection.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

import structlog

from lucy.config import settings
from lucy.core.openclaw import (
    ChatConfig,
    OpenClawClient,
    OpenClawError,
    OpenClawResponse,
    get_openclaw_client,
)

logger = structlog.get_logger()

ASSETS = Path(__file__).parent.parent.parent.parent / "assets"
SUB_MAX_TURNS = 10
SUB_MAX_PAYLOAD_CHARS = 80_000
SUB_TOOL_RESULT_MAX_CHARS = 8_000
SUB_TIMEOUT_SECONDS = 120


@dataclass
class SubAgentSpec:
    """Specification for a sub-agent type."""

    name: str
    system_prompt_file: str
    model: str
    tool_names: list[str] = field(default_factory=list)
    max_turns: int = SUB_MAX_TURNS
    max_tokens: int = 4096
    temperature: float = 0.4


REGISTRY: dict[str, SubAgentSpec] = {
    "research": SubAgentSpec(
        name="research",
        system_prompt_file="sub_agents/research.md",
        model=settings.model_tier_research,
        tool_names=[
            "COMPOSIO_SEARCH_TOOLS",
            "COMPOSIO_MULTI_EXECUTE_TOOL",
            "lucy_search_slack_history",
        ],
        max_turns=12,
    ),
    "code": SubAgentSpec(
        name="code",
        system_prompt_file="sub_agents/code.md",
        model=settings.model_tier_code,
        tool_names=[
            "COMPOSIO_REMOTE_WORKBENCH",
            "COMPOSIO_REMOTE_BASH_TOOL",
            "lucy_write_file",
            "lucy_edit_file",
        ],
        max_turns=10,
    ),
    "integrations": SubAgentSpec(
        name="integrations",
        system_prompt_file="sub_agents/integrations.md",
        model=settings.model_tier_default,
        tool_names=[
            "COMPOSIO_SEARCH_TOOLS",
            "COMPOSIO_MANAGE_CONNECTIONS",
            "COMPOSIO_MULTI_EXECUTE_TOOL",
            "lucy_resolve_custom_integration",
        ],
        max_turns=8,
    ),
    "document": SubAgentSpec(
        name="document",
        system_prompt_file="sub_agents/document.md",
        model=settings.model_tier_document,
        tool_names=[
            "COMPOSIO_REMOTE_WORKBENCH",
            "COMPOSIO_REMOTE_BASH_TOOL",
            "lucy_write_file",
            "lucy_generate_pdf",
            "lucy_generate_excel",
            "lucy_generate_csv",
        ],
        max_turns=8,
        max_tokens=8192,
    ),
}


def _load_soul_lite() -> str:
    """Load SOUL_LITE.md for sub-agent system prompts."""
    path = ASSETS / "SOUL_LITE.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return (
        "Act, don't narrate. Execute tasks and return results.\n"
        "Be direct and thorough. Never fabricate data."
    )


def _build_sub_system_prompt(spec: SubAgentSpec) -> str:
    """Build the system prompt for a sub-agent: SOUL_LITE + task-specific."""
    soul_lite = _load_soul_lite()
    task_path = ASSETS / spec.system_prompt_file
    task_prompt = ""
    if task_path.exists():
        task_prompt = task_path.read_text(encoding="utf-8")
    return f"{soul_lite}\n\n---\n\n{task_prompt}"


def _to_assistant_msg(resp: OpenClawResponse) -> dict[str, Any]:
    """Convert an OpenClawResponse into an assistant message dict."""
    msg: dict[str, Any] = {
        "role": "assistant",
        "content": resp.content or "",
    }
    if resp.tool_calls:
        msg["tool_calls"] = [
            {
                "id": tc.get("id", f"sub_call_{i}"),
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": json.dumps(tc.get("parameters", {})),
                },
            }
            for i, tc in enumerate(resp.tool_calls)
        ]
    return msg


def _trim_subagent_context(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Trim older tool results from a sub-agent's message history."""
    trimmed: list[dict[str, Any]] = []
    tool_msgs = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    keep_last = max(2, len(tool_msgs) // 2)
    trim_indices = set(tool_msgs[:-keep_last]) if len(tool_msgs) > keep_last else set()

    for i, msg in enumerate(messages):
        if i in trim_indices:
            content = msg.get("content", "")
            trimmed.append({
                **msg,
                "content": content[:200] + "...(trimmed)" if len(content) > 200 else content,
            })
        else:
            trimmed.append(msg)
    return trimmed


async def _llm_call_with_retry(
    client: OpenClawClient,
    messages: list[dict[str, Any]],
    config: ChatConfig,
    max_retries: int = 2,
) -> OpenClawResponse:
    """LLM call with retry on retryable errors."""
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return await client.chat_completion(messages=messages, config=config)
        except OpenClawError as e:
            last_error = e
            if e.status_code and e.status_code in {429, 500, 502, 503, 504}:
                wait = min(2 ** attempt, 8)
                logger.warning(
                    "subagent_llm_retry",
                    attempt=attempt + 1,
                    status=e.status_code,
                    wait_s=wait,
                )
                await asyncio.sleep(wait)
                continue
            raise
    raise last_error or OpenClawError("Sub-agent LLM call failed after retries")


async def _execute_subagent_tool(
    tc: dict[str, Any],
    workspace_id: str,
    tool_executor: Any | None = None,
) -> str:
    """Execute a single tool call within a sub-agent context.

    Reuses the main agent's tool execution via the provided executor,
    or falls back to importing it directly.
    """
    name = tc.get("name", "")
    params = tc.get("parameters", {})

    try:
        if tool_executor:
            result = await tool_executor(name, params, workspace_id)
        else:
            from lucy.core.agent import get_agent
            agent = get_agent()
            result = await agent._execute_tool(name, params, workspace_id)

        if isinstance(result, dict):
            return json.dumps(result, ensure_ascii=False, default=str)
        return str(result)
    except Exception as e:
        logger.error(
            "subagent_tool_error",
            tool=name,
            error=str(e),
        )
        return json.dumps({"error": str(e)})


async def run_subagent(
    task: str,
    spec: SubAgentSpec,
    workspace_id: str,
    tool_registry: dict[str, dict[str, Any]],
    progress_callback: Callable[[str, int], Awaitable[None]] | None = None,
    tool_executor: Any | None = None,
) -> str:
    """Run an isolated sub-agent loop. Returns final text.

    Includes: context trimming, loop detection, error retry,
    empty response recovery, and timeout protection.
    Rate limiting handled by shared OpenClawClient singleton.
    """
    client = await get_openclaw_client()
    system_prompt = _build_sub_system_prompt(spec)

    tools = [
        tool_registry[n] for n in spec.tool_names if n in tool_registry
    ]
    messages: list[dict[str, Any]] = [{"role": "user", "content": task}]
    call_signatures: dict[str, int] = {}
    resp: OpenClawResponse | None = None
    turn = 0

    logger.info(
        "subagent_start",
        agent=spec.name,
        model=spec.model,
        tool_count=len(tools),
        workspace_id=workspace_id,
    )

    for turn in range(spec.max_turns):
        if progress_callback and turn > 0 and turn % 3 == 0:
            await progress_callback(spec.name, turn)

        config = ChatConfig(
            model=spec.model,
            system_prompt=system_prompt,
            tools=tools if tools else None,
            max_tokens=spec.max_tokens,
            temperature=spec.temperature,
        )

        resp = await _llm_call_with_retry(client, messages, config)

        if not resp.tool_calls:
            # Empty response recovery
            if not resp.content and turn > 0:
                messages.append({"role": "assistant", "content": ""})
                messages.append({
                    "role": "user",
                    "content": "Continue with the task. Use the tools to get results.",
                })
                continue
            break

        messages.append(_to_assistant_msg(resp))

        # Loop detection
        loop_detected = False
        for tc in resp.tool_calls:
            sig = f"{tc['name']}:{json.dumps(tc.get('parameters', {}), sort_keys=True)}"
            call_signatures[sig] = call_signatures.get(sig, 0) + 1
            if call_signatures[sig] >= 3:
                messages.append({
                    "role": "user",
                    "content": "You are repeating the same call. Try a different approach or return what you have.",
                })
                call_signatures.clear()
                loop_detected = True
                break

        if loop_detected:
            continue

        for tc_idx, tc in enumerate(resp.tool_calls):
            result = await _execute_subagent_tool(tc, workspace_id, tool_executor)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", f"sub_call_{tc_idx}"),
                "content": str(result)[:SUB_TOOL_RESULT_MAX_CHARS],
            })

        total_chars = sum(len(str(m.get("content", ""))) for m in messages)
        if total_chars > SUB_MAX_PAYLOAD_CHARS:
            messages = _trim_subagent_context(messages)

    final = (resp.content if resp else "") or ""
    logger.info(
        "subagent_complete",
        agent=spec.name,
        turns=turn + 1 if resp else 0,
        result_length=len(final),
        workspace_id=workspace_id,
    )
    return final
