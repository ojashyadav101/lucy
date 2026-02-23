"""Rate limiting layer for Lucy.

Two dimensions of rate limiting:

1. **Per-model limits** — Prevent exhausting OpenRouter/model-specific
   rate limits. Different models have different TPM/RPM quotas.

2. **Per-external-API limits** — Prevent hitting Google Calendar,
   GitHub, Linear, etc. rate limits when multiple users make concurrent
   requests through Composio.

Implementation: Token bucket algorithm.
    - Simple, well-understood, low overhead
    - Refills tokens at a steady rate
    - Allows short bursts (bucket capacity) while enforcing long-term rate
    - Zero external dependencies (no Redis needed for single-process)

Design decisions:
    - In-memory only. Lucy is single-process. No need for distributed
      rate limiting until multi-process deployment.
    - Async-aware. Uses asyncio.Lock for thread safety.
    - Graceful degradation. When rate limited, we wait (with timeout)
      rather than reject. Only reject if wait would exceed user patience.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger()


@dataclass
class TokenBucket:
    """Token bucket rate limiter.

    Args:
        rate: Tokens added per second
        capacity: Maximum tokens in the bucket
    """
    rate: float               # Tokens per second
    capacity: float           # Max tokens
    _tokens: float = field(init=False)
    _last_refill: float = field(init=False)
    _lock: asyncio.Lock = field(init=False, default_factory=asyncio.Lock)

    def __post_init__(self) -> None:
        self._tokens = self.capacity
        self._last_refill = time.monotonic()

    def _refill(self) -> None:
        """Add tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(
            self.capacity,
            self._tokens + elapsed * self.rate,
        )
        self._last_refill = now

    async def acquire(self, tokens: float = 1.0, timeout: float = 30.0) -> bool:
        """Try to acquire tokens. Returns True if acquired, False if timed out.

        If not enough tokens, waits until they're available (up to timeout).
        """
        deadline = time.monotonic() + timeout

        async with self._lock:
            while True:
                self._refill()

                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return True

                # Calculate wait time
                needed = tokens - self._tokens
                wait_time = needed / self.rate

                # Check if we'd exceed timeout
                remaining = deadline - time.monotonic()
                if remaining <= 0 or wait_time > remaining:
                    return False

                # Release lock while waiting, then re-acquire
                # (we can't hold the lock during sleep)
                break

        # Wait outside the lock
        await asyncio.sleep(min(wait_time, remaining))

        # Re-acquire lock and try again (recursive)
        return await self.acquire(tokens, timeout=max(0, deadline - time.monotonic()))

    @property
    def available_tokens(self) -> float:
        """Current available tokens (approximate, no lock)."""
        elapsed = time.monotonic() - self._last_refill
        return min(self.capacity, self._tokens + elapsed * self.rate)


# ═══════════════════════════════════════════════════════════════════════════
# MODEL RATE LIMITS
# ═══════════════════════════════════════════════════════════════════════════
# These are conservative defaults based on typical OpenRouter quotas.
# Actual limits depend on your OpenRouter plan.

_MODEL_LIMITS: dict[str, tuple[float, float]] = {
    # model_prefix: (requests_per_second, burst_capacity)
    "google/": (5.0, 15),          # Gemini: generous
    "anthropic/": (2.0, 8),        # Claude: more restrictive
    "deepseek/": (3.0, 10),        # DeepSeek: moderate
    "minimax/": (3.0, 10),         # MiniMax: moderate
    "openai/": (3.0, 10),          # OpenAI: moderate
    "_default": (2.0, 8),          # Fallback
}


def _get_model_limit(model: str) -> tuple[float, float]:
    """Get rate limit config for a model."""
    for prefix, limits in _MODEL_LIMITS.items():
        if prefix != "_default" and model.startswith(prefix):
            return limits
    return _MODEL_LIMITS["_default"]


# ═══════════════════════════════════════════════════════════════════════════
# EXTERNAL API RATE LIMITS
# ═══════════════════════════════════════════════════════════════════════════
# Conservative limits for external services accessed through Composio.

_API_LIMITS: dict[str, tuple[float, float]] = {
    # api_name: (requests_per_second, burst_capacity)
    "google_calendar": (2.0, 5),
    "google_sheets": (2.0, 5),
    "google_drive": (2.0, 5),
    "gmail": (2.0, 5),
    "github": (5.0, 15),           # GitHub is generous
    "linear": (3.0, 10),
    "slack": (3.0, 10),
    "clickup": (2.0, 5),
    "_default": (2.0, 5),
}


# ═══════════════════════════════════════════════════════════════════════════
# RATE LIMITER MANAGER
# ═══════════════════════════════════════════════════════════════════════════

class RateLimiter:
    """Manages rate limiters for models and external APIs.

    Usage:
        limiter = get_rate_limiter()

        # Before LLM call:
        if await limiter.acquire_model("anthropic/claude-sonnet-4"):
            response = await llm_call(...)
        else:
            # Rate limited — back off or use different model

        # Before external API call:
        if await limiter.acquire_api("google_calendar"):
            result = await calendar_api(...)
    """

    def __init__(self) -> None:
        self._model_buckets: dict[str, TokenBucket] = {}
        self._api_buckets: dict[str, TokenBucket] = {}

    def _get_model_bucket(self, model: str) -> TokenBucket:
        """Get or create a token bucket for a model."""
        if model not in self._model_buckets:
            rate, capacity = _get_model_limit(model)
            self._model_buckets[model] = TokenBucket(
                rate=rate,
                capacity=capacity,
            )
        return self._model_buckets[model]

    def _get_api_bucket(self, api_name: str) -> TokenBucket:
        """Get or create a token bucket for an external API."""
        if api_name not in self._api_buckets:
            limits = _API_LIMITS.get(api_name, _API_LIMITS["_default"])
            self._api_buckets[api_name] = TokenBucket(
                rate=limits[0],
                capacity=limits[1],
            )
        return self._api_buckets[api_name]

    async def acquire_model(
        self,
        model: str,
        timeout: float = 30.0,
    ) -> bool:
        """Acquire rate limit token for an LLM call.

        Returns True if acquired, False if rate limited (timed out).
        """
        bucket = self._get_model_bucket(model)
        acquired = await bucket.acquire(timeout=timeout)

        if not acquired:
            logger.warning(
                "model_rate_limited",
                model=model,
                available_tokens=bucket.available_tokens,
            )

        return acquired

    async def acquire_api(
        self,
        api_name: str,
        timeout: float = 15.0,
    ) -> bool:
        """Acquire rate limit token for an external API call.

        Returns True if acquired, False if rate limited.
        """
        bucket = self._get_api_bucket(api_name)
        acquired = await bucket.acquire(timeout=timeout)

        if not acquired:
            logger.warning(
                "api_rate_limited",
                api_name=api_name,
                available_tokens=bucket.available_tokens,
            )

        return acquired

    def classify_api_from_tool(self, tool_name: str, params: dict[str, Any]) -> str | None:
        """Infer which external API a tool call targets.

        Uses the tool name and parameters to determine which rate limiter
        to apply. Returns None if no specific API is identified.
        """
        name_lower = tool_name.lower()
        actions = params.get("actions", [])

        # Check Composio multi-execute actions
        for action in actions:
            action_name = ""
            if isinstance(action, str):
                action_name = action.lower()
            elif isinstance(action, dict):
                action_name = (
                    action.get("action", "") or action.get("tool", "")
                ).lower()

            if not action_name:
                continue

            # Map action prefixes to APIs
            if "googlecalendar" in action_name or "google_calendar" in action_name or "gcal" in action_name:
                return "google_calendar"
            if "googlesheets" in action_name or "google_sheets" in action_name or "gsheet" in action_name:
                return "google_sheets"
            if "googledrive" in action_name or "google_drive" in action_name or "gdrive" in action_name:
                return "google_drive"
            if "gmail" in action_name:
                return "gmail"
            if "github" in action_name:
                return "github"
            if "linear" in action_name:
                return "linear"
            if "slack" in action_name:
                return "slack"
            if "clickup" in action_name:
                return "clickup"

        return None

    @property
    def metrics(self) -> dict[str, Any]:
        """Return current rate limiter state for observability."""
        return {
            "models": {
                model: round(bucket.available_tokens, 1)
                for model, bucket in self._model_buckets.items()
            },
            "apis": {
                api: round(bucket.available_tokens, 1)
                for api, bucket in self._api_buckets.items()
            },
        }


# ═══════════════════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════════════════

_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    """Get or create the singleton rate limiter."""
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter()
    return _limiter
