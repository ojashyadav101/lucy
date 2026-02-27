#!/usr/bin/env python3
"""Run Lucy with Socket Mode (development) or HTTP mode (production).

Usage:
    python scripts/run.py              # Run with Socket Mode (default)
    python scripts/run.py --http       # Run HTTP mode with uvicorn
    python scripts/run.py --port 3000  # Custom port for HTTP mode
"""

import argparse
import os
import ssl
import sys
from pathlib import Path

# ── Single Instance Lock ───────────────────────────────────────────────────
PID_FILE = Path("/tmp/lucy_bot.pid")

def _check_single_instance() -> bool:
    """Prevent multiple Lucy bots from running simultaneously."""
    import psutil
    
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            if psutil.pid_exists(old_pid):
                # Check if it's actually a Lucy process
                proc = psutil.Process(old_pid)
                cmdline = " ".join(proc.cmdline())
                if "lucy" in cmdline.lower() or "scripts/run" in cmdline:
                    print(f"ERROR: Lucy is already running (PID {old_pid})", file=sys.stderr)
                    print(f"To stop it: kill {old_pid}", file=sys.stderr)
                    print(f"Or delete: rm {PID_FILE}", file=sys.stderr)
                    return False
        except (ValueError, psutil.NoSuchProcess, psutil.AccessDenied):
            pass  # Stale PID file, remove it
        PID_FILE.unlink(missing_ok=True)
    
    # Write our PID
    PID_FILE.write_text(str(os.getpid()))
    return True

def _cleanup_pid():
    """Remove PID file on exit."""
    PID_FILE.unlink(missing_ok=True)

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# ── SSL fix: make aiohttp/asyncio use certifi's CA bundle ──────────────────
# aiohttp ignores SSL_CERT_FILE; we must patch ssl.create_default_context.
try:
    import certifi as _certifi

    _orig_create_default_context = ssl.create_default_context

    def _certifi_create_default_context(purpose=ssl.Purpose.SERVER_AUTH, *args, **kwargs):
        if purpose == ssl.Purpose.SERVER_AUTH and "cafile" not in kwargs:
            kwargs["cafile"] = _certifi.where()
        return _orig_create_default_context(purpose, *args, **kwargs)

    ssl.create_default_context = _certifi_create_default_context
except ImportError:
    pass  # certifi not installed, fall through to system certs
# ───────────────────────────────────────────────────────────────────────────

from lucy.config import settings
import structlog

logger = structlog.get_logger()


def run_socket_mode() -> None:
    """Run Lucy with Socket Mode (WebSocket to Slack)."""
    import asyncio
    from slack_bolt.async_app import AsyncApp
    from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
    
    from lucy.slack.middleware import (
        resolve_workspace_middleware,
        resolve_user_middleware,
        resolve_channel_middleware,
    )
    from lucy.slack.handlers import register_handlers
    
    logger.info("starting_lucy", mode="socket_mode")
    
    async def _run():
        import ssl
        import certifi
        from slack_sdk.web.async_client import AsyncWebClient

        # Build an SSL context that uses certifi's CA bundle so aiohttp
        # can verify Slack's TLS certificate on macOS Homebrew Python.
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())

        # Create Bolt app with a custom web client that uses our SSL context.
        web_client = AsyncWebClient(
            token=settings.slack_bot_token,
            ssl=ssl_ctx,
        )
        bolt = AsyncApp(
            client=web_client,
            signing_secret=settings.slack_signing_secret,
            process_before_response=False,
        )
        
        # Register async middleware
        bolt.middleware(resolve_workspace_middleware)
        bolt.middleware(resolve_user_middleware)
        bolt.middleware(resolve_channel_middleware)
        
        # Register handlers
        register_handlers(bolt)
        
        logger.info("bolt_app_created", middlewares=3)

        # Pre-warm LLM message pools (non-blocking background task)
        from lucy.core.humanize import initialize_pools
        asyncio.create_task(initialize_pools())

        # Start cron scheduler — discovers and schedules all workspace crons
        from lucy.crons.scheduler import get_scheduler
        scheduler = get_scheduler(slack_client=web_client)
        await scheduler.start()

        handler = AsyncSocketModeHandler(bolt, settings.slack_app_token)
        await handler.start_async()
    
    asyncio.run(_run())


def run_http_mode(port: int = 3000) -> None:
    """Run Lucy with HTTP mode (for production)."""
    import uvicorn
    
    logger.info("starting_lucy", mode="http", port=port)
    
    uvicorn.run(
        "lucy.app:api",
        host="0.0.0.0",
        port=port,
        reload=(settings.env == "development"),
    )


def main() -> int:
    # Check for existing instance before doing anything else
    if not _check_single_instance():
        return 1
    
    parser = argparse.ArgumentParser(description="Run Lucy Slack bot")
    parser.add_argument("--http", action="store_true", help="Use HTTP mode instead of Socket Mode")
    parser.add_argument("--port", type=int, default=3000, help="Port for HTTP mode")
    parser.add_argument("--force", action="store_true", help="Force start even if another instance is running")
    args = parser.parse_args()
    
    # Allow override with --force
    if args.force and PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            print(f"Killing existing Lucy (PID {old_pid}) due to --force", file=sys.stderr)
            import psutil
            psutil.Process(old_pid).terminate()
            import time
            time.sleep(1)
            if psutil.pid_exists(old_pid):
                psutil.Process(old_pid).kill()
            PID_FILE.unlink(missing_ok=True)
        except Exception as e:
            print(f"Warning: Could not kill existing process: {e}", file=sys.stderr)
        # Re-check after killing
        if not _check_single_instance():
            return 1
    
    try:
        if args.http:
            run_http_mode(args.port)
        else:
            run_socket_mode()
        _cleanup_pid()
        return 0
    except KeyboardInterrupt:
        logger.info("shutting_down", reason="keyboard_interrupt")
        _cleanup_pid()
        return 0
    except Exception as e:
        logger.error("fatal_error", error=str(e))
        _cleanup_pid()
        return 1


if __name__ == "__main__":
    sys.exit(main())
