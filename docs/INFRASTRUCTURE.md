# Infrastructure Layer — Deep Dive

> Rate limiting and request tracing.
> Files: `src/lucy/infra/rate_limiter.py`, `trace.py`

---

## Rate Limiting

**File:** `src/lucy/infra/rate_limiter.py`

Lucy uses in-memory token bucket rate limiting (not the database-backed
`RateLimit` model, which exists for future per-workspace billing limits).

### Token Bucket Algorithm

Each bucket has a `rate` (tokens per second) and `capacity` (burst size).
Tokens refill continuously. When a request arrives:

```
1. Refill tokens based on elapsed time since last refill
2. If tokens >= requested amount:
     Deduct and return immediately
3. Else:
     Wait up to `timeout` seconds for tokens to refill
4. If still insufficient after timeout:
     Return False (request rejected)
```

The `acquire()` method is async-safe with an `asyncio.Lock` to prevent
race conditions.

### Per-Model Rate Limits

| Model Prefix | Rate (req/sec) | Burst |
|-------------|----------------|-------|
| `google/` | 5.0 | 15 |
| `anthropic/` | 2.0 | 8 |
| `deepseek/` | 3.0 | 10 |
| `minimax/` | 3.0 | 10 |
| `openai/` | 3.0 | 10 |
| Default | 2.0 | 8 |

Model prefix matching: `google/gemini-2.5-flash` matches `google/`.

### Per-API Rate Limits

| API Name | Rate (req/sec) | Burst |
|----------|----------------|-------|
| `google_calendar` | 2.0 | 5 |
| `google_sheets` | 2.0 | 5 |
| `google_drive` | 2.0 | 5 |
| `gmail` | 2.0 | 5 |
| `github` | 5.0 | 15 |
| `linear` | 3.0 | 10 |
| `slack` | 3.0 | 10 |
| `clickup` | 2.0 | 5 |
| Default | 2.0 | 5 |

### API Classification

`classify_api_from_tool(tool_name, params)` infers which API a tool call
targets by matching tool name prefixes:

- `GMAIL_*` or `GOOGLEMAIL_*` → `gmail`
- `GOOGLECALENDAR_*` → `google_calendar`
- `GOOGLESHEETS_*` → `google_sheets`
- `GOOGLEDRIVE_*` → `google_drive`
- `GITHUB_*` → `github`
- `LINEAR_*` → `linear`
- `SLACK_*` → `slack`
- `CLICKUP_*` → `clickup`

### Usage in Agent Loop

```
agent.py: _execute_tool()
    │
    ├── Before LLM call:
    │     await rate_limiter.acquire_model(model_name, timeout=30)
    │
    └── Before external tool call:
          api = rate_limiter.classify_api_from_tool(tool_name, params)
          if api:
              await rate_limiter.acquire_api(api, timeout=15)
```

### Singleton Access

`get_rate_limiter()` returns a module-level singleton. Buckets are
created lazily on first access for each model/API.

---

## Request Tracing

**File:** `src/lucy/infra/trace.py`

Per-request trace collection for observability. Each agent run creates
a `Trace` that collects `Span` timing data.

### Trace Lifecycle

```
handlers.py: _handle_message()
    │
    ├── trace = Trace.start()           # Create trace, set in ContextVar
    │
    ├── async with trace.span("route"): # Time intent classification
    │     route = classify_and_route()
    │
    ├── async with trace.span("agent"): # Time agent execution
    │     result = agent.run()
    │
    ├── trace.finish(user_message, response_text)
    │     ├── Logs structured event via structlog
    │     └── Includes: total_ms, model, intent, tool count, token usage
    │
    └── trace.write_to_thread_log(ws_root, ws_id, thread_ts)
          └── Appends JSONL to workspaces/{id}/logs/threads/{thread_ts}.jsonl
```

### Span

```python
@dataclass
class Span:
    name: str           # e.g., "route", "agent", "tool:lucy_web_search"
    start_ms: float     # Monotonic timestamp
    end_ms: float       # Monotonic timestamp
    metadata: dict      # Arbitrary key-value pairs

    @property
    def duration_ms(self) -> float
```

### Trace

```python
class Trace:
    trace_id: str               # UUID
    spans: list[Span]           # Collected spans
    model_used: str | None      # Final model used
    intent: str | None          # Router intent
    tool_calls_made: int        # Total tool calls
    user_message: str           # Original user message
    response_text: str          # Final response
    usage: dict | None          # Token usage from LLM

    @classmethod
    def start(cls) -> Trace     # Create + set in ContextVar
    @classmethod
    def current(cls) -> Trace | None  # Get from ContextVar

    def span(self, name, **metadata) -> SpanContext  # Async context manager
    def finish(...) -> dict     # Log + return summary
    def write_to_thread_log(...)  # Persist to JSONL
```

### Thread Log Format

```jsonl
{"trace_id": "abc-123", "total_ms": 4523, "model": "google/gemini-2.5-flash", "intent": "code", "tool_calls": 5, "spans": [...]}
{"trace_id": "def-456", "total_ms": 1200, "model": "minimax/minimax-m2.5", "intent": "lookup", "tool_calls": 2, "spans": [...]}
```

### Context Variable

`_current_trace` is a `ContextVar[Trace | None]` that makes the current
trace available anywhere in the async call stack without explicit
parameter passing. Agent code can call `Trace.current()` to access it.

---

## Cross-System Effects

| If You Change... | Also Check... |
|-----------------|---------------|
| Model rate limits | `_MODEL_LIMITS` dict in `rate_limiter.py` |
| API rate limits | `_API_LIMITS` dict in `rate_limiter.py` |
| Span names | Trace log consumers (monitoring dashboards) |
| Thread log format | Any log analysis scripts |
