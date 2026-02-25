"""Lucy v10 — Edge cases and untested cron scenarios.

Phase 1: Edge cases (trigger_now, bad timezone, wrong name, duplicate)
Phase 2: Real integration cron execution (create + trigger + verify output)
Phase 3: Concurrent and complex operations
"""
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
            "reply": reply_text, "thread_ts": msg_ts,
        }
    print(f"  TIMEOUT ({elapsed}s)", flush=True)
    return {
        "label": label, "status": "timeout", "elapsed": elapsed,
        "reply": "", "thread_ts": msg_ts,
    }


def run_thread_test(
    label: str, messages: list[str], timeout: int = 180,
) -> dict:
    print(f"\n{'='*60}", flush=True)
    print(f"TEST (threaded): {label}", flush=True)
    t0 = time.time()

    thread_ts = send_mention(messages[0])
    print(f"  > {messages[0][:160]}", flush=True)
    last_reply = wait_for_reply(thread_ts, thread_ts, timeout=timeout)

    all_replies = []
    if last_reply:
        all_replies.append(last_reply.get("text", "")[:2000])
    else:
        all_replies.append("[TIMEOUT]")

    for follow_up in messages[1:]:
        print(f"  > {follow_up[:160]}", flush=True)
        after_ts = send_thread_reply(thread_ts, follow_up)
        reply = wait_for_reply(thread_ts, after_ts, timeout=timeout)
        if reply:
            all_replies.append(reply.get("text", "")[:2000])
        else:
            all_replies.append("[TIMEOUT]")

    elapsed = round(time.time() - t0, 1)
    final_status = "pass" if "[TIMEOUT]" not in all_replies else "partial"
    combined = "\n---\n".join(all_replies)
    print(f"  {final_status.upper()} ({elapsed}s)", flush=True)
    for i, r in enumerate(all_replies):
        print(f"  Reply {i+1}: {r[:400]}", flush=True)

    return {
        "label": label, "status": final_status, "elapsed": elapsed,
        "reply": combined, "thread_ts": thread_ts,
    }


def main():
    results = []

    # ═══════════════════════════════════════════════════════════════
    # PHASE 1: Edge Cases
    # ═══════════════════════════════════════════════════════════════
    print("\n" + "="*60, flush=True)
    print("PHASE 1: EDGE CASES", flush=True)
    print("="*60, flush=True)

    # 1. Trigger an existing cron manually
    results.append(run_test(
        "trigger_now",
        "run the heartbeat task right now, I want to see what it does",
        timeout=240,
    ))

    # 2. Delete a non-existent cron (should list alternatives)
    results.append(run_test(
        "delete_nonexistent",
        "delete the task called 'banana-checker'",
    ))

    # 3. Modify a non-existent cron (should list alternatives)
    results.append(run_test(
        "modify_nonexistent",
        "change the 'rocket-launcher' task to run every 5 minutes",
    ))

    # 4. Create a cron with very short interval (should warn about cost)
    results.append(run_test(
        "short_interval_warning",
        "set up a task that runs every 1 minute to check if our "
        "website serprisingly.com is still online",
    ))

    # 5. Modify just the description of a cron (not the schedule)
    results.append(run_thread_test(
        "modify_description",
        [
            "create a daily 9am IST task called 'team-digest' that "
            "summarizes the top Slack messages from yesterday",
            "keep the same schedule but change it to also include "
            "the count of GitHub commits from yesterday",
        ],
    ))

    # ═══════════════════════════════════════════════════════════════
    # PHASE 2: Real Integration Execution
    # ═══════════════════════════════════════════════════════════════
    print("\n" + "="*60, flush=True)
    print("PHASE 2: REAL INTEGRATION EXECUTION", flush=True)
    print("="*60, flush=True)

    # 6. Create a cron, trigger it, see real output
    results.append(run_thread_test(
        "create_trigger_verify",
        [
            "create a task called 'clerk-user-count' that checks "
            "how many total users we have in Clerk and posts the "
            "count here. Set it to run every 6 hours.",
            "now trigger that task immediately so I can see what it does",
        ],
        timeout=240,
    ))

    # ═══════════════════════════════════════════════════════════════
    # PHASE 3: Complex Operations
    # ═══════════════════════════════════════════════════════════════
    print("\n" + "="*60, flush=True)
    print("PHASE 3: COMPLEX OPERATIONS", flush=True)
    print("="*60, flush=True)

    # 7. Create + modify + trigger in a single thread
    results.append(run_thread_test(
        "lifecycle_thread",
        [
            "create a task called 'github-pr-check' that runs every "
            "Monday at 10am IST to check for open PRs on our GitHub repos",
            "change it to Tuesday and Thursday instead of just Monday",
            "trigger it now to test",
        ],
        timeout=240,
    ))

    # 8. Concurrent: create two different crons back-to-back
    print(f"\n{'='*60}", flush=True)
    print("TEST: concurrent_create", flush=True)
    t0 = time.time()
    ts1 = send_mention(
        "create a task called 'morning-news' that runs daily at 8am "
        "IST to do a web search for tech news and post top 3 headlines"
    )
    print("  > Sent message 1 (morning-news)", flush=True)
    time.sleep(2)
    ts2 = send_mention(
        "create a task called 'evening-summary' that runs daily at "
        "6pm IST to summarize today's Slack activity"
    )
    print("  > Sent message 2 (evening-summary)", flush=True)

    reply1 = wait_for_reply(ts1, ts1, timeout=120)
    reply2 = wait_for_reply(ts2, ts2, timeout=120)
    elapsed = round(time.time() - t0, 1)

    r1_text = reply1.get("text", "")[:1000] if reply1 else "[TIMEOUT]"
    r2_text = reply2.get("text", "")[:1000] if reply2 else "[TIMEOUT]"
    status = "pass" if reply1 and reply2 else "partial"
    print(f"  {status.upper()} ({elapsed}s)", flush=True)
    print(f"  Reply 1: {r1_text[:400]}", flush=True)
    print(f"  Reply 2: {r2_text[:400]}", flush=True)
    results.append({
        "label": "concurrent_create",
        "status": status,
        "elapsed": elapsed,
        "reply": f"Morning: {r1_text}\n---\nEvening: {r2_text}",
    })

    # 9. Final cleanup: list all and delete test crons
    results.append(run_test(
        "final_list",
        "list all my scheduled tasks",
    ))

    results.append(run_test(
        "cleanup_delete",
        "delete the following tasks: team-digest, clerk-user-count, "
        "github-pr-check, morning-news, evening-summary",
    ))

    results.append(run_test(
        "verify_cleanup",
        "list my tasks one more time to confirm everything is clean",
    ))

    # ═══════════════════════════════════════════════════════════════
    # RESULTS
    # ═══════════════════════════════════════════════════════════════
    print(f"\n\n{'='*60}", flush=True)
    print("V10 EDGE CASE TEST RESULTS", flush=True)
    print(f"{'='*60}", flush=True)

    passed = sum(1 for r in results if r["status"] == "pass")
    partial = sum(1 for r in results if r["status"] == "partial")
    failed = sum(1 for r in results if r["status"] == "timeout")
    total = len(results)

    for r in results:
        icon = {"pass": "PASS", "partial": "PART", "timeout": "FAIL"}.get(
            r["status"], "????"
        )
        print(f"  [{icon}] {r['label']:30s} ({r['elapsed']}s)", flush=True)

    print(f"\nTotal: {total} | Pass: {passed} | Partial: {partial}"
          f" | Timeout: {failed}", flush=True)

    non_timeout = [r for r in results if r["status"] != "timeout"]
    if non_timeout:
        avg = round(sum(r["elapsed"] for r in non_timeout) / len(non_timeout), 1)
        print(f"Avg response time (non-timeout): {avg}s", flush=True)

    with open("test_v10_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
    print("\nResults saved to test_v10_results.json", flush=True)


if __name__ == "__main__":
    main()
