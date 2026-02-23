"""Request-scoped tracing for Lucy.

Every incoming Slack event gets a unique trace_id. Each significant step
(prompt build, LLM call, tool execution, Slack post) is recorded as a
Span with start/end timestamps and metadata.  At the end of the request
the full trace is emitted as a structured log event and written to a
per-thread JSONL file for later analysis.

Usage::

    from lucy.core.trace import Trace

    trace = Trace.start("abc123")
    async with trace.span("llm_call", model="minimax"):
        response = await client.chat(...)
    trace.finish(user_message=msg, response=resp)
"""

from __future__ import annotations

import json
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

import structlog

logger = structlog.get_logger()

_current_trace: ContextVar[Trace | None] = ContextVar("_current_trace", default=None)


@dataclass
class Span:
    name: str
    start_ms: float = 0.0
    end_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        if self.end_ms and self.start_ms:
            return round(self.end_ms - self.start_ms, 1)
        return 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "duration_ms": self.duration_ms,
            **self.metadata,
        }


class SpanContext:
    """Async context manager for timing a span."""

    def __init__(self, span: Span, origin: float) -> None:
        self._span = span
        self._origin = origin

    async def __aenter__(self) -> Span:
        self._span.start_ms = round((time.monotonic() - self._origin) * 1000, 1)
        return self._span

    async def __aexit__(self, *exc: Any) -> None:
        self._span.end_ms = round((time.monotonic() - self._origin) * 1000, 1)


class Trace:
    """Lightweight per-request trace collector."""

    def __init__(self, trace_id: str) -> None:
        self.trace_id = trace_id
        self.spans: list[Span] = []
        self._origin = time.monotonic()
        self._start_epoch = time.time()

        self.model_used: str = ""
        self.intent: str = ""
        self.tool_calls_made: list[str] = []
        self.user_message: str = ""
        self.response_text: str = ""
        self.usage: dict[str, int] = {}

    @classmethod
    def start(cls, trace_id: str | None = None) -> Trace:
        tid = trace_id or uuid.uuid4().hex[:12]
        trace = cls(tid)
        _current_trace.set(trace)
        structlog.contextvars.bind_contextvars(trace_id=tid)
        return trace

    @classmethod
    def current(cls) -> Trace | None:
        return _current_trace.get()

    def span(self, name: str, **metadata: Any) -> SpanContext:
        s = Span(name=name, metadata=metadata)
        self.spans.append(s)
        return SpanContext(s, self._origin)

    @property
    def total_ms(self) -> float:
        return round((time.monotonic() - self._origin) * 1000, 1)

    def finish(
        self,
        user_message: str = "",
        response_text: str = "",
    ) -> dict[str, Any]:
        self.user_message = user_message
        self.response_text = response_text

        record = self._to_dict()

        logger.info(
            "request_trace",
            trace_id=self.trace_id,
            total_ms=self.total_ms,
            model=self.model_used,
            intent=self.intent,
            tool_count=len(self.tool_calls_made),
            span_count=len(self.spans),
        )

        structlog.contextvars.unbind_contextvars("trace_id")
        _current_trace.set(None)
        return record

    def _to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "timestamp": self._start_epoch,
            "total_ms": self.total_ms,
            "model_used": self.model_used,
            "intent": self.intent,
            "tool_calls_made": self.tool_calls_made,
            "user_message": self.user_message[:500],
            "response_text": self.response_text[:500],
            "usage": self.usage,
            "spans": [s.to_dict() for s in self.spans],
        }

    async def write_to_thread_log(
        self,
        workspace_root: Path,
        workspace_id: str,
        thread_ts: str | None,
    ) -> None:
        """Append this trace as a JSONL line to the thread log file."""
        if not thread_ts:
            return
        log_dir = workspace_root / workspace_id / "logs" / "threads"
        log_dir.mkdir(parents=True, exist_ok=True)

        safe_ts = thread_ts.replace(".", "_")
        log_path = log_dir / f"{safe_ts}.jsonl"

        record = self._to_dict()
        line = json.dumps(record, default=str, ensure_ascii=False)
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception as e:
            logger.warning("thread_log_write_failed", error=str(e))
