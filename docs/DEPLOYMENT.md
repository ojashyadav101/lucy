# Deployment & Operations — Deep Dive

> Application entry points, infrastructure setup, environment configuration,
> exception handling, and testing.

---

## Application Architecture

```mermaid
flowchart TD
    subgraph entry [Entry Points]
        RunPy["scripts/run.py"]
        AppPy["src/lucy/app.py"]
    end

    subgraph modes [Runtime Modes]
        Socket["Socket Mode\n(WebSocket to Slack)"]
        HTTP["HTTP Mode\n(uvicorn + FastAPI)"]
    end

    subgraph services [Background Services]
        Scheduler["Cron Scheduler"]
        EmailListener["Email Listener"]
        Pools["Humanize Pools"]
    end

    RunPy -->|"--http flag"| HTTP
    RunPy -->|"default"| Socket
    AppPy --> HTTP

    Socket --> Scheduler
    Socket --> EmailListener
    Socket --> Pools
    HTTP --> Scheduler
    HTTP --> EmailListener
```

---

## Entry Point: `scripts/run.py`

The primary way to start Lucy.

### Startup Sequence (Socket Mode — default)

```
1. Singleton check
   ├── Read PID from /tmp/lucy_bot.pid
   ├── If process alive → exit (unless --force)
   └── Write current PID

2. SSL fix
   ├── Import certifi CA bundle
   └── Patch ssl.create_default_context to use it

3. Create Slack Bolt app
   ├── Token: settings.slack_bot_token
   ├── Signing secret: settings.slack_signing_secret
   └── Custom WebClient with certifi SSL context

4. Register middleware (order matters)
   ├── resolve_workspace_middleware
   ├── resolve_user_middleware
   └── resolve_channel_middleware

5. Register event handlers
   └── register_handlers(bolt)

6. Pre-warm LLM pools (non-blocking)
   └── asyncio.create_task(initialize_pools())

7. Start cron scheduler
   └── await get_scheduler(slack_client).start()

8. Start Socket Mode handler
   └── AsyncSocketModeHandler(bolt, settings.slack_app_token)
```

### Startup Sequence (HTTP Mode — `--http`)

```
1. Same singleton check
2. Run uvicorn with lucy.app:api
   ├── Host: 0.0.0.0
   ├── Port: --port (default 3000)
   └── Auto-reload: True in development
```

### CLI Arguments

| Argument | Default | Purpose |
|----------|---------|---------|
| `--http` | False | Use HTTP mode instead of Socket Mode |
| `--port` | 3000 | Port for HTTP mode |
| `--force` | False | Force start even if another instance is running |

### Singleton Check

- PID file: `/tmp/lucy_bot.pid`
- Kills stale processes matching `lucy.app` or `scripts/run.py`
- Cleans up PID file on SIGTERM/SIGINT

---

## FastAPI Application: `src/lucy/app.py`

### Routes

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/health` | Returns `{"status": "ok", "service": "lucy"}` |
| `GET` | `/health/db` | Tests database connection, returns status |
| `POST` | `/slack/events` | Slack events endpoint (HTTP mode only) |
| — | `/spaces/*` | Spaces router (conditional, if `spaces_enabled`) |

### Slack Bolt Setup

```python
bolt = AsyncApp(
    token=settings.slack_bot_token,
    signing_secret=settings.slack_signing_secret,
)

bolt.middleware(resolve_workspace_middleware)
bolt.middleware(resolve_user_middleware)
bolt.middleware(resolve_channel_middleware)
register_handlers(bolt)
```

### Lifespan Events

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # STARTUP
    scheduler = get_scheduler(slack_client)
    await scheduler.start()

    if settings.agentmail_enabled and settings.agentmail_api_key:
        listener = get_email_listener()
        inbox_id = f"lucy@{settings.agentmail_domain}"
        channel = auto_discover_notification_channel()
        await listener.start(slack_client, [inbox_id], channel)

    yield  # Application runs

    # SHUTDOWN
    if listener and listener.running:
        await listener.stop()
    await scheduler.stop()
    await close_db()
```

### Email Listener Auto-Discovery

The notification channel for inbound emails is auto-discovered by checking
for these channel names (in order): `talk-to-lucy`, `lucy-my-ai`, `lucy`,
`general`.

### Version

```
Title: "Lucy"
Version: "0.2.0"
Description: "AI coworker for Slack — proactive, skill-driven, built on OpenClaw"
```

---

## Infrastructure Services

### Docker Compose: `docker-compose.yml`

| Service | Image | Port | Purpose |
|---------|-------|------|---------|
| `postgres` | `postgres:16` | 5432 | Primary database |
| `camofox` | CamoFox image | 9377 | Anti-detection browser |

PostgreSQL credentials: `lucy:lucy`, database: `lucy`

### Database URL

Default: `postgresql+asyncpg://lucy:lucy@localhost:5432/lucy`

Override via `LUCY_DATABASE_URL` environment variable.

---

## Environment Configuration

### `.env.example`

All environment variables are prefixed with `LUCY_`.

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `LUCY_SLACK_BOT_TOKEN` | Yes | — | Slack bot OAuth token |
| `LUCY_SLACK_APP_TOKEN` | Yes | — | Slack app-level token (Socket Mode) |
| `LUCY_SLACK_SIGNING_SECRET` | Yes | — | Slack request verification |
| `LUCY_OPENROUTER_API_KEY` | Yes | — | OpenRouter API key for LLM |
| `LUCY_OPENROUTER_BASE_URL` | No | `https://openrouter.ai/api/v1` | OpenRouter endpoint |
| `LUCY_OPENCLAW_MODEL` | No | `google/gemini-2.5-flash` | Default model |
| `LUCY_OPENCLAW_READ_TIMEOUT` | No | `120.0` | LLM read timeout (seconds) |
| `LUCY_MODEL_TIER_FAST` | No | `google/gemini-2.5-flash` | Fast tier model |
| `LUCY_MODEL_TIER_DEFAULT` | No | `moonshotai/kimi-k2.5` | Default tier model |
| `LUCY_MODEL_TIER_CODE` | No | `minimax/minimax-m2.5` | Code tier model |
| `LUCY_MODEL_TIER_RESEARCH` | No | `google/gemini-3-flash-preview` | Research tier model |
| `LUCY_MODEL_TIER_DOCUMENT` | No | `moonshotai/kimi-k2.5` | Document tier model |
| `LUCY_MODEL_TIER_FRONTIER` | No | `google/gemini-3.1-pro-preview` | Frontier tier model |
| `LUCY_OPENCLAW_BASE_URL` | No | `http://167.86.82.46:18791` | OpenClaw Gateway URL |
| `LUCY_OPENCLAW_API_KEY` | No | — | OpenClaw Gateway token |
| `LUCY_CAMOFOX_URL` | No | `http://localhost:9377` | CamoFox browser URL |
| `LUCY_DATABASE_URL` | No | `postgresql+asyncpg://...` | Database connection string |
| `LUCY_COMPOSIO_API_KEY` | No | — | Composio SDK key |
| `LUCY_AGENTMAIL_API_KEY` | No | — | AgentMail API key |
| `LUCY_AGENTMAIL_DOMAIN` | No | `zeeyamail.com` | Email domain |
| `LUCY_AGENTMAIL_ENABLED` | No | `True` | Enable email tools |
| `LUCY_WORKSPACE_ROOT` | No | `./workspaces` | Workspace filesystem root |
| `LUCY_CONVEX_TEAM_TOKEN` | No | — | Convex team token |
| `LUCY_CONVEX_TEAM_ID` | No | — | Convex team ID |
| `LUCY_VERCEL_TOKEN` | No | — | Vercel API token |
| `LUCY_VERCEL_TEAM_ID` | No | — | Vercel team ID |
| `LUCY_SPACES_DOMAIN` | No | `zeeya.app` | Spaces custom domain |
| `LUCY_SPACES_ENABLED` | No | `True` | Enable Spaces platform |
| `LUCY_LOG_LEVEL` | No | `INFO` | Logging level |
| `LUCY_ENV` | No | `development` | Environment name |

### `keys.json` Fallback

If environment variables are not set, Lucy loads credentials from
`keys.json` in the project root:

```json
{
    "openclaw_lucy": {
        "gateway_token": "...",
        "openrouter_api_key": "..."
    },
    "composio": {
        "api_key": "..."
    },
    "agentmail": {
        "api_key": "..."
    },
    "convex": {
        "team_token": "...",
        "team_id": "..."
    },
    "vercel": {
        "token": "...",
        "team_id": "..."
    }
}
```

Loading order: environment variable > `keys.json` > default value.

---

## Dependencies

**File:** `pyproject.toml`

### Runtime Dependencies

| Package | Purpose |
|---------|---------|
| `slack-bolt[async]` | Slack event handling |
| `slack-sdk` | Slack Web API client |
| `fastapi` | HTTP API framework |
| `uvicorn[standard]` | ASGI server |
| `sqlalchemy[asyncio]` | ORM with async support |
| `asyncpg` | PostgreSQL async driver |
| `alembic` | Database migrations |
| `httpx` | Async HTTP client |
| `pydantic-settings` | Configuration management |
| `structlog` | Structured logging |
| `apscheduler` | Cron job scheduling |
| `composio-core` | Composio SDK |
| `agentmail` | Email SDK |
| `certifi` | SSL CA certificates |
| `python-dotenv` | .env file loading |
| `tenacity` | Retry logic |
| `openpyxl` | Excel file generation |
| `weasyprint` | PDF generation |
| `pyyaml` | YAML parsing (skill frontmatter) |

### Development Dependencies

| Package | Purpose |
|---------|---------|
| `pytest` | Test runner |
| `pytest-asyncio` | Async test support |
| `ruff` | Linter + formatter |
| `mypy` | Type checking |

---

## Exception Hierarchy

```
Exception
└── LucyError                          (src/lucy/core/__init__.py)
    └── (all domain exceptions inherit from this)

Exception
├── OpenClawError                      (src/lucy/core/openclaw.py)
│   Attributes: status_code (int | None)
│   Raised: LLM API failures (429, 500, 502, 503, 504)
│   Caught: agent.py, handlers.py, sub_agents.py
│
├── OpenClawGatewayError               (src/lucy/integrations/openclaw_gateway.py)
│   Attributes: tool (str)
│   Raised: Gateway exec/file/web_fetch failures
│   Caught: mcp_manager.py
│
├── CamoFoxError                       (src/lucy/integrations/camofox.py)
│   Attributes: status_code (int), detail (str)
│   Raised: Browser API call failures
│   Caught: camofox.py internally
│
└── _RetryableComposioError            (src/lucy/integrations/composio_client.py)
    Internal only — triggers retry logic in Composio client
```

### Error Flow

```
Tool execution fails
    │
    ├── OpenClawError (LLM failure)
    │     → Agent retries with backoff
    │     → Escalates model on repeated failures
    │     → Supervisor may REPLAN or ESCALATE
    │
    ├── OpenClawGatewayError (VPS failure)
    │     → MCP installation aborted
    │     → Falls back to next resolution stage
    │
    ├── CamoFoxError (browser failure)
    │     → Heartbeat marks consecutive failure
    │     → After 3 failures: status = "error"
    │
    └── General Exception
          → _run_with_recovery() catches
          → classify_error_for_degradation()
          → get_degradation_message()
          → User sees friendly error
```

---

## Testing

### Test Infrastructure

**File:** `tests/conftest.py`

```python
# Session-scoped async engine (one DB per test run)
@pytest.fixture(scope="session")
async def engine():
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()

# Per-test database session with rollback
@pytest.fixture
async def db_session(engine):
    async with AsyncSession(engine) as session:
        yield session
        await session.rollback()
```

### Test Suites

| File | Tests | Coverage |
|------|-------|----------|
| `test_quality_fixes.py` | 12+ | Event deduplication, retry logic, failure recovery, 400 recovery, output pipeline, tool search, context trimming |
| `test_round4.py` | 20 | Slack history search (5), system prompt audit (5), file output tools (5), edge case handlers (5) |

### Running Tests

```bash
pytest tests/ -v                    # Run all tests
pytest tests/test_quality_fixes.py  # Run specific suite
pytest -x                           # Stop on first failure
pytest --tb=short                   # Short traceback
```

### What's Tested

- Event deduplication (30s TTL)
- Retry logic with tenacity in OpenClaw client
- LLM-redirected failure recovery
- Silent recovery cascade
- 400 error recovery mid-loop
- Dynamic environment injection into prompts
- Three-layer output pipeline (sanitize, markdown, tone)
- Tool search pre-filtering
- System prompt intelligence sections
- Slack history search tools
- File generation tools (PDF, Excel, CSV)
- Edge case handlers (status, cancellation, deduplication)

### What's Not Tested

- Full end-to-end Slack flow (requires live Slack app)
- Composio tool execution (requires API key)
- Heartbeat evaluation loop (requires database)
- Cron scheduler timing (requires APScheduler)
- Email listener WebSocket (requires AgentMail)

---

## Logging

Lucy uses `structlog` with JSON output in production and console output
in development.

### Configuration

```python
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        # JSON in production, Console in dev
        structlog.processors.JSONRenderer()
        if settings.env != "development"
        else structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(20),
)
```

### Log Levels

| Level | Use Case |
|-------|----------|
| `DEBUG` | Internal mechanics, cache hits/misses |
| `INFO` | Actions taken (tool calls, messages sent) |
| `WARNING` | Degraded states (rate limited, fallback used) |
| `ERROR` | Failures (API errors, exceptions) |

### Structured Fields

Every log event includes `workspace_id` when available. The `Trace` system
adds `trace_id` for request-scoped correlation.

---

## Alembic Configuration

**File:** `alembic.ini`

| Setting | Value |
|---------|-------|
| Script location | `migrations` |
| File template | `%%(year)d%%(month).2d%%(day).2d_%%(hour).2d%%(minute).2d_%%(rev)s_%%(slug)s` |
| Post-write hooks | `ruff` formatting |
| SQL URL | Overridden from `settings.database_url` |

### Async Migration Support

`migrations/env.py` uses `run_async_migrations()` to support async engines:

```python
async def run_async_migrations():
    engine = create_async_engine(settings.database_url)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()
```
