"""Lucy application entry point — Slack Bolt + FastAPI.

Architecture:
- FastAPI for health checks and web endpoints
- Slack Bolt for Slack events via Socket Mode or HTTP
- Async SQLAlchemy for database operations
"""

from __future__ import annotations

import os as _os
import ssl as _ssl
try:
    import certifi as _certifi
    _os.environ.setdefault("SSL_CERT_FILE", _certifi.where())
    _os.environ.setdefault("REQUESTS_CA_BUNDLE", _certifi.where())
    _ssl_ctx = _ssl.create_default_context(cafile=_certifi.where())
except ImportError:
    _ssl_ctx = None

import asyncio
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

from lucy.config import settings
from lucy.db.session import close_db
from lucy.slack.middleware import (
    resolve_workspace_middleware,
    resolve_user_middleware,
    resolve_channel_middleware,
)
from lucy.slack.handlers import register_handlers

logger = structlog.get_logger()

# Configure structlog
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer()
        if settings.env == "production"
        else structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(20),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)


# ═══════════════════════════════════════════════════════════════════════════════
# SLACK BOLT APP
# ═══════════════════════════════════════════════════════════════════════════════

from slack_sdk.web.async_client import AsyncWebClient as _AsyncWebClient
_bolt_client = _AsyncWebClient(token=settings.slack_bot_token, ssl=_ssl_ctx) if _ssl_ctx else None

bolt = AsyncApp(
    token=settings.slack_bot_token,
    signing_secret=settings.slack_signing_secret,
    process_before_response=True,
    client=_bolt_client,
)

# Register middleware (order matters!)
bolt.middleware(resolve_workspace_middleware)
bolt.middleware(resolve_user_middleware)
bolt.middleware(resolve_channel_middleware)

# Register event handlers
register_handlers(bolt)

# ═══════════════════════════════════════════════════════════════════════════════
# FASTAPI APP
# ═══════════════════════════════════════════════════════════════════════════════

async def _schema_refresh_loop(interval_seconds: float = 240.0) -> None:
    """Background task: proactively refresh stale capability indexes.

    Runs every *interval_seconds* (default 4 min). For each workspace whose
    index TTL has expired, fetches fresh tool schemas from Composio so the
    next user request hits a warm index with < 1 ms retrieval latency rather
    than paying the Composio API round-trip inline.
    """
    from lucy.retrieval.capability_index import get_capability_index
    from lucy.retrieval.tool_retriever import get_retriever
    from lucy.integrations.registry import get_integration_registry
    from lucy.db.session import AsyncSessionLocal
    from lucy.db.models import Workspace
    from sqlalchemy import select

    while True:
        await asyncio.sleep(interval_seconds)
        try:
            capability_index = get_capability_index()
            retriever = get_retriever()
            registry = get_integration_registry()

            # Fetch all workspace IDs from DB
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(Workspace.id))
                workspace_ids = [row[0] for row in result.fetchall()]

            refreshed = 0
            for ws_id in workspace_ids:
                ws_index = capability_index.get(str(ws_id))
                if ws_index.is_stale:
                    try:
                        active_providers = await registry.get_active_providers(ws_id)
                        if active_providers:
                            connected = {p.lower() for p in active_providers}
                            await retriever.populate(ws_id, connected)
                            refreshed += 1
                    except Exception as e:
                        logger.warning(
                            "bg_schema_refresh_failed",
                            workspace_id=str(ws_id),
                            error=str(e),
                        )

            if refreshed:
                logger.info("bg_schema_refresh_complete", workspaces_refreshed=refreshed)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("bg_schema_refresh_loop_error", error=str(e))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    logger.info("app_starting", env=settings.env)

    if settings.env == "development":
        logger.info("database_initializing")

    # Start background capability index refresh task
    refresh_task = asyncio.create_task(_schema_refresh_loop())

    yield

    # Shutdown
    refresh_task.cancel()
    try:
        await refresh_task
    except asyncio.CancelledError:
        pass
    logger.info("app_shutting_down")
    await close_db()


api = FastAPI(
    title="Lucy",
    version="0.1.0",
    description="AI coworker for Slack — built on OpenClaw",
    lifespan=lifespan,
)

handler = AsyncSlackRequestHandler(bolt)


@api.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok", "service": "lucy"}


@api.get("/metrics")
async def metrics_endpoint() -> dict:
    """Live reliability metrics snapshot.

    Returns counters and latency histograms (p50/p95/p99) for:
      - tool calls, errors, loops, unknown-tool attempts, no-text fallbacks
      - task throughput by status
      - LLM turn latency, tool execution latency, full task latency
    """
    from lucy.observability.metrics import get_metrics
    from lucy.core.circuit_breaker import all_breaker_snapshots
    snap = await get_metrics().snapshot()
    snap["circuit_breakers"] = all_breaker_snapshots()
    return snap


@api.get("/health/slo")
async def health_slo() -> dict:
    """SLO evaluation against live metrics.

    Returns pass/fail status for each production SLO:
      - tool_success_rate >= 99%
      - no_text_fallback_rate <= 0.5%
      - unknown_tool_rate <= 0.1%
      - tool_p95_latency_ms <= 8 000 ms
      - task_p95_latency_ms <= 30 000 ms
    """
    from lucy.observability.slo import get_slo_evaluator
    report = await get_slo_evaluator().check_and_alert(logger)
    return report.to_dict()


@api.get("/health/index")
async def health_index() -> dict:
    """Capability index stats (tool retrieval architecture)."""
    from lucy.retrieval.capability_index import get_capability_index
    return get_capability_index().snapshot()


@api.get("/health/db")
async def health_db() -> dict[str, str]:
    """Database health check."""
    try:
        from lucy.db.session import async_engine
        from sqlalchemy import text
        
        async with async_engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            result.scalar()
        
        return {"status": "ok", "database": "connected"}
    except Exception as e:
        logger.error("db_health_check_failed", error=str(e))
        return {"status": "error", "database": "disconnected"}


@api.post("/slack/events")
async def slack_events(req: object) -> object:
    """Slack events endpoint for HTTP mode.
    
    In Socket Mode, this is not used. Bolt handles events via WebSocket.
    """
    return await handler.handle(req)  # type: ignore[arg-type]


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    """Run Lucy with Socket Mode (recommended for development)."""
    import asyncio

    import signal
    import sys

    _pidfile = "/tmp/lucy.pid"

    def _check_singleton():
        """Ensure only one Lucy process runs at a time.

        Kills any existing Lucy process regardless of entry point
        (lucy.app or scripts/run.py).
        """
        import subprocess
        try:
            result = subprocess.run(
                ["pgrep", "-f", "lucy.app|scripts/run.py"],
                capture_output=True, text=True,
            )
            for line in result.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                old_pid = int(line.strip())
                if old_pid == _os.getpid():
                    continue
                try:
                    _os.kill(old_pid, 0)
                    logger.warning("killing_stale_lucy", pid=old_pid)
                    _os.kill(old_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        except Exception:
            pass

        for pf in [_pidfile, "/tmp/lucy_bot.pid"]:
            if _os.path.exists(pf):
                try:
                    old_pid = int(open(pf).read().strip())
                    if old_pid != _os.getpid():
                        _os.kill(old_pid, signal.SIGKILL)
                except (ProcessLookupError, ValueError, OSError):
                    pass

        with open(_pidfile, "w") as f:
            f.write(str(_os.getpid()))

        def _cleanup(*_):
            try:
                _os.remove(_pidfile)
            except FileNotFoundError:
                pass
            sys.exit(0)

        signal.signal(signal.SIGTERM, _cleanup)
        signal.signal(signal.SIGINT, _cleanup)

    _check_singleton()
    logger.info("starting_lucy", mode="socket_mode", pid=_os.getpid())

    async def _warm_bm25_index():
        """Eagerly populate BM25 index for all workspaces on startup.

        Without this, the first request after a restart hits an empty index
        and falls through to meta-tools, which the LLM often mishandles
        (producing 'I don't have access' false negatives).
        """
        try:
            from lucy.db.session import AsyncSessionLocal
            from lucy.db.models import Workspace
            from lucy.integrations.registry import get_integration_registry
            from lucy.retrieval.tool_retriever import get_retriever
            from sqlalchemy import select
            import time as _t

            t0 = _t.monotonic()
            registry = get_integration_registry()
            retriever = get_retriever()

            async with AsyncSessionLocal() as db:
                result = await db.execute(select(Workspace.id))
                workspace_ids = [row[0] for row in result.fetchall()]

            total_tools = 0
            for ws_id in workspace_ids:
                try:
                    active = await registry.get_active_providers(ws_id)
                    if active:
                        connected = {p.lower() for p in active}
                        count = await retriever.populate(ws_id, connected)
                        total_tools += count
                        logger.info(
                            "startup_index_populated",
                            workspace_id=str(ws_id),
                            apps=list(connected),
                            tools=count,
                        )
                except Exception as e:
                    logger.warning("startup_index_failed", workspace_id=str(ws_id), error=str(e))

            elapsed = (_t.monotonic() - t0) * 1000
            logger.info("startup_warmup_complete", workspaces=len(workspace_ids), total_tools=total_tools, elapsed_ms=round(elapsed))
        except Exception as e:
            logger.error("startup_warmup_error", error=str(e))

    async def _run():
        await _warm_bm25_index()
        handler = AsyncSocketModeHandler(bolt, settings.slack_app_token)
        await handler.start_async()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
