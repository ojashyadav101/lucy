"""Circuit breaker for Lucy's external API calls.

Prevents cascade failures when Composio, OpenClaw, or any downstream service
is degraded. Implements the standard three-state machine:

  CLOSED ──(failure_threshold exceeded)──► OPEN
  OPEN   ──(recovery_timeout elapsed)───► HALF_OPEN
  HALF_OPEN ──(call succeeds)────────────► CLOSED
  HALF_OPEN ──(call fails)───────────────► OPEN

Usage
-----
    cb = get_circuit_breaker("composio_api")

    try:
        result = await cb.call(my_async_fn, arg1, arg2)
    except CircuitBreakerOpen:
        # Return a graceful fallback immediately
        result = fallback_value

Each named breaker is a singleton so state is shared across all callers for
the same service, giving system-wide protection rather than per-request.

Configuration
-------------
All breakers are configured through the BREAKER_CONFIGS dict. Defaults apply
to any name not explicitly listed.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Coroutine

import structlog

logger = structlog.get_logger()


# ─────────────────────────────────────────────────────────────────────────────
# State and exceptions
# ─────────────────────────────────────────────────────────────────────────────

class BreakerState(Enum):
    CLOSED = auto()     # Normal — calls pass through
    OPEN = auto()       # Tripped — calls are blocked
    HALF_OPEN = auto()  # Recovery probe — limited calls allowed


class CircuitBreakerOpen(Exception):
    """Raised when a call is attempted while the breaker is OPEN."""

    def __init__(self, name: str, retry_after: float) -> None:
        self.name = name
        self.retry_after = retry_after  # seconds until next probe
        super().__init__(
            f"Circuit breaker '{name}' is OPEN. "
            f"Retry after {retry_after:.1f}s."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Per-service configuration
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class BreakerConfig:
    """Tuning parameters for one circuit breaker."""

    # Number of consecutive failures before tripping to OPEN
    failure_threshold: int = 5

    # Seconds to wait in OPEN state before transitioning to HALF_OPEN
    recovery_timeout: float = 30.0

    # Maximum concurrent calls allowed in HALF_OPEN state (probe window)
    half_open_max_calls: int = 2

    # Minimum number of calls in a window before computing failure rate
    # (avoids tripping on the very first cold-start error)
    minimum_calls: int = 3


# Named configurations — anything not listed uses BreakerConfig defaults.
BREAKER_CONFIGS: dict[str, BreakerConfig] = {
    "composio_api": BreakerConfig(
        failure_threshold=4,
        recovery_timeout=20.0,
        half_open_max_calls=1,
        minimum_calls=2,
    ),
    "openclaw_api": BreakerConfig(
        failure_threshold=5,
        recovery_timeout=30.0,
        half_open_max_calls=2,
        minimum_calls=3,
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
# Circuit breaker
# ─────────────────────────────────────────────────────────────────────────────

class CircuitBreaker:
    """Async circuit breaker for a single named service."""

    def __init__(self, name: str, config: BreakerConfig) -> None:
        self.name = name
        self.config = config

        self._state = BreakerState.CLOSED
        self._failure_count: int = 0
        self._call_count: int = 0          # total calls (for minimum_calls gate)
        self._success_count: int = 0       # successes in HALF_OPEN
        self._half_open_calls: int = 0     # concurrent probes in HALF_OPEN
        self._opened_at: float = 0.0       # monotonic time when last tripped
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def state(self) -> BreakerState:
        return self._state

    @property
    def failure_count(self) -> int:
        return self._failure_count

    async def call(
        self,
        fn: Callable[..., Coroutine[Any, Any, Any]],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Execute *fn* through the circuit breaker.

        Args:
            fn: Async callable to execute.
            *args / **kwargs: Forwarded to fn.

        Returns:
            The return value of fn.

        Raises:
            CircuitBreakerOpen: If the breaker is currently OPEN.
            Exception: Any exception raised by fn (re-raised after recording failure).
        """
        await self._before_call()
        try:
            result = await fn(*args, **kwargs)
            await self._on_success()
            return result
        except CircuitBreakerOpen:
            raise
        except Exception as exc:
            await self._on_failure(exc)
            raise

    def is_open(self) -> bool:
        """True if the breaker is currently blocking calls (OPEN state)."""
        if self._state != BreakerState.OPEN:
            return False
        elapsed = time.monotonic() - self._opened_at
        return elapsed < self.config.recovery_timeout

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------

    async def _before_call(self) -> None:
        async with self._lock:
            if self._state == BreakerState.OPEN:
                elapsed = time.monotonic() - self._opened_at
                if elapsed >= self.config.recovery_timeout:
                    self._transition_to_half_open()
                else:
                    retry_after = self.config.recovery_timeout - elapsed
                    raise CircuitBreakerOpen(self.name, retry_after)

            if self._state == BreakerState.HALF_OPEN:
                if self._half_open_calls >= self.config.half_open_max_calls:
                    raise CircuitBreakerOpen(self.name, 0.0)
                self._half_open_calls += 1

            self._call_count += 1

    async def _on_success(self) -> None:
        async with self._lock:
            if self._state == BreakerState.HALF_OPEN:
                self._success_count += 1
                # One successful probe is enough to close
                self._transition_to_closed()
                return
            # In CLOSED state, reset failure counter on success
            if self._failure_count > 0:
                self._failure_count = 0

    async def _on_failure(self, exc: Exception) -> None:
        async with self._lock:
            self._failure_count += 1
            logger.warning(
                "circuit_breaker_failure",
                name=self.name,
                state=self._state.name,
                failure_count=self._failure_count,
                threshold=self.config.failure_threshold,
                error=str(exc)[:200],
            )
            if self._state == BreakerState.HALF_OPEN:
                # Failed during probe — stay OPEN
                self._transition_to_open()
                return
            if (
                self._call_count >= self.config.minimum_calls
                and self._failure_count >= self.config.failure_threshold
            ):
                self._transition_to_open()

    def _transition_to_open(self) -> None:
        self._state = BreakerState.OPEN
        self._opened_at = time.monotonic()
        self._half_open_calls = 0
        self._success_count = 0
        logger.error(
            "circuit_breaker_opened",
            name=self.name,
            failure_count=self._failure_count,
        )

    def _transition_to_half_open(self) -> None:
        self._state = BreakerState.HALF_OPEN
        self._half_open_calls = 0
        self._success_count = 0
        logger.info("circuit_breaker_half_open", name=self.name)

    def _transition_to_closed(self) -> None:
        self._state = BreakerState.CLOSED
        self._failure_count = 0
        self._call_count = 0
        self._half_open_calls = 0
        logger.info("circuit_breaker_closed", name=self.name)

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        elapsed_open = (
            round(time.monotonic() - self._opened_at, 1)
            if self._state == BreakerState.OPEN
            else None
        )
        return {
            "name": self.name,
            "state": self._state.name,
            "failure_count": self._failure_count,
            "call_count": self._call_count,
            "elapsed_open_s": elapsed_open,
            "config": {
                "failure_threshold": self.config.failure_threshold,
                "recovery_timeout_s": self.config.recovery_timeout,
            },
        }


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────

_breakers: dict[str, CircuitBreaker] = {}
_registry_lock = asyncio.Lock()


def get_circuit_breaker(name: str) -> CircuitBreaker:
    """Return (or lazily create) the named circuit breaker singleton."""
    if name not in _breakers:
        config = BREAKER_CONFIGS.get(name, BreakerConfig())
        _breakers[name] = CircuitBreaker(name=name, config=config)
    return _breakers[name]


def all_breaker_snapshots() -> list[dict]:
    """Snapshot of all registered circuit breakers (for /health/slo)."""
    return [cb.snapshot() for cb in _breakers.values()]
