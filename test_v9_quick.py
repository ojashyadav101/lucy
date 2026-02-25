"""Quick cron test - 3 basic operations to validate the system."""
from __future__ import annotations

import json
import sys
import time
import httpx
import certifi

KEYS = json.load(open("keys.json"))
BOT_TOKEN = KEYS["slack"]["bot_token"]
USER_TOKEN = KEYS["slack"]["user_token"]
CHANNEL = "C0AGNRMGALS"
BOT_USER_ID = "U0AG8LVAB4M"
SSL = certifi.where()

_PROGRESS_PHRASES = {
    "on it", "pulling that together", "working on", "hang tight",
    "still working", "looking into", "checking on", "making good headway",
    "bit longer than", "wrapping up", "thorough one", "moment",
    "almost have what you need", "let me", "give me a",
}


def _is_progress_msg(text: str) -> bool:
    lower = text.lower().strip()
    return any(p in lower for p in _PROGRESS_PHRASES) and len(lower) < 120


def send_mention(text: str) -> str:
    with httpx.Client(verify=SSL, timeout=15) as c:
        r = c.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {USER_TOKEN}"},
            json={"channel": CHANNEL, "text": f"<@{BOT_USER_ID}> {text}"},
        ).json()
        if not r.get("ok"):
            raise RuntimeError(f"Send failed: {r.get('error')}")
        return r["ts"]


def wait_for_reply(
    thread_ts: str, after_ts: str, timeout: int = 180
) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(5)
        with httpx.Client(verify=SSL, timeout=15) as c:
            r = c.get(
                "https://slack.com/api/conversations.replies",
                headers={"Authorization": f"Bearer {BOT_TOKEN}"},
                params={"channel": CHANNEL, "ts": thread_ts, "limit": 50},
            ).json()
        bot_msgs = [
            msg for msg in r.get("messages", [])
            if msg.get("user") == BOT_USER_ID and msg["ts"] > after_ts
        ]
        substantive = [
            m for m in bot_msgs if not _is_progress_msg(m.get("text", ""))
        ]
        if substantive:
            return substantive[-1]
        if bot_msgs and time.time() > deadline - 15:
            return bot_msgs[-1]
    return None


def run_test(label: str, text: str, timeout: int = 180) -> dict:
    print(f"\n{'='*60}", flush=True)
    print(f"TEST: {label}", flush=True)
    print(f"  > {text[:140]}", flush=True)
    t0 = time.time()
    msg_ts = send_mention(text)
    print(f"  Message sent (ts={msg_ts}), waiting...", flush=True)
    reply = wait_for_reply(msg_ts, msg_ts, timeout=timeout)
    elapsed = round(time.time() - t0, 1)
    if reply:
        reply_text = reply.get("text", "")[:2000]
        print(f"  OK ({elapsed}s): {reply_text[:400]}", flush=True)
        return {
            "label": label,
            "status": "pass",
            "elapsed": elapsed,
            "reply": reply_text,
        }
    print(f"  TIMEOUT ({elapsed}s)", flush=True)
    return {"label": label, "status": "timeout", "elapsed": elapsed, "reply": ""}


def main():
    print("=== QUICK CRON TEST ===", flush=True)
    results = []

    results.append(run_test(
        "create_cron",
        "set up a task that runs every day at 9am India time to check "
        "my Gmail inbox and send me a summary of unread emails here",
    ))

    results.append(run_test(
        "list_crons",
        "show me all my scheduled tasks",
    ))

    results.append(run_test(
        "delete_cron",
        "delete the Gmail summary task",
    ))

    print(f"\n{'='*60}", flush=True)
    print("RESULTS:", flush=True)
    for r in results:
        icon = "PASS" if r["status"] == "pass" else "FAIL"
        print(f"  [{icon}] {r['label']:25s} ({r['elapsed']}s)", flush=True)

    with open("test_v9_quick_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
    print("\nSaved to test_v9_quick_results.json", flush=True)


if __name__ == "__main__":
    main()
