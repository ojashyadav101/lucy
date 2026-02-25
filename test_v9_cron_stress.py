"""Lucy v9 Cron/Heartbeat Stress Test.

Tests:
1. Creating crons via natural language
2. Listing crons with descriptions
3. Modifying cron schedules
4. Deleting crons
5. Invalid cron expression handling
6. Cross-integration cron creation (monitor + notify)
7. Complex multi-step cron workflows
8. Concurrent cron operations
9. Timezone-aware scheduling
10. Triggering crons manually
"""
from __future__ import annotations

import json
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


def send_thread_reply(thread_ts: str, text: str) -> str:
    with httpx.Client(verify=SSL, timeout=15) as c:
        r = c.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {USER_TOKEN}"},
            json={
                "channel": CHANNEL,
                "text": text,
                "thread_ts": thread_ts,
            },
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
    print(f"\n{'='*60}")
    print(f"TEST: {label}")
    print(f"  > {text[:140]}")
    t0 = time.time()
    msg_ts = send_mention(text)
    reply = wait_for_reply(msg_ts, msg_ts, timeout=timeout)
    elapsed = round(time.time() - t0, 1)
    if reply:
        reply_text = reply.get("text", "")[:2000]
        print(f"  OK ({elapsed}s): {reply_text[:400]}")
        return {
            "label": label,
            "status": "pass",
            "elapsed": elapsed,
            "reply": reply_text,
            "thread_ts": msg_ts,
        }
    print(f"  TIMEOUT ({elapsed}s)")
    return {
        "label": label,
        "status": "timeout",
        "elapsed": elapsed,
        "reply": "",
        "thread_ts": msg_ts,
    }


def run_thread_test(
    label: str,
    messages: list[str],
    timeout: int = 180,
) -> dict:
    print(f"\n{'='*60}")
    print(f"TEST (threaded): {label}")
    t0 = time.time()

    thread_ts = send_mention(messages[0])
    print(f"  > {messages[0][:140]}")
    last_reply = wait_for_reply(thread_ts, thread_ts, timeout=timeout)

    all_replies = []
    if last_reply:
        all_replies.append(last_reply.get("text", "")[:2000])

    for follow_up in messages[1:]:
        print(f"  > {follow_up[:140]}")
        after_ts = send_thread_reply(thread_ts, follow_up)
        reply = wait_for_reply(thread_ts, after_ts, timeout=timeout)
        if reply:
            all_replies.append(reply.get("text", "")[:2000])
        else:
            all_replies.append("[TIMEOUT]")

    elapsed = round(time.time() - t0, 1)
    final_status = "pass" if "[TIMEOUT]" not in all_replies else "partial"
    combined = "\n---\n".join(all_replies)
    print(f"  {final_status.upper()} ({elapsed}s)")
    for i, r in enumerate(all_replies):
        print(f"  Reply {i+1}: {r[:300]}")

    return {
        "label": label,
        "status": final_status,
        "elapsed": elapsed,
        "reply": combined,
        "thread_ts": thread_ts,
    }


def main():
    results = []

    # ── Phase 1: Basic cron creation ─────────────────────────────────
    results.append(run_test(
        "create_simple_cron",
        "set up a daily task that runs at 9am IST (India time) every weekday "
        "to check my unread Gmail and give me a summary in this channel",
    ))

    results.append(run_test(
        "create_monitoring_cron",
        "I want you to check every 15 minutes if any new GitHub issues "
        "have been opened on our repo and notify me here",
    ))

    # ── Phase 2: List and verify ─────────────────────────────────────
    results.append(run_test(
        "list_crons_detailed",
        "show me all my scheduled tasks with their schedules and what "
        "each one does",
    ))

    # ── Phase 3: Modify existing cron ────────────────────────────────
    results.append(run_test(
        "modify_cron_schedule",
        "change the Gmail summary task to run at 8:30am instead of 9am",
    ))

    # ── Phase 4: Invalid expression ──────────────────────────────────
    results.append(run_test(
        "invalid_cron_handling",
        "set up a task with schedule 'every banana' to check the weather",
    ))

    # ── Phase 5: Complex cross-integration cron ──────────────────────
    results.append(run_test(
        "cross_integration_cron",
        "create a weekly Monday 10am task that: 1) pulls our latest "
        "GitHub repo stats, 2) checks how many new Clerk signups we "
        "had last week, 3) sends a summary to this channel with both "
        "numbers formatted nicely",
    ))

    # ── Phase 6: Timezone-aware cron ─────────────────────────────────
    results.append(run_test(
        "timezone_cron",
        "schedule a task for 6pm New York time every Friday to send "
        "a weekly wrap-up message to the team in this channel",
    ))

    # ── Phase 7: Stock/product monitoring style ──────────────────────
    results.append(run_test(
        "monitoring_heartbeat",
        "I want to track the price of Bitcoin. Set up a check every "
        "30 minutes and only notify me if the price goes above $100,000 "
        "or drops below $80,000. Use web search to get the current price.",
    ))

    # ── Phase 8: Delete a cron ───────────────────────────────────────
    results.append(run_test(
        "delete_cron",
        "remove the GitHub issues monitoring task I set up earlier",
    ))

    # ── Phase 9: List after deletion ─────────────────────────────────
    results.append(run_test(
        "verify_deletion",
        "list all my scheduled tasks again, I want to confirm the "
        "GitHub issues monitor was removed",
    ))

    # ── Phase 10: Threaded cron conversation ─────────────────────────
    results.append(run_thread_test(
        "threaded_cron_modify",
        [
            "set up a task to check Google Drive for any files shared "
            "with me in the last hour, run it every 2 hours",
            "actually make it every 3 hours instead",
            "and add a timezone of Asia/Kolkata",
        ],
    ))

    # ── Phase 11: Multiple crons in one message ──────────────────────
    results.append(run_test(
        "multi_cron_create",
        "I need three tasks set up: "
        "1) every day at 9am IST check my unread emails, "
        "2) every Monday at 10am check open GitHub PRs, "
        "3) every 6 hours check if any new users signed up on Clerk",
    ))

    # ── Phase 12: Rapid fire cron operations ─────────────────────────
    print(f"\n{'='*60}")
    print("TEST: rapid_cron_ops (back-to-back create + delete)")
    t0 = time.time()
    ts1 = send_mention(
        "create a task called 'temp-test-task' that runs every hour "
        "to say hello in this channel"
    )
    time.sleep(2)
    ts2 = send_mention("now delete the temp-test-task immediately")

    reply1 = wait_for_reply(ts1, ts1, timeout=120)
    reply2 = wait_for_reply(ts2, ts2, timeout=120)
    elapsed = round(time.time() - t0, 1)

    r1_text = reply1.get("text", "")[:1000] if reply1 else "[TIMEOUT]"
    r2_text = reply2.get("text", "")[:1000] if reply2 else "[TIMEOUT]"
    status = (
        "pass" if reply1 and reply2 and "TIMEOUT" not in r1_text
        else "partial"
    )
    print(f"  {status.upper()} ({elapsed}s)")
    print(f"  Create: {r1_text[:300]}")
    print(f"  Delete: {r2_text[:300]}")
    results.append({
        "label": "rapid_cron_ops",
        "status": status,
        "elapsed": elapsed,
        "reply": f"Create: {r1_text}\n---\nDelete: {r2_text}",
    })

    # ── Results Summary ──────────────────────────────────────────────
    print(f"\n\n{'='*60}")
    print("CRON STRESS TEST RESULTS")
    print(f"{'='*60}")

    passed = sum(1 for r in results if r["status"] == "pass")
    partial = sum(1 for r in results if r["status"] == "partial")
    failed = sum(1 for r in results if r["status"] == "timeout")
    total = len(results)

    for r in results:
        icon = {
            "pass": "PASS",
            "partial": "PART",
            "timeout": "FAIL",
        }.get(r["status"], "????")
        print(f"  [{icon}] {r['label']:30s} ({r['elapsed']}s)")

    print(f"\nTotal: {total} | Pass: {passed} | Partial: {partial}"
          f" | Timeout: {failed}")

    avg_time = (
        round(
            sum(r["elapsed"] for r in results if r["status"] != "timeout")
            / max(1, passed + partial),
            1,
        )
    )
    print(f"Avg response time (non-timeout): {avg_time}s")

    with open("test_v9_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
    print("\nResults saved to test_v9_results.json")


if __name__ == "__main__":
    main()
