"""Lucy application entry point — Slack Bolt + FastAPI.

Architecture:
- FastAPI for health checks
- Slack Bolt for Slack events via Socket Mode
- Async SQLAlchemy for database
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
import signal
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp

from lucy.config import settings
from lucy.db.session import close_db
from lucy.slack.handlers import register_handlers
from lucy.slack.middleware import (
    resolve_channel_middleware,
    resolve_user_middleware,
    resolve_workspace_middleware,
)

logger = structlog.get_logger()

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

# ═══════════════════════════════════════════════════════════════════════════
# SLACK BOLT APP
# ═══════════════════════════════════════════════════════════════════════════

from slack_sdk.web.async_client import AsyncWebClient as _AsyncWebClient  # noqa: E402

_multi_tenant = bool(settings.slack_client_id)

if _multi_tenant:
    from lucy.web.authorize import multi_tenant_authorize

    bolt = AsyncApp(
        signing_secret=settings.slack_signing_secret,
        authorize=multi_tenant_authorize,
        process_before_response=False,
    )
    logger.info("bolt_init_multi_tenant")
else:
    _bolt_client = (
        _AsyncWebClient(token=settings.slack_bot_token, ssl=_ssl_ctx)
        if _ssl_ctx
        else None
    )
    bolt = AsyncApp(
        token=settings.slack_bot_token,
        signing_secret=settings.slack_signing_secret,
        client=_bolt_client,
        process_before_response=False,
    )
    logger.info("bolt_init_single_tenant")

bolt.middleware(resolve_workspace_middleware)
bolt.middleware(resolve_user_middleware)
bolt.middleware(resolve_channel_middleware)
register_handlers(bolt)


# ═══════════════════════════════════════════════════════════════════════════
# FASTAPI APP
# ═══════════════════════════════════════════════════════════════════════════


# Shutdown coordination: set when SIGTERM received, checked by handlers
shutting_down = asyncio.Event()
_active_agents: set[asyncio.Task] = set()  # type: ignore[type-arg]
_DRAIN_TIMEOUT = 60


def track_agent_task(task: asyncio.Task) -> None:  # type: ignore[type-arg]
    """Register an in-flight agent task for graceful drain."""
    _active_agents.add(task)
    task.add_done_callback(_active_agents.discard)


def is_shutting_down() -> bool:
    return shutting_down.is_set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager with graceful shutdown."""
    logger.info("app_starting", env=settings.env)

    from lucy.pipeline.humanize import initialize_pools

    asyncio.create_task(initialize_pools())

    from lucy.crons.scheduler import get_scheduler

    scheduler = get_scheduler(slack_client=bolt.client)
    await scheduler.start()

    email_listener = await _start_email_listener(bolt.client)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: shutting_down.set())

    yield

    logger.info("app_shutting_down", active_agents=len(_active_agents))

    if _active_agents:
        logger.info("draining_agents", count=len(_active_agents))
        done, pending = await asyncio.wait(
            _active_agents, timeout=_DRAIN_TIMEOUT,
        )
        if pending:
            logger.warning(
                "drain_timeout_forcing_cancel",
                cancelled=len(pending),
                completed=len(done),
            )
            for task in pending:
                task.cancel()

    if email_listener:
        await email_listener.stop()
    await scheduler.stop()
    await close_db()
    logger.info("app_shutdown_complete")


api = FastAPI(
    title="Lucy",
    version="0.2.0",
    description="AI coworker for Slack — proactive, skill-driven, built on OpenClaw",
    lifespan=lifespan,
)

handler = AsyncSlackRequestHandler(bolt)


@api.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "lucy"}


@api.get("/health/db")
async def health_db() -> dict[str, str]:
    try:
        from sqlalchemy import text

        from lucy.db.session import async_engine

        async with async_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ok", "database": "connected"}
    except Exception as e:
        logger.error("db_health_check_failed", error=str(e))
        return {"status": "error", "database": "disconnected"}


@api.post("/slack/events")
async def slack_events(req: Request) -> object:
    """Slack events endpoint for HTTP mode (Events API + interactivity)."""
    return await handler.handle(req)  # type: ignore[arg-type]


from lucy.web.oauth import router as _oauth_router

api.include_router(_oauth_router)

if settings.spaces_enabled:
    from lucy.spaces.http_endpoints import router as _spaces_router

    api.include_router(_spaces_router)


# ═══════════════════════════════════════════════════════════════════════════
# EMAIL LISTENER STARTUP
# ═══════════════════════════════════════════════════════════════════════════


async def _start_email_listener(slack_client: object) -> object | None:
    """Start the AgentMail WebSocket listener if configured."""
    if not settings.agentmail_enabled or not settings.agentmail_api_key:
        return None

    try:
        from lucy.integrations.email_listener import get_email_listener

        listener = get_email_listener()
        inbox_id = f"lucy@{settings.agentmail_domain}"

        channel = None
        try:
            result = await slack_client.conversations_list(  # type: ignore[attr-defined]
                types="public_channel", limit=100,
            )
            for ch in result.get("channels", []):
                name = ch.get("name", "")
                if name in ("talk-to-lucy", "lucy-my-ai", "lucy", "general"):
                    channel = ch.get("id")
                    break
        except Exception as e:
            logger.warning("email_channel_lookup_failed", error=str(e))

        await listener.start(
            slack_client=slack_client,
            inbox_ids=[inbox_id],
            notification_channel=channel,
        )
        logger.info(
            "email_listener_started",
            inbox_id=inbox_id,
            notification_channel=channel,
        )
        return listener

    except Exception as e:
        logger.warning("email_listener_start_failed", error=str(e))
        return None


# ═══════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════


def main() -> None:
    """Run Lucy with Socket Mode."""
    import signal
    import sys

    _pidfile = "/tmp/lucy.pid"

    def _check_singleton() -> None:
        import subprocess

        try:
            result = subprocess.run(
                ["pgrep", "-f", "lucy.app|scripts/run.py"],
                capture_output=True,
                text=True,
            )
            for line in result.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                old_pid = int(line.strip())
                if old_pid == _os.getpid():
                    continue
                try:
                    _os.kill(old_pid, 0)
                    logger.warning("graceful_shutdown_stale_lucy", pid=old_pid)
                    # SIGTERM first — gives the old process a chance to flush
                    # logs, release DB connections, and clean up state.
                    # SIGKILL only as fallback after 5s.
                    _os.kill(old_pid, signal.SIGTERM)
                    import time as _time
                    for _ in range(10):
                        _time.sleep(0.5)
                        try:
                            _os.kill(old_pid, 0)
                        except ProcessLookupError:
                            break
                    else:
                        logger.warning("force_killing_stale_lucy", pid=old_pid)
                        try:
                            _os.kill(old_pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                except ProcessLookupError:
                    pass
        except Exception as e:
            logger.warning("stale_pid_cleanup_failed", error=str(e))

        for pf in [_pidfile, "/tmp/lucy_bot.pid"]:
            if _os.path.exists(pf):
                try:
                    old_pid = int(open(pf).read().strip())
                    if old_pid != _os.getpid():
                        _os.kill(old_pid, signal.SIGTERM)
                except (ProcessLookupError, ValueError, OSError):
                    pass

        with open(_pidfile, "w") as f:
            f.write(str(_os.getpid()))

        def _cleanup(*_: object) -> None:
            try:
                _os.remove(_pidfile)
            except FileNotFoundError:
                pass
            sys.exit(0)

        signal.signal(signal.SIGTERM, _cleanup)
        signal.signal(signal.SIGINT, _cleanup)

    _check_singleton()
    logger.info("starting_lucy", mode="socket_mode", pid=_os.getpid())

    async def _run() -> None:
        from lucy.crons.scheduler import get_scheduler

        scheduler = get_scheduler(slack_client=bolt.client)
        await scheduler.start()

        email_listener = await _start_email_listener(bolt.client)

        sm_handler = AsyncSocketModeHandler(bolt, settings.slack_app_token)
        try:
            await sm_handler.start_async()
        finally:
            # Mirror the HTTP-mode lifespan shutdown: clean up all resources
            # even in Socket Mode so logs are flushed and connections released.
            if email_listener:
                try:
                    await email_listener.stop()
                except Exception:
                    pass
            try:
                scheduler.stop()
            except Exception:
                pass
            try:
                from lucy.db import close_db
                await close_db()
            except Exception:
                pass

    asyncio.run(_run())


if __name__ == "__main__":
    main()
