"""LLM client for Lucy.

Routes all requests through OpenRouter (openrouter.ai/api/v1).
OpenClaw stripped tool parameters, so we bypass it entirely.
OpenRouter provides OpenAI-compatible tool calling across 224+ models.

Primary model: minimax/minimax-m2.5
- Native interleaved thinking between tool calls
- #1 on OpenRouter for programming/technology
- $0.30/$1.10 per M tokens, 197K context
"""

from __future__ import annotations

import json as _json
import time
from dataclasses import dataclass, field
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

logger = structlog.get_logger()

_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

# Exact-match response cache for short, deterministic internal LLM calls
# (e.g. classify_service, humanize). Key = "model:content", TTL = 5 min.
_response_cache: dict[str, tuple[str, float]] = {}
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
    """Retrieve a cached response if still valid."""
    entry = _response_cache.get(key)
    if entry is None:
        return None
    text, ts = entry
    if time.monotonic() - ts > _CACHE_TTL:
        _response_cache.pop(key, None)
        return None
    return text


def _cache_put(key: str, text: str) -> None:
    """Store a response in the cache."""
    _response_cache[key] = (text, time.monotonic())
    # Evict oldest entries if cache grows too large
    if len(_response_cache) > 500:
        oldest = sorted(_response_cache, key=lambda k: _response_cache[k][1])
        for k in oldest[:100]:
            _response_cache.pop(k, None)


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
    max_tokens: int = 4096
    system_prompt: str | None = None
    tools: list[dict[str, Any]] | None = None


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

        # Exact-match cache for short deterministic calls (no tools)
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
        )

        @retry(
            retry=retry_if_exception(_is_retryable_llm_error),
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=1, max=4),
            reraise=True,
        )
        async def _do_request() -> OpenClawResponse:
            # ── Rate limit check before LLM call ─────────────────────
            from lucy.infra.rate_limiter import get_rate_limiter
            limiter = get_rate_limiter()
            acquired = await limiter.acquire_model(model, timeout=30.0)
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
                    completion_tokens=result.usage.get("completion_tokens") if result.usage else None,
                    cached_tokens=cached_tokens,
                )
                return result

            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                logger.error(
                    "chat_completion_failed",
                    status_code=status,
                    response=e.response.text[:500],
                )
                raise OpenClawError(
                    f"Chat completion failed: {status}",
                    status_code=status,
                )
            except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.PoolTimeout) as e:
                logger.warning("chat_completion_timeout", error=str(e))
                raise
            except OpenClawError:
                raise
            except Exception as e:
                logger.error("chat_completion_error", error=str(e))
                raise OpenClawError(f"Chat completion error: {e}")

        result = await _do_request()

        if cache_key and result.content and not result.tool_calls:
            _cache_put(cache_key, result.content)

        return result

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
    """Load SOUL.md system prompt."""
    try:
        import pathlib

        soul_path = (
            pathlib.Path(__file__).parent.parent.parent.parent
            / "prompts"
            / "SOUL.md"
        )
        if soul_path.exists():
            return soul_path.read_text()
    except Exception as e:
        logger.warning("failed_to_load_soul", error=str(e))
    return (
        "You are Lucy, an AI coworker. You are direct, helpful, "
        "and get things done. You work inside Slack and have access "
        "to various tools and integrations."
    )


_client: OpenClawClient | None = None


async def get_openclaw_client() -> OpenClawClient:
    global _client
    if _client is None:
        _client = OpenClawClient()
    return _client


async def close_openclaw_client() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None
