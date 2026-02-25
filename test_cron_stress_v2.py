"""Comprehensive cron stress test: diverse types, immediate execution, quality analysis."""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone as tz
from pathlib import Path

import httpx
import certifi

KEYS = json.load(open("keys.json"))
BOT_TOKEN = KEYS["slack"]["bot_token"]
USER_TOKEN = KEYS["slack"]["user_token"]
CHANNEL = "C0AGNRMGALS"
BOT_USER_ID = "U0AG8LVAB4M"
WORKSPACE = "8e302095-f4e6-4243-906f-55f6c3bd2583"
SSL = certifi.where()
CRONS_DIR = Path(
    f"/Users/ojashyadav/.cursor/worktrees/lucy/rdj/workspaces/"
    f"{WORKSPACE}/crons"
)

USER_HEADERS = {
    "Authorization": f"Bearer {USER_TOKEN}",
    "Content-Type": "application/json",
}
BOT_HEADERS = {
    "Authorization": f"Bearer {BOT_TOKEN}",
    "Content-Type": "application/json",
}

_PROGRESS_MARKERS = [
    "working on", "let me", "looking into", "give me a moment",
    "processing", "one moment", "on it", "i'll", "checking",
    "searching", "pulling", "gathering", "fetching",
]


def _is_progress(text: str) -> bool:
    low = text.lower().strip()
    return any(m in low for m in _PROGRESS_MARKERS) and len(low) < 120


async def send(text: str, thread: str | None = None) -> str:
    body: dict = {"channel": CHANNEL, "text": text}
    if thread:
        body["thread_ts"] = thread
    async with httpx.AsyncClient(verify=SSL) as c:
        r = await c.post(
            "https://slack.com/api/chat.postMessage",
            headers=USER_HEADERS, json=body,
        )
        data = r.json()
        if not data.get("ok"):
            print(f"    [send error] {data.get('error')}")
            return ""
        return data["ts"]


async def wait_reply(
    thread: str, timeout: int = 180, after_ts: str | None = None,
) -> str | None:
    cutoff = after_ts or thread
    deadline = time.time() + timeout
    while time.time() < deadline:
        async with httpx.AsyncClient(verify=SSL) as c:
            r = await c.get(
                "https://slack.com/api/conversations.replies",
                headers=BOT_HEADERS,
                params={"channel": CHANNEL, "ts": thread, "limit": 50},
            )
        msgs = r.json().get("messages", [])
        for m in msgs:
            if (
                (m.get("user") == BOT_USER_ID or m.get("bot_id"))
                and m["ts"] > cutoff
                and not _is_progress(m.get("text", ""))
            ):
                return m["text"]
        await asyncio.sleep(5)
    return None


async def check_channel_for_bot_messages(
    after_ts: str, timeout: int = 300, min_messages: int = 1,
) -> list[str]:
    """Collect bot messages that appeared in the channel after a timestamp."""
    deadline = time.time() + timeout
    found: list[str] = []
    seen_ts: set[str] = set()
    while time.time() < deadline:
        async with httpx.AsyncClient(verify=SSL) as c:
            r = await c.get(
                "https://slack.com/api/conversations.history",
                headers=BOT_HEADERS,
                params={"channel": CHANNEL, "oldest": after_ts, "limit": 30},
            )
        msgs = r.json().get("messages", [])
        for m in msgs:
            is_bot = m.get("user") == BOT_USER_ID or m.get("bot_id")
            if not is_bot:
                continue
            ts_val = m.get("ts", "")
            if ts_val in seen_ts:
                continue
            seen_ts.add(ts_val)
            text = m.get("text", "")
            if text and not _is_progress(text):
                found.append(text)
        if len(found) >= min_messages:
            return found
        await asyncio.sleep(10)
    return found


def read_execution_log(cron_slug: str) -> str:
    log_path = CRONS_DIR / cron_slug / "execution.log"
    if log_path.exists():
        return log_path.read_text()
    return "(no execution log)"


def read_learnings(cron_slug: str) -> str:
    path = CRONS_DIR / cron_slug / "LEARNINGS.md"
    if path.exists():
        return path.read_text()
    return "(no learnings)"


def read_task_json(cron_slug: str) -> dict:
    path = CRONS_DIR / cron_slug / "task.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}


async def main() -> None:
    print("=" * 70)
    print("CRON STRESS TEST V2 — Diverse Types, Immediate Execution")
    print("=" * 70)

    # ── Phase 1: Clean slate ─────────────────────────────────────────
    print("\n" + "─" * 70)
    print("PHASE 1: Delete all existing user cron jobs")
    print("─" * 70)

    ts1 = await send(
        f"<@{BOT_USER_ID}> Delete ALL my cron jobs. Every single one. "
        "I want a completely clean slate. Don't ask for confirmation, "
        "just delete them all."
    )
    reply1 = await wait_reply(ts1, timeout=120)
    print(f"  Lucy: {(reply1 or 'NO REPLY')[:300]}")

    # Also manually clean any leftovers
    for d in CRONS_DIR.iterdir():
        if d.is_dir() and d.name not in (
            "heartbeat", "slack-sync", "slack-history-sync",
            "slack-message-sync",
        ):
            import shutil
            shutil.rmtree(d, ignore_errors=True)
            print(f"  [cleanup] removed {d.name}")

    await asyncio.sleep(5)

    # ── Phase 2: Create diverse cron jobs ────────────────────────────
    print("\n" + "─" * 70)
    print("PHASE 2: Create diverse cron jobs (all fire within 3 minutes)")
    print("─" * 70)

    # Record the time before we create crons
    pre_create_ts = str(time.time())

    test_crons = [
        {
            "label": "Bitcoin Price Check",
            "message": (
                f"<@{BOT_USER_ID}> Create a cron job called 'btc-price' "
                "that runs every 2 minutes. It should check the current "
                "Bitcoin price and give me a quick update. "
                "Use Asia/Kolkata timezone. "
                "Deliver it to this channel."
            ),
            "slug": "btc-price",
        },
        {
            "label": "Weather Report (DM mode)",
            "message": (
                f"<@{BOT_USER_ID}> Set up a cron called 'weather-check' "
                "that runs every 2 minutes. Check the current weather "
                "in Mumbai, India and give me a brief update. "
                "Send it as a DM to me, not the channel. "
                "Use Asia/Kolkata timezone."
            ),
            "slug": "weather-check",
        },
        {
            "label": "GitHub Repo Stats",
            "message": (
                f"<@{BOT_USER_ID}> Create a recurring task called "
                "'github-stats' every 3 minutes. Check the trending "
                "repositories on GitHub today and list the top 3. "
                "Asia/Kolkata timezone, post to this channel."
            ),
            "slug": "github-stats",
        },
        {
            "label": "Simple Reminder",
            "message": (
                f"<@{BOT_USER_ID}> Remind me every 2 minutes to "
                "stretch and take a break. DM me the reminder. "
                "Asia/Kolkata timezone."
            ),
            "slug": "stretch-reminder",
        },
        {
            "label": "Product Availability Check",
            "message": (
                f"<@{BOT_USER_ID}> Create a cron called 'product-watch' "
                "every 3 minutes. Check if the PlayStation 5 is available "
                "on amazon.in and report the price and availability. "
                "Post to this channel. Asia/Kolkata timezone."
            ),
            "slug": "product-watch",
        },
        {
            "label": "Daily Motivation",
            "message": (
                f"<@{BOT_USER_ID}> Set up a task called 'motivation' "
                "every 2 minutes. Share an inspiring quote or thought "
                "to start the day. Make it different each time. "
                "Post to this channel. Asia/Kolkata."
            ),
            "slug": "motivation",
        },
    ]

    threads: dict[str, str] = {}

    for tc in test_crons:
        print(f"\n  Creating: {tc['label']}...")
        ts = await send(tc["message"])
        threads[tc["slug"]] = ts
        reply = await wait_reply(ts, timeout=90)
        print(f"    Lucy: {(reply or 'NO REPLY')[:200]}")
        await asyncio.sleep(3)

    # ── Phase 3: Verify task.json files ──────────────────────────────
    print("\n" + "─" * 70)
    print("PHASE 3: Verify task.json for each cron")
    print("─" * 70)

    await asyncio.sleep(5)

    for tc in test_crons:
        slug = tc["slug"]
        data = read_task_json(slug)
        if not data:
            possible = [
                d.name for d in CRONS_DIR.iterdir()
                if d.is_dir() and slug.split("-")[0] in d.name
            ]
            print(f"  {slug}: NOT FOUND (similar: {possible})")
            continue

        has_channel = bool(data.get("delivery_channel"))
        has_user = bool(data.get("requesting_user_id"))
        mode = data.get("delivery_mode", "channel")
        print(
            f"  {slug}: "
            f"channel={'Y' if has_channel else 'N'} "
            f"user={'Y' if has_user else 'N'} "
            f"mode={mode} "
            f"cron={data.get('cron', '?')}"
        )

    # ── Phase 4: Wait for crons to fire ──────────────────────────────
    print("\n" + "─" * 70)
    print("PHASE 4: Waiting for crons to fire (up to 5 minutes)...")
    print("─" * 70)

    channel_messages = await check_channel_for_bot_messages(
        pre_create_ts, timeout=300, min_messages=3,
    )

    print(f"\n  Found {len(channel_messages)} bot messages in channel:")
    for i, msg in enumerate(channel_messages, 1):
        preview = msg[:150].replace("\n", " | ")
        print(f"    [{i}] {preview}")

    # ── Phase 5: Analyze execution logs ──────────────────────────────
    print("\n" + "─" * 70)
    print("PHASE 5: Execution logs and learnings")
    print("─" * 70)

    for tc in test_crons:
        slug = tc["slug"]
        log = read_execution_log(slug)
        learn = read_learnings(slug)
        print(f"\n  ─── {tc['label']} ({slug}) ───")
        if log == "(no execution log)":
            alt_slugs = [
                d.name for d in CRONS_DIR.iterdir()
                if d.is_dir() and slug.split("-")[0] in d.name
            ]
            if alt_slugs:
                slug = alt_slugs[0]
                log = read_execution_log(slug)
                learn = read_learnings(slug)
                print(f"  (using alt slug: {slug})")

        print(f"  Execution log (last 400 chars):\n{log[-400:]}")
        if learn != "(no learnings)":
            print(f"  Learnings:\n{learn[:300]}")

    # ── Phase 6: Quality analysis ────────────────────────────────────
    print("\n" + "─" * 70)
    print("PHASE 6: Quality Analysis")
    print("─" * 70)

    results = {
        "crons_created": 0,
        "crons_fired": 0,
        "crons_delivered": 0,
        "has_personality": 0,
        "has_real_data": 0,
        "issues": [],
    }

    for tc in test_crons:
        slug = tc["slug"]
        task = read_task_json(slug)
        if not task:
            alt = [
                d.name for d in CRONS_DIR.iterdir()
                if d.is_dir() and slug.split("-")[0] in d.name
            ]
            slug = alt[0] if alt else slug
            task = read_task_json(slug)

        if task:
            results["crons_created"] += 1
        log = read_execution_log(slug)
        if "elapsed:" in log:
            results["crons_fired"] += 1
        if "delivered" in log:
            results["crons_delivered"] += 1
        if any(w in log.lower() for w in ["hey", "hi", "morning", "!"]):
            results["has_personality"] += 1
        if "SKIP" in log:
            results["issues"].append(f"{slug}: returned SKIP")
        if "FAILED" in log:
            results["issues"].append(f"{slug}: FAILED execution")

    print(f"  Created: {results['crons_created']}/{len(test_crons)}")
    print(f"  Fired:   {results['crons_fired']}/{len(test_crons)}")
    print(f"  Delivered: {results['crons_delivered']}/{len(test_crons)}")
    print(f"  Has personality: {results['has_personality']}/{len(test_crons)}")
    if results["issues"]:
        print(f"  Issues:")
        for issue in results["issues"]:
            print(f"    - {issue}")

    # ── Phase 7: Cleanup ─────────────────────────────────────────────
    print("\n" + "─" * 70)
    print("PHASE 7: Cleanup — deleting test crons")
    print("─" * 70)

    cleanup_ts = await send(
        f"<@{BOT_USER_ID}> Delete all cron jobs: btc-price, "
        "weather-check, github-stats, stretch-reminder, "
        "product-watch, motivation. Delete them all immediately."
    )
    cleanup_reply = await wait_reply(cleanup_ts, timeout=120)
    print(f"  Lucy: {(cleanup_reply or 'NO REPLY')[:200]}")

    print("\n" + "=" * 70)
    print("STRESS TEST V2 COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
