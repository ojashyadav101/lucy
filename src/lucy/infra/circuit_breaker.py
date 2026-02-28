"""Circuit breaker for external service calls.

Three states:
    CLOSED     — calls pass through normally.
    OPEN       — calls fast-fail without hitting the service.
    HALF_OPEN  — one probe call is allowed to test recovery.

Transitions:
    CLOSED  → OPEN       when consecutive failures >= failure_threshold.
    OPEN    → HALF_OPEN  after cooldown_seconds elapse.
    HALF_OPEN → CLOSED   on first success.
    HALF_OPEN → OPEN     on another failure (timer resets).
"""

from __future__ import annotations

import time
from types import TracebackType
from typing import Self

import structlog

logger = structlog.get_logger()


class CircuitBreaker:
    """Per-service circuit breaker with async context manager support."""

    __slots__ = (
        "name",
        "failure_threshold",
        "cooldown_seconds",
        "_failure_count",
        "_last_failure_time",
        "_probe_in_flight",
    )

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        cooldown_seconds: float = 60.0,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._failure_count: int = 0
        self._last_failure_time: float = 0.0
        self._probe_in_flight: bool = False

    @property
    def is_open(self) -> bool:
        """True when failures >= threshold and cooldown has NOT elapsed."""
        if self._failure_count < self.failure_threshold:
            return False
        return (time.monotonic() - self._last_failure_time) < self.cooldown_seconds

    @property
    def is_half_open(self) -> bool:
        """True when failures >= threshold but cooldown HAS elapsed."""
        if self._failure_count < self.failure_threshold:
            return False
        return (time.monotonic() - self._last_failure_time) >= self.cooldown_seconds

    def record_success(self) -> None:
        self._probe_in_flight = False
        if self._failure_count > 0:
            logger.info(
                "circuit_breaker_closed",
                breaker=self.name,
                previous_failures=self._failure_count,
            )
        self._failure_count = 0

    def record_failure(self) -> None:
        self._probe_in_flight = False
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        if self._failure_count == self.failure_threshold:
            logger.warning(
                "circuit_breaker_opened",
                breaker=self.name,
                failures=self._failure_count,
                cooldown_s=self.cooldown_seconds,
            )

    def should_allow_request(self) -> bool:
        """Return True if the circuit is CLOSED or HALF_OPEN (probe).

        In HALF_OPEN state only one concurrent probe is allowed to
        prevent a stampede of requests hitting a recovering service.
        """
        if self._failure_count < self.failure_threshold:
            return True
        if self.is_half_open and not self._probe_in_flight:
            self._probe_in_flight = True
            logger.info("circuit_breaker_half_open_probe", breaker=self.name)
            return True
        return False

    async def __aenter__(self) -> Self:
        if not self.should_allow_request():
            raise CircuitOpenError(self.name)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if exc_type is None:
            self.record_success()
        else:
            self.record_failure()


class CircuitOpenError(Exception):
    """Raised when a call is attempted on an open circuit."""

    def __init__(self, breaker_name: str) -> None:
        super().__init__(
            f"Circuit breaker '{breaker_name}' is open — service appears down"
        )
        self.breaker_name = breaker_name


_BREAKER_CONFIGS: dict[str, tuple[int, float]] = {
    # name: (failure_threshold, cooldown_seconds)
    "openrouter": (5, 60.0),
    "composio": (5, 60.0),
    "vercel": (3, 30.0),
    "convex": (3, 30.0),
}

openrouter_breaker = CircuitBreaker("openrouter", *_BREAKER_CONFIGS["openrouter"])
composio_breaker = CircuitBreaker("composio", *_BREAKER_CONFIGS["composio"])
vercel_breaker = CircuitBreaker("vercel", *_BREAKER_CONFIGS["vercel"])
convex_breaker = CircuitBreaker("convex", *_BREAKER_CONFIGS["convex"])
