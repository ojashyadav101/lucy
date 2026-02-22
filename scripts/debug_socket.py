#!/usr/bin/env python3
"""Debug script: test Socket Mode connection with verbose logging."""
import logging
import ssl
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)

import certifi

ssl_ctx = ssl.create_default_context(cafile=certifi.where())

import asyncio
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_sdk.web.async_client import AsyncWebClient
from lucy.config import settings


async def main():
    print("Creating web client...", flush=True)
    wc = AsyncWebClient(token=settings.slack_bot_token, ssl=ssl_ctx)

    print("Testing auth...", flush=True)
    try:
        r = await wc.auth_test()
        print(f"auth.test OK: bot={r.data.get('user')}", flush=True)
    except Exception as e:
        print(f"auth.test FAILED: {e}", flush=True)

    print("Creating bolt app...", flush=True)
    bolt = AsyncApp(
        client=wc,
        signing_secret=settings.slack_signing_secret,
        process_before_response=True,
    )

    print("Creating Socket Mode handler...", flush=True)
    handler = AsyncSocketModeHandler(bolt, settings.slack_app_token)

    print("Testing apps.connections.open...", flush=True)
    app_wc = AsyncWebClient(token=settings.slack_app_token, ssl=ssl_ctx)
    try:
        r = await app_wc.apps_connections_open()
        print(f"connections.open OK: url={r.data.get('url', '?')[:40]}", flush=True)
    except Exception as e:
        print(f"connections.open FAILED: {e}", flush=True)

    print("Starting socket mode (5s timeout)...", flush=True)
    try:
        await asyncio.wait_for(handler.start_async(), timeout=15.0)
    except asyncio.TimeoutError:
        print("TIMED OUT after 15s", flush=True)
    except Exception as e:
        print(f"socket mode error: {e}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
