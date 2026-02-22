"""Central router for model calls.

TIER_1_FAST calls OpenRouter directly (no VPS hop) for minimum latency.
TIER_2+ goes through OpenClaw gateway on VPS for tool orchestration.
"""

from __future__ import annotations

import asyncio
import structlog
from typing import Any

import httpx

from lucy.routing.tiers import ModelTier, get_tier_config
from lucy.costs.tracker import log_cost
from lucy.core.openclaw import OpenClawResponse, load_soul

logger = structlog.get_logger()

_direct_client: httpx.AsyncClient | None = None


def _get_direct_client() -> httpx.AsyncClient:
    """Shared httpx client for direct OpenRouter calls."""
    global _direct_client
    if _direct_client is None:
        # Import here to avoid circular imports
        from lucy.config import settings
        api_key = settings.openrouter_api_key or os.environ.get("OPENROUTER_API_KEY", "")
        _direct_client = httpx.AsyncClient(
            base_url="https://openrouter.ai/api",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://lucy.ai",
                "X-Title": "Lucy AI",
            },
            timeout=httpx.Timeout(60.0, connect=5.0),
        )
    return _direct_client


class ModelRouter:
    """Intelligent router for LLM calls.

    TIER_1 -> direct to OpenRouter (fastest path, ~2-4s)
    TIER_2+ -> via OpenClaw VPS gateway (supports tools/memory)
    """

    async def route(
        self,
        messages: list[dict[str, Any]],
        tier: ModelTier = ModelTier.TIER_2_STANDARD,
        workspace_id: str | None = None,
        task_id: str | None = None,
        **kwargs: Any,
    ) -> OpenClawResponse:
        """Route a request to the appropriate model and path.
        
        ALWAYS uses direct OpenRouter path — the OpenClaw VPS has reliability issues
        and OpenRouter now supports tools directly.
        """
        config = get_tier_config(tier)
        models_to_try = [config.primary_model] + config.fallback_models

        # Always use direct OpenRouter path — VPS is unreliable
        # OpenRouter supports OpenAI-compatible tools directly
        last_error = None

        for model in models_to_try:
            try:
                response = await self._call_direct(model, messages, config, **kwargs)

                if workspace_id and response.usage:
                    asyncio.create_task(
                        log_cost(
                            workspace_id=workspace_id,
                            task_id=task_id,
                            model=model,
                            usage=response.usage,
                        )
                    )
                return response

            except Exception as e:
                logger.warning("model_call_failed", model=model, error=str(e))
                last_error = e

        raise Exception(f"All models in {tier.name} failed: {last_error}")

    async def _call_direct(
        self,
        model: str,
        messages: list[dict[str, Any]],
        config: Any,
        **kwargs: Any,
    ) -> OpenClawResponse:
        """Call OpenRouter directly — bypasses VPS for minimum latency.
        
        Now supports tools/function calling directly through OpenRouter.
        """
        or_model = model.replace("openrouter/", "", 1)
        client = _get_direct_client()

        soul = load_soul()

        # Inject current date/time context so the LLM constructs correct date
        # parameters for tools like Google Calendar.
        # tz_offset_hours comes from the call site (workspace settings); defaults to IST.
        from datetime import datetime, timezone, timedelta
        tz_offset_hours: float = kwargs.get("tz_offset_hours", 5.5)
        tz_label: str = kwargs.get("tz_label", "Asia/Kolkata (IST, UTC+5:30)")

        full_hours = int(tz_offset_hours)
        minutes = int(round((tz_offset_hours - full_hours) * 60))
        user_tz = timezone(timedelta(hours=full_hours, minutes=minutes))

        now_utc = datetime.now(timezone.utc)
        now_local = now_utc.astimezone(user_tz)
        today_start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end_local = today_start_local + timedelta(days=1)

        tomorrow_start_local = today_end_local
        tomorrow_end_local = tomorrow_start_local + timedelta(days=1)

        date_context = (
            f"\n\n## Current Context\n"
            f"- Current UTC time: {now_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
            f"- User's timezone: {tz_label}\n"
            f"- User's local time: {now_local.strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
            f"- Today (local): {now_local.strftime('%A, %B %d, %Y')}\n"
            f"- Today start (RFC3339): {today_start_local.isoformat()}\n"
            f"- Today end (RFC3339): {today_end_local.isoformat()}\n"
            f"- Tomorrow start (RFC3339): {tomorrow_start_local.isoformat()}\n"
            f"- Tomorrow end (RFC3339): {tomorrow_end_local.isoformat()}\n"
            f"\n## Tool Calling Rules\n"
            f"- When calling calendar tools for 'today', use time_min={today_start_local.isoformat()} and time_max={today_end_local.isoformat()}\n"
            f"- When calling calendar tools for 'tomorrow', use time_min={tomorrow_start_local.isoformat()} and time_max={tomorrow_end_local.isoformat()}\n"
            f"- ALWAYS use concrete RFC3339 timestamps — NEVER use template variables like {{{{current_date_time.start}}}}\n"
            f"- For calendar_id, use 'primary' unless told otherwise\n"
            f"- If a tool returns data, use it and move on — do NOT call the same tool again with the same parameters\n"
            f"- You HAVE access to all tools listed in your tool definitions — never claim you don't have access to a tool that is available to you"
        )

        final_messages = [{"role": "system", "content": soul + date_context}] + list(messages)

        payload = {
            "model": or_model,
            "messages": final_messages,
            "max_tokens": kwargs.get("max_tokens", config.max_tokens),
            "temperature": kwargs.get("temperature", 0.7),
        }
        
        # Add tools if provided (OpenRouter supports OpenAI-compatible function calling)
        tools = kwargs.get("tools")
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        logger.info("direct_openrouter_call", model=or_model, has_tools=bool(tools))
        resp = await client.post("/v1/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()

        choices = data.get("choices", [])
        if not choices:
            return OpenClawResponse(content="", usage=data.get("usage"))
            
        message = choices[0].get("message", {})
        content = message.get("content", "")
        
        # Extract tool calls if present
        tool_calls = None
        raw_tool_calls = message.get("tool_calls")
        if raw_tool_calls:
            import json
            tool_calls = []
            for tc in raw_tool_calls:
                args = tc.get("function", {}).get("arguments", "{}")
                parse_error = None
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        parse_error = "invalid_json_arguments"
                        args = {}
                tool_calls.append({
                    "id": tc.get("id"),
                    "name": tc.get("function", {}).get("name"),
                    "parameters": args,
                    "parse_error": parse_error,
                })

        return OpenClawResponse(
            content=content,
            tool_calls=tool_calls,
            usage=data.get("usage"),
        )


# Singleton instance
_router: ModelRouter | None = None

def get_router() -> ModelRouter:
    """Get singleton ModelRouter."""
    global _router
    if _router is None:
        _router = ModelRouter()
    return _router
