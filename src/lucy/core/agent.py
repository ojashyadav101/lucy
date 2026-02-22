"""LucyAgent — Core orchestrator for task execution.

Manages the flow:
1. Task created in DB (by Slack handler)
2. Agent picks up task, spawns OpenClaw session
3. Execute task with OpenClaw
4. Stream progress/results back to Slack
5. Update task status, close session
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone, timedelta
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from lucy.core.openclaw import (
    OpenClawClient,
    OpenClawResponse,
    ChatConfig,
    get_openclaw_client,
)
from lucy.core.types import TaskContext
from lucy.db.models import Task, TaskStatus, User, Workspace
from lucy.db.session import AsyncSessionLocal
from lucy.memory.vector import get_vector_memory
from lucy.memory.sync import sync_task_to_memory
from lucy.routing.classifier import get_classifier
from lucy.routing.tiers import ModelTier
from lucy.observability.metrics import get_metrics
from lucy.core.safety import get_auth_builder, get_claim_validator, get_provider_formatter
from lucy.retrieval.tool_retriever import get_retriever, INITIAL_K, EXPANDED_K, MIN_RELEVANCE_SCORE
from lucy.core.circuit_breaker import get_circuit_breaker, CircuitBreakerOpen
from lucy.core.timeout import with_timeout, classify_tool, ToolType

logger = structlog.get_logger()

TOOL_RESULT_MAX_CHARS = 12000
MAX_TOOL_TURNS = 5
MAX_TOOL_CONTEXT_MESSAGES = 40
_NOISY_TOOL_KEYS = {
    "etag",
    "kind",
    "creator",
    "organizer",
    "attendees",
    "conferenceData",
    "defaultReminders",
    "reminders",
    "iCalUID",
    "sequence",
}
class LucyAgent:
    """Lucy agent for executing tasks via OpenClaw.

    Usage:
        agent = LucyAgent()
        
        # Process a single task
        await agent.execute_task(task_id)
        
        # Or run as background worker
        await agent.run_worker()
    """

    def __init__(self, openclaw: OpenClawClient | None = None):
        """Initialize agent.

        Args:
            openclaw: OpenClaw client (creates default if None)
        """
        self.openclaw = openclaw
        self._running = False

    async def _get_client(self) -> OpenClawClient:
        """Get or create OpenClaw client."""
        if self.openclaw is None:
            self.openclaw = await get_openclaw_client()
        return self.openclaw

    def _compact_tool_value(self, value: Any, depth: int = 0) -> Any:
        """Compact tool payloads so LLM sees more complete item coverage.

        This is intentionally generic for all integrations: we remove noisy keys,
        keep key business fields, and bound nested sizes.
        """
        if depth > 5:
            return "<max_depth>"

        if isinstance(value, dict):
            compacted: dict[str, Any] = {}

            # Prefer stable ordering for high-signal keys first.
            priority_keys = (
                "id",
                "name",
                "title",
                "summary",
                "subject",
                "status",
                "state",
                "url",
                "htmlLink",
                "hangoutLink",
                "start",
                "end",
                "date",
                "dateTime",
                "due",
                "message",
                "error",
                "items",
                "results",
                "data",
            )

            ordered_keys = [k for k in priority_keys if k in value] + [
                k for k in value.keys() if k not in priority_keys
            ]

            for key in ordered_keys:
                if key in _NOISY_TOOL_KEYS:
                    continue
                compacted[key] = self._compact_tool_value(value[key], depth + 1)
            return compacted

        if isinstance(value, list):
            max_items = 120
            items = value[:max_items]
            compacted_list = [self._compact_tool_value(v, depth + 1) for v in items]
            if len(value) > max_items:
                compacted_list.append({"_truncated_items": len(value) - max_items})
            return compacted_list

        if isinstance(value, str):
            return value if len(value) <= 500 else value[:500] + "...(truncated)"

        return value

    def _tool_result_to_llm_content(self, result_content: Any) -> str:
        """Serialize tool result with structure-aware compaction."""
        if isinstance(result_content, (dict, list)):
            compacted = self._compact_tool_value(result_content)
            serialized = json.dumps(compacted, ensure_ascii=False, separators=(",", ":"))
        else:
            serialized = str(result_content)

        if len(serialized) > TOOL_RESULT_MAX_CHARS:
            removed = len(serialized) - TOOL_RESULT_MAX_CHARS
            serialized = (
                f'[TRUNCATED: removed {removed} chars]\\n'
                + serialized[:TOOL_RESULT_MAX_CHARS]
                + "...(truncated_after_compaction)"
            )
        return serialized

    def _tool_call_signature(self, call: dict[str, Any]) -> str:
        """Create deterministic signature for repeated-call loop detection."""
        name = call.get("name", "")
        params = call.get("parameters", {}) or {}
        try:
            params_key = json.dumps(params, sort_keys=True, separators=(",", ":"))
        except Exception:
            params_key = str(params)
        return f"{name}:{params_key}"

    def _trim_tool_context(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Bound tool-loop context growth with a sliding window."""
        if len(messages) <= MAX_TOOL_CONTEXT_MESSAGES:
            return messages
        return messages[-MAX_TOOL_CONTEXT_MESSAGES:]

    def _classify_tool_error(self, error_text: str) -> str:
        """Classify tool errors for better model recovery behavior."""
        t = error_text.lower()
        if any(k in t for k in ("timeout", "temporarily", "429", "rate limit", "connection reset")):
            return "retryable"
        if any(k in t for k in ("not connected", "unauthorized", "forbidden", "permission")):
            return "auth"
        if any(k in t for k in ("invalid", "missing", "required", "schema", "parse")):
            return "invalid_params"
        return "fatal"

    async def execute_task(self, task_id: UUID) -> TaskStatus:
        """Execute a single task end-to-end.

        Args:
            task_id: Task ID to execute

        Returns:
            Final task status
        """
        async with AsyncSessionLocal() as db:
            # Load task with relationships
            result = await db.execute(
                select(Task)
                .where(Task.id == task_id)
                .options(
                    selectinload(Task.workspace),
                    selectinload(Task.requester),
                )
            )
            task = result.scalar_one_or_none()
            
            if not task:
                logger.error("task_not_found", task_id=str(task_id))
                return TaskStatus.FAILED
            
            # Build context
            ctx = TaskContext(
                task=task,
                workspace=task.workspace,
                requester=task.requester,
                slack_channel_id=task.config.get("channel_id") if task.config else None,
                slack_thread_ts=task.config.get("thread_ts") if task.config else None,
            )
            
            try:
                original_text = task.config.get("original_text", "") if task.config else ""

                if not task.intent or not getattr(task, "model_tier", None):
                    classifier = get_classifier()
                    classification = await classifier.classify(original_text)
                    task.intent = classification.intent
                    task.model_tier = classification.tier.name

                task.status = TaskStatus.RUNNING
                task.started_at = datetime.now(timezone.utc)
                await db.commit()

                logger.info(
                    "task_execution_started",
                    task_id=str(task_id),
                    intent=task.intent,
                    tier=getattr(task, "model_tier", "UNKNOWN"),
                )

                result_status = await self._execute_with_openclaw(ctx, db)
                return result_status
                
            except Exception as e:
                logger.error(
                    "task_execution_failed",
                    task_id=str(task_id),
                    workspace_id=str(ctx.workspace.id) if ctx.workspace else "unknown",
                    intent=task.intent,
                    error=str(e),
                    exc_info=True,
                )
                
                task.status = TaskStatus.FAILED
                task.status_reason = str(e)[:500]
                task.error_count += 1
                task.last_error = str(e)[:1000]
                await db.commit()

                _m = get_metrics()
                await _m.inc_labeled("tasks_total", "failed")

                return TaskStatus.FAILED

    async def _execute_with_openclaw(
        self,
        ctx: TaskContext,
        db: Any,  # AsyncSession
    ) -> TaskStatus:
        """Execute task via OpenClaw — optimized for speed.

        Fast path: simple chat/lookup skip tool fetching and memory search.
        """
        import time as _time
        t0 = _time.monotonic()

        original_text = ctx.task.config.get("original_text", "") if ctx.task.config else ""
        intent = ctx.task.intent or "chat"
        tier_name = getattr(ctx.task, "model_tier", None) or "TIER_2_STANDARD"
        tier = ModelTier[tier_name] if hasattr(ModelTier, tier_name) else ModelTier.TIER_2_STANDARD

        # ── Build conversation history from Slack thread ────────────────────
        messages = await self._build_thread_messages(ctx, original_text)

        # ── Memory: always attempt (no intent gating) ───────────────────────
        memory = get_vector_memory()
        try:
            def _search():
                return memory.search(
                    query=original_text,
                    workspace_id=ctx.workspace.id,
                    user_id=ctx.requester.id if ctx.requester else None,
                    limit=5,
                )
            results = await asyncio.to_thread(_search)
            if results:
                memory_context = "\n".join(
                    [f"- {r.get('memory', r.get('text', str(r)))}" for r in results]
                )
                messages[-1]["content"] += "\n\nRelevant memories:\n" + memory_context
                logger.info("memory_context_injected", count=len(results))
        except Exception as e:
            logger.warning("memory_search_skipped", error=str(e))

        # ── Tools: two-tier strategy (no intent gating) ─────────────────────
        # 1. Always try BM25 retrieval (<1ms) for connected integrations
        # 2. Always provide Composio meta-tools as fallback
        # The LLM decides whether to use tools, not the classifier.
        tools = None
        _used_retrieval = False

        try:
            from lucy.integrations.composio_client import get_composio_client
            from lucy.integrations.registry import get_integration_registry
            registry = get_integration_registry()
            active_providers = await registry.get_active_providers(ctx.workspace.id)
            connected_apps = {p.lower() for p in active_providers}

            # Detect multi-step queries that need tools from multiple apps
            _lower = original_text.lower()
            _numbered = re.findall(r'\d+[\.\)]\s', original_text)
            _chain_words = re.findall(r'\b(?:then|after that|next|also|and then)\b', _lower)
            _and_count = _lower.count(' and ')
            _is_multi_step = (
                len(_chain_words) >= 2
                or len(_numbered) >= 2
                or _and_count >= 2
            )
            _retrieval_k = EXPANDED_K if _is_multi_step else INITIAL_K
            logger.info(
                "multi_step_detection",
                is_multi_step=_is_multi_step,
                k=_retrieval_k,
                numbered_steps=len(_numbered),
                chain_words=len(_chain_words),
                and_count=_and_count,
                text_preview=original_text[:100],
            )

            # Tier 1: BM25 fast-path (< 1ms) — finds exact tools for connected apps
            retrieval_result = await get_retriever().retrieve(
                workspace_id=ctx.workspace.id,
                query=original_text,
                connected_apps=connected_apps,
                k=_retrieval_k,
            )

            if retrieval_result is not None and retrieval_result.tools:
                tools = retrieval_result.tools
                _used_retrieval = True
                tier = max(tier, ModelTier.TIER_2_STANDARD)
                logger.info(
                    "tools_via_bm25_fast_path",
                    workspace_id=str(ctx.workspace.id),
                    tool_count=len(tools),
                    top_score=round(retrieval_result.top_score, 2),
                )
            elif connected_apps:
                # BM25 index empty but we have connected apps — populate now
                logger.info("bm25_cold_populating_inline", apps=list(connected_apps))
                await get_retriever().populate(ctx.workspace.id, connected_apps)
                retrieval_result = await get_retriever().retrieve(
                    workspace_id=ctx.workspace.id,
                    query=original_text,
                    connected_apps=connected_apps,
                    k=_retrieval_k,
                )
                if retrieval_result and retrieval_result.tools:
                    tools = retrieval_result.tools
                    _used_retrieval = True
                    tier = max(tier, ModelTier.TIER_2_STANDARD)
                    logger.info(
                        "tools_via_bm25_after_inline_populate",
                        tool_count=len(tools),
                        top_score=round(retrieval_result.top_score, 2),
                    )

            if not tools:
                # Tier 2: Composio meta-tools — only if no direct tools found
                client = get_composio_client()
                tools = await client.get_tools(user_id=str(ctx.workspace.id))
                logger.info(
                    "tools_via_meta_tools_fallback",
                    workspace_id=str(ctx.workspace.id),
                    tool_count=len(tools) if tools else 0,
                )
                if tools:
                    tier = max(tier, ModelTier.TIER_2_STANDARD)

        except Exception as e:
            logger.error("tools_fetch_exception", workspace_id=str(ctx.workspace.id), error=str(e))
            tools = None

        # Lazy import to avoid circular dependency
        from lucy.routing.router import get_router
        router = get_router()

        logger.info(
            "llm_call_start",
            task_id=str(ctx.task.id),
            tier=tier.name,
            intent=intent,
            has_tools=tools is not None,
            tool_count=len(tools) if tools else 0,
            message_count=len(messages),
            prep_ms=round((_time.monotonic() - t0) * 1000),
        )

        # Multi-turn tool execution loop.
        # Composio meta-tools (SEARCH_TOOLS → MULTI_EXECUTE_TOOL) need
        # multiple LLM ↔ tool rounds to complete a request.
        import json as _json

        all_messages = list(messages)
        response_content = ""
        last_tool_results: list[dict[str, Any]] = []
        repeated_signature_counts: dict[str, int] = {}
        available_tool_names = {
            t.get("function", {}).get("name", "")
            for t in (tools or [])
            if isinstance(t, dict)
        }

        # Per-task reliability accumulators for metrics emission at task end.
        _metrics = get_metrics()
        _task_tool_calls = 0
        _task_tool_errors = 0
        _task_tool_loops = 0
        _task_unknown_tools = 0
        _task_no_text = False
        # Track whether any tool result was truncated (for claim validation).
        _task_had_truncation = False
        # Serialised tool result strings fed to the LLM (for claim validator check).
        _llm_tool_result_strings: list[str] = []
        # Track whether staged K expansion has already been done this task.
        _retrieval_expanded = False

        for turn in range(MAX_TOOL_TURNS):
            # ── Staged K expansion (retrieval path only) ──────────────────────
            # If all tool calls in the previous turn produced errors and we haven't
            # expanded yet, refresh the tool set with EXPANDED_K to surface more
            # candidate schemas before the next LLM call.
            if (
                _used_retrieval
                and not _retrieval_expanded
                and turn > 0
                and last_tool_results
                and all(r.get("status") == "error" for r in last_tool_results)
            ):
                try:
                    from lucy.integrations.registry import get_integration_registry
                    _reg = get_integration_registry()
                    _active = {p.lower() for p in await _reg.get_active_providers(ctx.workspace.id)}
                    expanded_result = await get_retriever().retrieve(
                        workspace_id=ctx.workspace.id,
                        query=original_text,
                        connected_apps=_active,
                        k=EXPANDED_K,
                    )
                    if expanded_result and expanded_result.tools:
                        tools = expanded_result.tools
                        available_tool_names = {
                            t.get("function", {}).get("name", "")
                            for t in tools
                            if isinstance(t, dict)
                        }
                        _retrieval_expanded = True
                        logger.info(
                            "retrieval_staged_expansion",
                            workspace_id=str(ctx.workspace.id),
                            task_id=str(ctx.task.id),
                            turn=turn,
                            expanded_k=EXPANDED_K,
                            new_count=len(tools),
                        )
                except Exception as _exp_err:
                    logger.warning("retrieval_staged_expansion_failed", error=str(_exp_err))

            _llm_turn_t0 = _time.monotonic()
            # Pass workspace timezone so the router injects the correct date context
            ws_settings = ctx.workspace.settings or {} if ctx.workspace else {}
            response = await router.route(
                messages=all_messages,
                tier=tier,
                workspace_id=str(ctx.workspace.id),
                task_id=str(ctx.task.id),
                tools=tools,
                temperature=0.7,
                tz_offset_hours=ws_settings.get("tz_offset_hours", 5.5),
                tz_label=ws_settings.get("tz_label", "Asia/Kolkata (IST, UTC+5:30)"),
            )

            _llm_turn_ms = (_time.monotonic() - _llm_turn_t0) * 1000
            await _metrics.record("llm_turn_latency_ms", _llm_turn_ms)

            response_content = response.content or ""
            tool_calls = response.tool_calls

            # If LLM returned content and no tool calls, we're done
            if not tool_calls:
                # Safety net: detect false "no access" claims when tools ARE available
                _lower_resp = response_content.lower()
                _false_no_access = (
                    tools
                    and ("don't have access" in _lower_resp or "do not have access" in _lower_resp
                         or "not connected" in _lower_resp or "need to connect" in _lower_resp)
                    and turn == 0  # only on first turn to avoid infinite retry
                )
                if _false_no_access:
                    logger.warning(
                        "false_no_access_detected",
                        task_id=str(ctx.task.id),
                        tool_count=len(tools),
                        response_preview=response_content[:200],
                    )
                    tool_names = [t.get("function", {}).get("name", "") for t in tools if isinstance(t, dict)]
                    correction = (
                        f"You DO have access to the following tools: {', '.join(tool_names[:15])}. "
                        f"Please use them to fulfill the user's request instead of saying you don't have access."
                    )
                    all_messages.append({"role": "assistant", "content": response_content})
                    all_messages.append({"role": "user", "content": correction})
                    continue
                break

            call_signature = "||".join(sorted(self._tool_call_signature(tc) for tc in tool_calls))
            repeated_signature_counts[call_signature] = repeated_signature_counts.get(call_signature, 0) + 1
            if repeated_signature_counts[call_signature] >= 3:
                logger.warning(
                    "tool_loop_detected",
                    task_id=str(ctx.task.id),
                    turn=turn + 1,
                    signature=call_signature[:300],
                )
                await _metrics.tool_loop_detected()
                _task_tool_loops += 1
                response_content = (
                    "I’m seeing repeated tool calls and stopped to avoid looping. "
                    "Please try a slightly more specific request."
                )
                break

            logger.info(
                "tool_turn",
                turn=turn + 1,
                tool_calls=[tc.get("name") for tc in tool_calls],
                task_id=str(ctx.task.id),
            )

            # Build assistant message with tool_calls in OpenAI format
            assistant_msg: dict[str, Any] = {"role": "assistant", "content": response_content}
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.get("id", f"call_{i}"),
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": _json.dumps(tc.get("parameters", {})),
                    },
                }
                for i, tc in enumerate(tool_calls)
            ]
            all_messages.append(assistant_msg)
            all_messages = self._trim_tool_context(all_messages)

            # ── Destructive action guardrail ─────────────────────────────
            # On the first request in a thread (no prior user confirmation),
            # only allow read-only tools. Any tool that modifies state must
            # wait for explicit user confirmation.
            _READ_VERBS = {"LIST", "GET", "FIND", "FETCH", "SEARCH", "ABOUT", "WATCH"}
            _has_prior_confirmation = len([m for m in all_messages if m.get("role") == "user"]) > 1

            if not _has_prior_confirmation and tool_calls:
                _write_calls = []
                _read_calls = []
                for tc in tool_calls:
                    name = tc.get("name", "").upper()
                    name_parts = set(name.split("_"))
                    if name_parts & _READ_VERBS:
                        _read_calls.append(tc)
                    else:
                        _write_calls.append(tc)

                if _write_calls:
                    blocked_names = [tc.get("name", "") for tc in _write_calls]
                    logger.warning(
                        "write_action_blocked",
                        task_id=str(ctx.task.id),
                        turn=turn + 1,
                        blocked_tools=blocked_names,
                        allowed_reads=[tc.get("name", "") for tc in _read_calls],
                    )

                    blocked_msg = (
                        "ACTION BLOCKED: This write/destructive action requires user "
                        "confirmation. You MUST respond to the user describing exactly "
                        "what you found and what you plan to do, then ask 'Should I go "
                        "ahead?' and WAIT for their reply before executing any write action."
                    )

                    # Build assistant message with all tool_calls
                    assistant_msg: dict[str, Any] = {"role": "assistant", "content": response_content or ""}
                    assistant_msg["tool_calls"] = [
                        {
                            "id": tc.get("id", f"call_{i}"),
                            "type": "function",
                            "function": {
                                "name": tc.get("name", ""),
                                "arguments": _json.dumps(tc.get("parameters", {})),
                            },
                        }
                        for i, tc in enumerate(tool_calls)
                    ]
                    all_messages.append(assistant_msg)

                    # Execute reads, inject blocked message for writes
                    if _read_calls:
                        read_results = await self._execute_tools(_read_calls, ctx, available_tool_names)
                        for idx, result in enumerate(read_results):
                            tc = _read_calls[idx]
                            raw = result.get("result", result.get("error", ""))
                            all_messages.append({
                                "role": "tool",
                                "tool_call_id": tc.get("id", f"call_{tool_calls.index(tc)}"),
                                "content": str(raw)[:8000] if raw else "OK",
                            })
                    for tc in _write_calls:
                        all_messages.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id", f"call_{tool_calls.index(tc)}"),
                            "content": blocked_msg,
                        })
                    all_messages = self._trim_tool_context(all_messages)
                    continue

            # Execute each tool call and record per-task reliability counters
            _tool_exec_t0 = _time.monotonic()
            tool_results = await self._execute_tools(tool_calls, ctx, available_tool_names)
            await _metrics.record("tool_latency_ms", (_time.monotonic() - _tool_exec_t0) * 1000)
            last_tool_results = tool_results

            for result in tool_results:
                _tool_name = result.get("tool", "unknown")
                await _metrics.tool_called(_tool_name)
                _task_tool_calls += 1
                if result.get("status") == "error":
                    _error_type = result.get("error_type", "fatal")
                    await _metrics.tool_error(_error_type)
                    _task_tool_errors += 1
                    if _error_type == "unknown_tool":
                        await _metrics.unknown_tool_called(_tool_name)
                        _task_unknown_tools += 1
                elif _used_retrieval:
                    # Successful call via retrieval path — boost this tool's score
                    get_retriever().record_tool_usage(ctx.workspace.id, _tool_name)

            for idx, result in enumerate(tool_results):
                raw_content = result.get("result", result.get("error", ""))

                # Phase 2: intercept auth errors and replace with connect-link message
                if result.get("status") == "error" and result.get("error_type") == "auth":
                    raw_content = await get_auth_builder().build_for_tool_error(
                        tool_name=result.get("tool", ""),
                        error_text=result.get("error", ""),
                        workspace_id=ctx.workspace.id,
                    )

                result_content = self._tool_result_to_llm_content(raw_content)

                # Track if this result was truncated (for claim validation later).
                if "[TRUNCATED:" in result_content:
                    _task_had_truncation = True
                _llm_tool_result_strings.append(result_content)

                all_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_calls[idx].get("id", f"call_{idx}"),
                    "content": result_content,
                })
            all_messages = self._trim_tool_context(all_messages)

        # Global reliability fallback:
        # if tool loops end without final model text, synthesize a grounded response
        # from the most recent tool outputs (prevents empty/no_text failures).
        if not (response_content or "").strip():
            _task_no_text = True
            await _metrics.no_text_fallback()

            provider_text = get_provider_formatter().format(last_tool_results, original_text)
            if provider_text:
                response_content = provider_text
            else:
                response_content = (
                    "I couldn't finalize the response after multiple tool calls. "
                    "Please retry your request in one message, and I'll run it again."
                )

        # Phase 2: claim validation — qualify any completeness assertions if data
        # was truncated or the tool payload was known to be partial.
        if response_content and _task_had_truncation:
            response_content = get_claim_validator().validate(
                response_content,
                is_partial=get_claim_validator().response_is_partial(_llm_tool_result_strings),
            )

        elapsed_ms = round((_time.monotonic() - t0) * 1000)

        # Resolve actual model name for display
        from lucy.routing.tiers import get_tier_config
        tier_config = get_tier_config(tier)
        model_name = tier_config.primary_model.replace("openrouter/", "")

        ctx.task.status = TaskStatus.COMPLETED
        ctx.task.completed_at = datetime.now(timezone.utc)
        ctx.task.result_summary = response_content[:500]
        ctx.task.result_data = {
            "full_response": response_content,
            "usage": response.usage,
            "elapsed_ms": elapsed_ms,
            "model": model_name,
            "message_count": len(messages),
        }
        await db.commit()

        # Emit consolidated task-level metrics log and update global histograms
        await _metrics.task_completed("completed", elapsed_ms)
        await _metrics.emit_task_log(
            logger=logger,
            task_id=str(ctx.task.id),
            elapsed_ms=elapsed_ms,
            intent=intent,
            model=model_name,
            tool_calls=_task_tool_calls,
            tool_errors=_task_tool_errors,
            tool_loops=_task_tool_loops,
            unknown_tools=_task_unknown_tools,
            no_text=_task_no_text,
            status="completed",
        )

        logger.info(
            "task_execution_completed",
            task_id=str(ctx.task.id),
            content_length=len(response_content),
            elapsed_ms=elapsed_ms,
            model=model_name,
        )

        class _Resp:
            def __init__(self, content):
                self.content = content
        await self._send_result_to_slack(ctx, _Resp(response_content))

        asyncio.create_task(sync_task_to_memory(ctx, response_content))

        return TaskStatus.COMPLETED

    async def _build_thread_messages(
        self,
        ctx: TaskContext,
        current_text: str,
    ) -> list[dict[str, Any]]:
        """Build LLM messages array from Slack thread history.

        Fetches previous messages in the thread so the LLM has full
        conversation context. Falls back to single message if thread
        fetch fails or there's no thread.
        """
        messages: list[dict[str, Any]] = []

        thread_ts = ctx.slack_thread_ts
        channel_id = ctx.slack_channel_id

        if thread_ts and channel_id:
            try:
                from lucy.config import settings
                import httpx

                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        "https://slack.com/api/conversations.replies",
                        params={
                            "channel": channel_id,
                            "ts": thread_ts,
                            "limit": 20,
                        },
                        headers={"Authorization": f"Bearer {settings.slack_bot_token}"},
                        timeout=5.0,
                    )
                    data = resp.json()

                if data.get("ok"):
                    thread_messages = data.get("messages", [])
                    # Exclude the very last message (that's our current one)
                    # and build alternating user/assistant messages
                    bot_user_id = None
                    for msg in thread_messages[:-1]:
                        text = msg.get("text", "").strip()
                        if not text:
                            continue

                        # Detect if this message is from our bot
                        is_bot = bool(msg.get("bot_id")) or msg.get("app_id")

                        if is_bot:
                            # Strip timing footers from previous Lucy responses
                            if "⏱️" in text:
                                text = text[:text.index("⏱️")].strip()
                            if text:
                                messages.append({"role": "assistant", "content": text})
                        else:
                            # Clean @mentions from user messages
                            import re
                            text = re.sub(r"<@[A-Z0-9]+>\s*", "", text).strip()
                            if text:
                                messages.append({"role": "user", "content": text})

                    logger.info(
                        "thread_history_loaded",
                        thread_ts=thread_ts,
                        history_messages=len(messages),
                        raw_messages=len(thread_messages),
                    )
                else:
                    logger.warning(
                        "thread_history_fetch_failed",
                        error=data.get("error"),
                        channel=channel_id,
                    )
            except Exception as e:
                logger.warning("thread_history_error", error=str(e))

        # Always append the current message last
        messages.append({"role": "user", "content": current_text})
        return messages

    async def _execute_tools(
        self,
        tool_calls: list[dict[str, Any]],
        ctx: TaskContext,
        available_tool_names: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute tool calls and return results.

        Composio meta-tools (COMPOSIO_SEARCH_TOOLS, COMPOSIO_MULTI_EXECUTE_TOOL,
        COMPOSIO_MANAGE_CONNECTIONS) are executed via composio.tools.execute().
        """
        results = []
        
        for call in tool_calls:
            tool_name = call.get("name", "")
            parameters = call.get("parameters", {})
            parse_error = call.get("parse_error")
            if parse_error:
                results.append({
                    "tool": tool_name,
                    "status": "error",
                    "error_type": "invalid_params",
                    "error": f"Tool call arguments could not be parsed: {parse_error}",
                })
                logger.warning(
                    "tool_call_argument_parse_error",
                    task_id=str(ctx.task.id),
                    tool=tool_name,
                )
                continue
            available = available_tool_names or set()
            if available and tool_name not in available:
                results.append({
                    "tool": tool_name,
                    "status": "error",
                    "error_type": "unknown_tool",
                    "error": f"Tool '{tool_name}' is not available in this workspace/toolset.",
                })
                logger.warning(
                    "invalid_tool_name_from_model",
                    task_id=str(ctx.task.id),
                    tool=tool_name,
                    available_count=len(available),
                )
                continue
            
            logger.info(
                "executing_tool",
                task_id=str(ctx.task.id),
                tool=tool_name,
                params_keys=list(parameters.keys()) if parameters else [],
            )
            
            try:
                # Composio meta-tools go through Composio SDK
                if tool_name.startswith("COMPOSIO_"):
                    result = await self._execute_composio_tool(
                        tool_name, parameters, ctx
                    )
                elif tool_name == "slack":
                    result = await self._execute_slack_tool(parameters, ctx)
                elif tool_name == "memory":
                    result = await self._execute_memory_tool(parameters, ctx)
                else:
                    # Integration tools (Linear, GitHub, etc.)
                    result = await self._execute_integration_tool(
                        tool_name, parameters, ctx
                    )
                
                results.append({
                    "tool": tool_name,
                    "status": "success",
                    "result": result,
                })
                
            except Exception as e:
                error_text = str(e)
                results.append({
                    "tool": tool_name,
                    "status": "error",
                    "error_type": self._classify_tool_error(error_text),
                    "error": error_text,
                })
        
        return results

    async def _execute_composio_tool(
        self,
        tool_name: str,
        parameters: dict[str, Any],
        ctx: TaskContext,
    ) -> dict[str, Any]:
        """Execute a Composio meta-tool via the SDK.

        Handles COMPOSIO_SEARCH_TOOLS, COMPOSIO_MULTI_EXECUTE_TOOL,
        COMPOSIO_MANAGE_CONNECTIONS, etc. Wrapped with:
          - Circuit breaker (composio_api) to block calls when service is degraded.
          - Per-tool timeout budget to prevent indefinite blocking.
        """
        from lucy.integrations.composio_client import get_composio_client
        client = get_composio_client()

        if not client.composio:
            return {"error": "Composio not initialized"}

        cb = get_circuit_breaker("composio_api")
        tool_type = classify_tool(tool_name)

        async def _call() -> dict[str, Any]:
            def _execute():
                return client.composio.tools.execute(
                    slug=tool_name,
                    arguments=parameters,
                    user_id=str(ctx.workspace.id),
                    dangerously_skip_version_check=True,
                )
            raw = await with_timeout(
                asyncio.to_thread(_execute),
                tool_type=tool_type,
                tool_name=tool_name,
            )
            # with_timeout returns a structured error dict on timeout
            if isinstance(raw, dict) and raw.get("status") == "error":
                raise RuntimeError(raw["error"])  # let circuit breaker count it
            return raw

        try:
            result = await cb.call(_call)
        except CircuitBreakerOpen as cbo:
            logger.warning(
                "composio_circuit_open",
                tool=tool_name,
                task_id=str(ctx.task.id),
                retry_after=cbo.retry_after,
            )
            return {
                "error": (
                    f"The integration service is temporarily unavailable "
                    f"(circuit open, retry in {cbo.retry_after:.0f}s). "
                    "Please try again shortly."
                ),
                "error_type": "retryable",
            }

        logger.info(
            "composio_tool_executed",
            tool=tool_name,
            task_id=str(ctx.task.id),
            result_type=type(result).__name__,
        )

        # Composio returns various types — normalize to dict/str
        if isinstance(result, dict):
            return result
        elif isinstance(result, str):
            return {"result": result}
        else:
            return {"result": str(result)}

    async def _execute_integration_tool(
        self,
        tool_name: str,
        parameters: dict[str, Any],
        ctx: TaskContext,
    ) -> dict[str, Any]:
        """Execute integration tool (Linear, GitHub, etc.) via Composio."""
        return await self._execute_composio_tool(tool_name, parameters, ctx)

    async def _send_result_to_slack(
        self,
        ctx: TaskContext,
        response: Any,
    ) -> None:
        """Send task result to Slack.

        Args:
            ctx: Task context
            response: Object with .content attribute
        """
        # For now, just log. In production, this would use Slack API
        # to update the original message or send a new one.
        logger.info(
            "would_send_to_slack",
            task_id=str(ctx.task.id),
            channel=ctx.slack_channel_id,
            thread=ctx.slack_thread_ts,
            response_length=len(getattr(response, "content", "")),
        )

    async def run_worker(self, poll_interval: float = 5.0) -> None:
        """Run as background worker, polling for tasks.

        Args:
            poll_interval: Seconds between polls
        """
        self._running = True
        logger.info("worker_started", poll_interval=poll_interval)
        
        while self._running:
            try:
                # Find pending tasks
                async with AsyncSessionLocal() as db:
                    from sqlalchemy import asc
                    
                    result = await db.execute(
                        select(Task)
                        .where(
                            Task.status == TaskStatus.CREATED,
                            Task.agent_id.is_(None),  # Unassigned
                        )
                        .order_by(asc(Task.priority), asc(Task.created_at))
                        .limit(10)
                    )
                    tasks = result.scalars().all()
                    
                    if tasks:
                        logger.info("found_pending_tasks", count=len(tasks))
                    
                    for task in tasks:
                        # Mark as assigned to this agent
                        # In future, could use multiple agents
                        task.status = TaskStatus.RUNNING
                        await db.commit()
                        
                        # Execute
                        await self.execute_task(task.id)
                
                await asyncio.sleep(poll_interval)
                
            except asyncio.CancelledError:
                logger.info("worker_cancelled")
                break
            except Exception as e:
                logger.error("worker_error", error=str(e))
                await asyncio.sleep(poll_interval)
        
        logger.info("worker_stopped")

    def stop(self) -> None:
        """Stop the background worker."""
        self._running = False


# Singleton instance
_agent: LucyAgent | None = None


async def get_agent() -> LucyAgent:
    """Get or create singleton LucyAgent."""
    global _agent
    if _agent is None:
        _agent = LucyAgent()
    return _agent


async def execute_task(task_id: UUID) -> TaskStatus:
    """Execute a task using the singleton agent."""
    agent = await get_agent()
    return await agent.execute_task(task_id)
