"""Test that cron results are delivered to Slack."""
from __future__ import annotations

import asyncio
import json
import time
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
    "working on",
    "let me",
    "looking into",
    "give me a moment",
    "processing",
    "one moment",
    "on it",
    "i'll",
    "checking",
]


def _is_progress(text: str) -> bool:
    low = text.lower().strip()
    return any(m in low for m in _PROGRESS_MARKERS) and len(low) < 120


async def send(text: str, thread: str | None = None) -> str:
    """Send as the USER (not the bot)."""
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


async def check_channel_messages(
    after_ts: str, timeout: int = 180,
) -> str | None:
    """Look for a NEW message from the bot in the channel (not in a thread)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        async with httpx.AsyncClient(verify=SSL) as c:
            r = await c.get(
                "https://slack.com/api/conversations.history",
                headers=BOT_HEADERS,
                params={
                    "channel": CHANNEL,
                    "oldest": after_ts,
                    "limit": 20,
                },
            )
        msgs = r.json().get("messages", [])
        for m in msgs:
            is_bot = m.get("user") == BOT_USER_ID or m.get("bot_id")
            if not is_bot:
                continue
            if not m.get("thread_ts") or m.get("thread_ts") == m.get("ts"):
                return m.get("text", "")
        await asyncio.sleep(8)
    return None


async def main() -> None:
    print("=" * 60)
    print("CRON DELIVERY TEST")
    print("=" * 60)

    # Step 1: Ask Lucy to create a cron that should run in ~2 mins
    print("\n[1] Asking Lucy to create a test cron...")
    ts = await send(
        f"<@{BOT_USER_ID}> Create a cron job called 'delivery-test' that "
        "runs every 2 minutes. The task should just say "
        "'Hello from a cron job! The current time is [time].' "
        "Use Asia/Kolkata timezone."
    )
    reply = await wait_reply(ts, timeout=120)
    print(f"    Lucy reply: {(reply or 'NO REPLY')[:200]}")

    if not reply:
        print("    FAIL: No reply from Lucy")
        return

    # Step 2: Check that task.json has delivery_channel
    print("\n[2] Checking task.json for delivery_channel...")
    task_file = CRONS_DIR / "delivery-test" / "task.json"
    retries = 5
    found = False
    for _ in range(retries):
        if task_file.exists():
            data = json.loads(task_file.read_text())
            print(f"    task.json contents: {json.dumps(data, indent=2)}")
            if data.get("delivery_channel"):
                print(f"    PASS: delivery_channel = {data['delivery_channel']}")
                found = True
            else:
                print("    FAIL: delivery_channel is missing!")
            break
        await asyncio.sleep(3)

    if not found and not task_file.exists():
        print(f"    FAIL: task.json not found at {task_file}")

    # Step 3: Trigger the cron manually and watch for delivery
    print("\n[3] Triggering cron manually...")
    before_trigger = str(time.time())
    ts2 = await send(
        f"<@{BOT_USER_ID}> trigger the delivery-test cron right now",
        thread=ts,
    )
    reply2 = await wait_reply(ts, timeout=120, after_ts=ts2)
    print(f"    Lucy trigger reply: {(reply2 or 'NO REPLY')[:200]}")

    # Step 4: Check if a message appeared in the channel from the cron
    print("\n[4] Checking channel for cron delivery...")
    delivered = await check_channel_messages(before_trigger, timeout=120)
    if delivered:
        print(f"    PASS: Cron result delivered to channel!")
        print(f"    Message: {delivered[:200]}")
    else:
        print("    FAIL: No cron delivery message found in channel")

    # Step 5: Also check execution.log
    print("\n[5] Checking execution.log...")
    log_file = CRONS_DIR / "delivery-test" / "execution.log"
    if log_file.exists():
        print(f"    Log contents:\n{log_file.read_text()[-500:]}")
    else:
        print("    No execution.log yet")

    # Cleanup: Delete the test cron
    print("\n[6] Cleaning up...")
    ts3 = await send(
        f"<@{BOT_USER_ID}> delete the delivery-test cron",
        thread=ts,
    )
    cleanup_reply = await wait_reply(ts, timeout=60, after_ts=ts3)
    print(f"    Cleanup: {(cleanup_reply or 'NO REPLY')[:100]}")

    print("\n" + "=" * 60)
    print("TEST COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
