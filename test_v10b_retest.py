"""V10b retest: verify cost warning + multi-delete fix."""
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
    print(f"  > {text[:160]}", flush=True)
    t0 = time.time()
    msg_ts = send_mention(text)
    print(f"  Sent, waiting...", flush=True)
    reply = wait_for_reply(msg_ts, msg_ts, timeout=timeout)
    elapsed = round(time.time() - t0, 1)
    if reply:
        reply_text = reply.get("text", "")[:2000]
        print(f"  OK ({elapsed}s): {reply_text[:500]}", flush=True)
        return {
            "label": label, "status": "pass", "elapsed": elapsed,
            "reply": reply_text,
        }
    print(f"  TIMEOUT ({elapsed}s)", flush=True)
    return {"label": label, "status": "timeout", "elapsed": elapsed, "reply": ""}


def main():
    print("=== V10B RETEST ===", flush=True)
    results = []

    # Test 1: High-frequency cron should show cost warning
    results.append(run_test(
        "cost_warning_every_minute",
        "create a task that runs every minute to check if "
        "serprisingly.com is online",
    ))

    # Test 2: Create two crons, then delete both in one message
    results.append(run_test(
        "create_task_A",
        "create a daily 9am task called 'alpha-task' to say good morning",
    ))

    results.append(run_test(
        "create_task_B",
        "create a daily 6pm task called 'beta-task' to say good evening",
    ))

    results.append(run_test(
        "multi_delete",
        "delete alpha-task and beta-task",
    ))

    # Test 3: Verify everything is clean
    results.append(run_test(
        "verify_clean",
        "list all my scheduled tasks",
    ))

    # Cleanup the every-minute cron
    results.append(run_test(
        "cleanup_minute_cron",
        "delete the website health check task that runs every minute",
    ))

    print(f"\n{'='*60}", flush=True)
    print("RESULTS:", flush=True)
    for r in results:
        icon = "PASS" if r["status"] == "pass" else "FAIL"
        print(f"  [{icon}] {r['label']:30s} ({r['elapsed']}s)", flush=True)

    # Check for cost warning
    cost_test = next(
        (r for r in results if r["label"] == "cost_warning_every_minute"), None
    )
    if cost_test and cost_test["status"] == "pass":
        reply_lower = cost_test["reply"].lower()
        has_warning = any(w in reply_lower for w in [
            "token", "cost", "expensive", "frequent", "runs per day",
            "1440", "consider",
        ])
        print(
            f"\n  Cost warning present: {'YES' if has_warning else 'NO'}",
            flush=True,
        )

    # Check multi-delete
    multi_del = next(
        (r for r in results if r["label"] == "multi_delete"), None
    )
    if multi_del and multi_del["status"] == "pass":
        reply_lower = multi_del["reply"].lower()
        deleted_both = "alpha" in reply_lower and "beta" in reply_lower
        asked_confirm = "should i" in reply_lower or "want me to" in reply_lower
        print(
            f"  Multi-delete acted: {'YES' if deleted_both else 'NO'}",
            flush=True,
        )
        print(
            f"  Asked for unnecessary confirmation: "
            f"{'YES (bad)' if asked_confirm and not deleted_both else 'NO (good)'}",
            flush=True,
        )

    with open("test_v10b_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
    print("\nSaved to test_v10b_results.json", flush=True)


if __name__ == "__main__":
    main()
