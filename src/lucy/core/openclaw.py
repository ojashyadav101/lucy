"""LLM client for Lucy.

Routes all requests through OpenRouter (openrouter.ai/api/v1).
OpenClaw stripped tool parameters, so we bypass it entirely.
OpenRouter provides OpenAI-compatible tool calling across 224+ models.

Primary model: minimax/minimax-m2.5
- Native interleaved thinking between tool calls
- #1 on OpenRouter for programming/technology
- $0.30/$1.10 per M tokens, 197K context

Streaming architecture:
    Main agent loop calls (with tools, expensive) use streaming mode.
    The stream lets us distinguish "hung" from "working":
      - Tokens flowing (even slowly) = model is working, no timeout
      - No tokens for STREAM_SILENCE_TIMEOUT = model is hung, cancel + escalate
    Cheap internal calls (planner, supervisor, humanize) use non-streaming.
"""

from __future__ import annotations

import asyncio
import collections
import json as _json
import time
from dataclasses import dataclass
from typing import Any

import certifi
import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from lucy.config import settings
from lucy.infra.circuit_breaker import openrouter_breaker

logger = structlog.get_logger()

_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

# Silence detection: if the stream produces no data for this many seconds,
# the model is considered hung. This is NOT a total duration cap — a call
# can run for 20 minutes as long as tokens keep flowing. Only true silence
# (zero bytes) triggers cancellation.
_STREAM_SILENCE_TIMEOUT = 120.0

# Ultimate safety net. Even with streaming, we cap at 20 minutes to
# prevent runaway calls. This should almost never trigger if silence
# detection works, but it protects against edge cases like a model that
# sends a keepalive byte every 119 seconds forever.
_LLM_WALLCLOCK_TIMEOUT = 1200.0

# Exact-match response cache for short, deterministic internal LLM calls
# (e.g. classify_service, humanize). Key = "model:content", TTL = 5 min.
_response_cache: collections.OrderedDict[str, tuple[str, float]] = collections.OrderedDict()
_CACHE_TTL = 300.0
_CACHE_MAX_INPUT_LEN = 200


def _cache_key(
    messages: list[dict[str, Any]],
    model: str,
    system_prompt: str | None = None,
) -> str | None:
    """Build a cache key for deterministic single-turn calls only.

    Includes the system_prompt hash to prevent cross-contamination between
    callers that share a model but use different system prompts.
    """
    if len(messages) == 1 and messages[0].get("role") == "user":
        content = messages[0].get("content", "")
        if isinstance(content, str) and len(content) < _CACHE_MAX_INPUT_LEN:
            sp_hash = hash(system_prompt) if system_prompt else 0
            return f"{model}:{sp_hash}:{content}"
    return None


def _cache_get(key: str) -> str | None:
    """Retrieve a cached response if still valid (LRU: moves to end)."""
    entry = _response_cache.get(key)
    if entry is None:
        return None
    text, ts = entry
    if time.monotonic() - ts > _CACHE_TTL:
        _response_cache.pop(key, None)
        return None
    _response_cache.move_to_end(key)
    return text


def _cache_put(key: str, text: str) -> None:
    """Store a response in the cache (LRU eviction via OrderedDict)."""
    _response_cache[key] = (text, time.monotonic())
    _response_cache.move_to_end(key)
    while len(_response_cache) > 500:
        _response_cache.popitem(last=False)


def _is_retryable_llm_error(exc: BaseException) -> bool:
    if isinstance(exc, OpenClawError) and exc.status_code in _RETRYABLE_STATUS_CODES:
        return True
    if isinstance(exc, httpx.ReadTimeout | httpx.ConnectTimeout | httpx.PoolTimeout):
        return True
    return False


@dataclass
class ChatConfig:
    """Configuration for a chat completion."""

    model: str = ""
    temperature: float = 0.7
    max_tokens: int = 16_384
    system_prompt: str | None = None
    tools: list[dict[str, Any]] | None = None
    stream: bool = False
    wallclock_timeout: float = 1200.0
    rate_limit_timeout: float = 30.0


@dataclass
class OpenClawResponse:
    """Response from chat completion."""

    content: str
    tool_calls: list[dict[str, Any]] | None = None
    usage: dict[str, int] | None = None
    engrams_added: list[dict[str, Any]] | None = None
    error: str | None = None


class OpenClawError(Exception):
    """LLM API error."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class OpenClawClient:
    """HTTP client for LLM via OpenRouter.

    Named OpenClawClient for backward compatibility with imports.
    All requests route through OpenRouter for reliable tool calling.
    """

    def __init__(self) -> None:
        api_key = settings.openrouter_api_key
        if not api_key:
            raise RuntimeError(
                "LUCY_OPENROUTER_API_KEY not set. "
                "Add openrouter_api_key to keys.json under openclaw_lucy."
            )

        self._client = httpx.AsyncClient(
            base_url=settings.openrouter_base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "X-Title": "Lucy AI Agent",
            },
            verify=certifi.where(),
            timeout=httpx.Timeout(
                connect=5.0,
                read=settings.openclaw_read_timeout,
                write=5.0,
                pool=15.0,
            ),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

        logger.info(
            "llm_client_initialized",
            base_url=settings.openrouter_base_url,
            model=settings.openclaw_model,
        )

    async def close(self) -> None:
        await self._client.aclose()
        logger.info("llm_client_closed")

    async def health_check(self) -> dict[str, Any]:
        """Quick model check via OpenRouter."""
        try:
            resp = await self._client.post(
                "/chat/completions",
                json={
                    "model": settings.openclaw_model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 3,
                },
            )
            resp.raise_for_status()
            return {"status": "ok", "model": settings.openclaw_model}
        except Exception as e:
            raise OpenClawError(f"Health check error: {e}")

    async def chat_completion(
        self,
        messages: list[dict[str, Any]],
        config: ChatConfig | None = None,
        workspace_id: str | None = None,
        user_id: str | None = None,
    ) -> OpenClawResponse:
        config = config or ChatConfig()
        model = config.model or settings.openclaw_model

        cache_key = (
            _cache_key(messages, model, config.system_prompt)
            if not config.tools
            else None
        )
        if cache_key:
            cached = _cache_get(cache_key)
            if cached is not None:
                logger.debug("internal_cache_hit", model=model)
                return OpenClawResponse(content=cached)

        final_messages: list[dict[str, Any]] = []
        if config.system_prompt:
            final_messages.append(
                {"role": "system", "content": config.system_prompt}
            )
        else:
            soul = load_soul()
            if soul:
                final_messages.append({"role": "system", "content": soul})

        final_messages.extend(messages)

        payload: dict[str, Any] = {
            "model": model,
            "messages": final_messages,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
        }

        if config.tools:
            payload["tools"] = config.tools
            payload["tool_choice"] = "auto"

        logger.info(
            "chat_completion_request",
            model=model,
            message_count=len(final_messages),
            has_tools=bool(config.tools),
            tool_count=len(config.tools) if config.tools else 0,
            streaming=config.stream,
        )

        if config.stream:
            return await self._stream_completion(
                payload, model,
                rate_limit_timeout=config.rate_limit_timeout,
            )

        return await self._non_stream_completion(
            payload, model, cache_key,
            wallclock_timeout=config.wallclock_timeout,
            rate_limit_timeout=config.rate_limit_timeout,
        )

    async def _non_stream_completion(
        self,
        payload: dict[str, Any],
        model: str,
        cache_key: str | None,
        wallclock_timeout: float = 1200.0,
        rate_limit_timeout: float = 30.0,
    ) -> OpenClawResponse:
        """Non-streaming path for cheap internal calls (planner, supervisor)."""
        if not openrouter_breaker.should_allow_request():
            raise OpenClawError(
                "OpenRouter circuit breaker is open — service appears down."
                " Retrying shortly.",
                status_code=503,
            )

        @retry(
            retry=retry_if_exception(_is_retryable_llm_error),
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=1, max=4),
            reraise=True,
        )
        async def _do_request() -> OpenClawResponse:
            from lucy.infra.rate_limiter import get_rate_limiter
            limiter = get_rate_limiter()
            acquired = await limiter.acquire_model(
                model, timeout=rate_limit_timeout,
            )
            if not acquired:
                raise OpenClawError(
                    f"Rate limited for model {model}. Try again shortly.",
                    status_code=429,
                )

            try:
                t0 = time.monotonic()
                response = await self._client.post(
                    "/chat/completions", json=payload,
                )
                llm_ms = round((time.monotonic() - t0) * 1000)
                response.raise_for_status()
                data = response.json()

                choices = data.get("choices", [])
                content = ""
                tool_calls = None

                if choices:
                    message = choices[0].get("message", {})
                    content = message.get("content") or ""
                    raw_tool_calls = message.get("tool_calls")
                    if raw_tool_calls:
                        tool_calls = self._parse_tool_calls(raw_tool_calls)

                result = OpenClawResponse(
                    content=content,
                    tool_calls=tool_calls,
                    usage=data.get("usage"),
                )

                cached_tokens = 0
                if result.usage:
                    details = result.usage.get("prompt_tokens_details") or {}
                    cached_tokens = details.get("cached_tokens", 0)

                logger.info(
                    "chat_completion_success",
                    model=model,
                    llm_ms=llm_ms,
                    content_length=len(result.content),
                    has_tool_calls=bool(result.tool_calls),
                    tool_call_count=(
                        len(result.tool_calls) if result.tool_calls else 0
                    ),
                    prompt_tokens=result.usage.get("prompt_tokens") if result.usage else None,
                    completion_tokens=result.usage.get("completion_tokens") if result.usage else None,  # noqa: E501
                    cached_tokens=cached_tokens,
                )
                openrouter_breaker.record_success()
                return result

            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                logger.error(
                    "chat_completion_failed",
                    status_code=status,
                    response=e.response.text[:500],
                )
                openrouter_breaker.record_failure()
                raise OpenClawError(
                    f"Chat completion failed: {status}",
                    status_code=status,
                )
            except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.PoolTimeout) as e:
                logger.warning("chat_completion_timeout", error=str(e))
                openrouter_breaker.record_failure()
                raise
            except OpenClawError:
                openrouter_breaker.record_failure()
                raise
            except Exception as e:
                logger.error("chat_completion_error", error=str(e))
                openrouter_breaker.record_failure()
                raise OpenClawError(f"Chat completion error: {e}")

        try:
            result = await asyncio.wait_for(
                _do_request(), timeout=wallclock_timeout,
            )
        except TimeoutError:
            logger.error(
                "llm_wallclock_timeout",
                model=model,
                timeout_s=wallclock_timeout,
            )
            openrouter_breaker.record_failure()
            raise OpenClawError(
                f"LLM call to {model} exceeded wallclock limit",
                status_code=504,
            )

        if cache_key and result.content and not result.tool_calls:
            _cache_put(cache_key, result.content)

        return result

    async def _stream_completion(
        self,
        payload: dict[str, Any],
        model: str,
        rate_limit_timeout: float = 30.0,
    ) -> OpenClawResponse:
        """Streaming path with silence detection.

        Uses SSE streaming so we can distinguish "model is actively
        generating tokens" from "model is hung/silent." Duration is
        irrelevant — only silence triggers cancellation.

        A call can run for 20 minutes if tokens keep flowing. But if
        zero data arrives for _STREAM_SILENCE_TIMEOUT seconds, we
        cancel and raise 504 for the caller to escalate models.
        """
        if not openrouter_breaker.should_allow_request():
            raise OpenClawError(
                "OpenRouter circuit breaker is open — service appears down."
                " Retrying shortly.",
                status_code=503,
            )

        from lucy.infra.rate_limiter import get_rate_limiter
        limiter = get_rate_limiter()
        acquired = await limiter.acquire_model(
            model, timeout=rate_limit_timeout,
        )
        if not acquired:
            raise OpenClawError(
                f"Rate limited for model {model}. Try again shortly.",
                status_code=429,
            )

        _STREAM_RETRYABLE = frozenset({429, 502, 503, 504})
        _MAX_STREAM_ATTEMPTS = 2
        stream_payload = {**payload, "stream": True}
        t0 = time.monotonic()
        last_activity = time.monotonic()
        content_parts: list[str] = []
        tool_call_deltas: dict[int, dict[str, Any]] = {}
        usage_data: dict[str, int] | None = None
        chunk_count = 0
        last_error: Exception | None = None

        for attempt in range(_MAX_STREAM_ATTEMPTS):
            content_parts.clear()
            tool_call_deltas.clear()
            usage_data = None
            chunk_count = 0
            last_error = None

            try:
                async with self._client.stream(
                    "POST",
                    "/chat/completions",
                    json=stream_payload,
                    timeout=httpx.Timeout(
                        connect=5.0,
                        read=_STREAM_SILENCE_TIMEOUT,
                        write=5.0,
                        pool=15.0,
                    ),
                ) as response:
                    response.raise_for_status()

                    async for raw_line in response.aiter_lines():
                        now = time.monotonic()

                        if now - t0 > _LLM_WALLCLOCK_TIMEOUT:
                            logger.error(
                                "stream_wallclock_exceeded",
                                model=model,
                                elapsed_s=round(now - t0),
                                chunks=chunk_count,
                            )
                            raise OpenClawError(
                                f"Streaming call exceeded"
                                f" {int(_LLM_WALLCLOCK_TIMEOUT)}s",
                                status_code=504,
                            )

                        last_activity = now

                        line = raw_line.strip()
                        if not line or line.startswith(":"):
                            continue
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break

                        try:
                            chunk = _json.loads(data_str)
                        except _json.JSONDecodeError:
                            continue

                        chunk_count += 1

                        if "usage" in chunk:
                            usage_data = chunk["usage"]

                        choices = chunk.get("choices", [])
                        if not choices:
                            continue

                        delta = choices[0].get("delta", {})

                        if delta.get("content"):
                            content_parts.append(delta["content"])

                        if "tool_calls" in delta:
                            for tc_delta in delta["tool_calls"]:
                                idx = tc_delta.get("index", 0)
                                if idx not in tool_call_deltas:
                                    tool_call_deltas[idx] = {
                                        "id": tc_delta.get("id", ""),
                                        "function": {
                                            "name": "",
                                            "arguments": "",
                                        },
                                    }
                                entry = tool_call_deltas[idx]
                                if tc_delta.get("id"):
                                    entry["id"] = tc_delta["id"]
                                fn = tc_delta.get("function", {})
                                if fn.get("name"):
                                    entry["function"]["name"] += fn["name"]
                                if fn.get("arguments"):
                                    entry["function"]["arguments"] += (
                                        fn["arguments"]
                                    )

                break  # success — exit retry loop

            except httpx.ReadTimeout:
                elapsed = time.monotonic() - t0
                silence = time.monotonic() - last_activity
                last_error = OpenClawError(
                    f"Model {model} went silent for"
                    f" {int(silence)}s during streaming",
                    status_code=504,
                )
                if attempt < _MAX_STREAM_ATTEMPTS - 1:
                    logger.warning(
                        "stream_silence_retrying",
                        model=model,
                        attempt=attempt + 1,
                        silence_s=round(silence),
                    )
                    await asyncio.sleep(1.0)
                    continue
                logger.warning(
                    "stream_silence_detected",
                    model=model,
                    elapsed_s=round(elapsed),
                    silence_s=round(silence),
                    chunks_received=chunk_count,
                    content_so_far=len("".join(content_parts)),
                )
                openrouter_breaker.record_failure()
                raise last_error
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                last_error = OpenClawError(
                    f"Streaming failed: {status}",
                    status_code=status,
                )
                if (
                    status in _STREAM_RETRYABLE
                    and attempt < _MAX_STREAM_ATTEMPTS - 1
                ):
                    logger.warning(
                        "stream_http_retrying",
                        model=model,
                        attempt=attempt + 1,
                        status_code=status,
                    )
                    await asyncio.sleep(1.0)
                    continue
                logger.error(
                    "stream_http_error",
                    status_code=status,
                    response=e.response.text[:500],
                )
                openrouter_breaker.record_failure()
                raise last_error
            except OpenClawError:
                openrouter_breaker.record_failure()
                raise
            except Exception as e:
                logger.error("stream_error", error=str(e))
                openrouter_breaker.record_failure()
                raise OpenClawError(f"Streaming error: {e}")

        llm_ms = round((time.monotonic() - t0) * 1000)
        content = "".join(content_parts)

        tool_calls = None
        if tool_call_deltas:
            raw_calls = [
                tool_call_deltas[i]
                for i in sorted(tool_call_deltas.keys())
            ]
            tool_calls = self._parse_tool_calls(raw_calls)

        cached_tokens = 0
        if usage_data:
            details = usage_data.get("prompt_tokens_details") or {}
            cached_tokens = details.get("cached_tokens", 0)

        logger.info(
            "chat_completion_success",
            model=model,
            llm_ms=llm_ms,
            content_length=len(content),
            has_tool_calls=bool(tool_calls),
            tool_call_count=len(tool_calls) if tool_calls else 0,
            prompt_tokens=usage_data.get("prompt_tokens") if usage_data else None,
            completion_tokens=usage_data.get("completion_tokens") if usage_data else None,
            cached_tokens=cached_tokens,
            stream_chunks=chunk_count,
        )

        openrouter_breaker.record_success()

        return OpenClawResponse(
            content=content,
            tool_calls=tool_calls,
            usage=usage_data,
        )

    @staticmethod
    def _parse_tool_calls(
        raw_tool_calls: list[dict[str, Any]],
    ) -> list[dict[str, Any]] | None:
        """Parse OpenAI-format tool calls into our internal format."""
        parsed = []
        for tc in raw_tool_calls:
            fn = tc.get("function")
            if not fn or not isinstance(fn, dict):
                continue
            fn_name = fn.get("name")
            if not fn_name:
                continue

            args = fn.get("arguments", "{}")
            parse_error = None
            if isinstance(args, str):
                try:
                    args = _json.loads(args)
                except _json.JSONDecodeError:
                    parse_error = "invalid_json_arguments"
                    args = {}
            elif not isinstance(args, dict):
                parse_error = "unexpected_arguments_type"
                args = {}

            parsed.append({
                "id": tc.get("id"),
                "name": fn_name,
                "parameters": args,
                "parse_error": parse_error,
            })
        return parsed or None

    def _load_soul(self) -> str:
        return load_soul()


def load_soul() -> str:
    """Load SOUL.md system prompt.

    Delegates to the canonical loader in pipeline.prompt so there is
    a single loading path for all SOUL variants.
    """
    try:
        from lucy.pipeline.prompt import _load_soul
        return _load_soul()
    except Exception as e:
        logger.error("soul_load_failed", error=str(e))
    return (
        "You are Lucy, an AI coworker. You are direct, helpful, "
        "and get things done. You work inside Slack and have access "
        "to various tools and integrations."
    )


_client: OpenClawClient | None = None
_client_lock = asyncio.Lock()


async def get_openclaw_client() -> OpenClawClient:
    global _client
    if _client is not None:
        return _client
    async with _client_lock:
        if _client is None:
            _client = OpenClawClient()
    return _client


async def close_openclaw_client() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None
