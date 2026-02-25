"""Lucy v5c Verification Test Suite - Iteration 3.

Focused verification of root-level fixes:
1. Force-stop for tool looping (COMPOSIO_REMOTE_BASH_TOOL no longer loops)
2. Clerk date filtering (created_after_unix_ms works)
3. GitHub org auto-detection from company knowledge
4. Cross-connector workflow speed improvements
5. Error handling for impossible tasks
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
        reply_text = reply.get("text", "")[:1500]
        print(f"  OK ({elapsed}s): {reply_text[:350]}")
        has_em = "\u2014" in reply_text
        has_en = "\u2013" in reply_text
        if has_em: print("  !! EM DASH")
        if has_en: print("  !! EN DASH")
        return {
            "label": label, "elapsed_s": elapsed,
            "response": reply_text, "thread_ts": msg_ts,
            "status": "ok", "has_em_dash": has_em, "has_en_dash": has_en,
        }
    else:
        print(f"  TIMEOUT after {elapsed}s")
        return {
            "label": label, "elapsed_s": elapsed,
            "response": None, "thread_ts": msg_ts, "status": "timeout",
        }


def run_thread_test(
    label: str, initial: str, followup: str, timeout: int = 180
) -> dict:
    print(f"\n{'='*60}")
    print(f"THREAD: {label}")
    print(f"  > Initial: {initial[:140]}")
    t0 = time.time()
    msg_ts = send_mention(initial)
    reply1 = wait_for_reply(msg_ts, msg_ts, timeout=timeout)
    if not reply1:
        print(f"  TIMEOUT on initial")
        return {"label": label, "status": "timeout_initial"}

    e1 = round(time.time() - t0, 1)
    r1_text = reply1["text"][:1500]
    print(f"  OK Initial ({e1}s): {r1_text[:350]}")

    time.sleep(3)
    print(f"  > Follow-up: {followup[:140]}")
    t1 = time.time()
    fu_ts = send_thread_reply(msg_ts, followup)
    reply2 = wait_for_reply(msg_ts, fu_ts, timeout=timeout)
    e2 = round(time.time() - t1, 1)
    r2_text = (reply2 or {}).get("text", "")[:1500]
    if reply2:
        print(f"  OK Follow-up ({e2}s): {r2_text[:350]}")
    else:
        print(f"  TIMEOUT on follow-up")

    return {
        "label": label, "initial_s": e1, "followup_s": e2,
        "initial_response": r1_text,
        "followup_response": r2_text,
        "thread_ts": msg_ts,
        "status": "ok" if reply2 else "timeout_followup",
    }


if __name__ == "__main__":
    results = []

    # ===== FIX 1: Force-stop verification =====
    print("\n" + "=" * 60)
    print("FIX 1: Force-stop for impossible/looping tasks")
    print("=" * 60)

    results.append(run_test(
        "force_stop_deploy",
        "push the latest code changes to our Vercel production deployment",
        timeout=120,
    ))

    results.append(run_test(
        "force_stop_cron",
        "set up a daily 9am reminder in Slack to check my email",
        timeout=120,
    ))

    # ===== FIX 2: Clerk date filtering =====
    print("\n" + "=" * 60)
    print("FIX 2: Clerk date filtering (should be fast now)")
    print("=" * 60)

    results.append(run_test(
        "clerk_weekly",
        "how many users registered on Clerk since last Monday?",
        timeout=120,
    ))

    results.append(run_test(
        "clerk_newest",
        "who are the 3 most recent Clerk signups?",
        timeout=90,
    ))

    # ===== FIX 3: GitHub org auto-detection =====
    print("\n" + "=" * 60)
    print("FIX 3: GitHub org context (should use Serprisingly automatically)")
    print("=" * 60)

    results.append(run_test(
        "github_org_auto",
        "list the top 5 most recently updated repos in our GitHub organization",
        timeout=120,
    ))

    # ===== ROUND: Cross-service workflows =====
    print("\n" + "=" * 60)
    print("ROUND: Cross-Service Workflows")
    print("=" * 60)

    results.append(run_thread_test(
        "cross_gmail_drive",
        "find the most recent email from a service I use and summarize it",
        "save that summary as a note in Google Drive",
        timeout=240,
    ))

    results.append(run_test(
        "cross_multi_report",
        "give me a quick daily snapshot: how many Clerk users total, any important unread emails, and the latest file in my Drive",
        timeout=240,
    ))

    results.append(run_test(
        "cross_clerk_github",
        "compare our Clerk signups from the past week with the number of GitHub repos we have, and tell me which is growing faster",
        timeout=180,
    ))

    # ===== QUALITY: Response quality checks =====
    print("\n" + "=" * 60)
    print("QUALITY: Response quality and tone checks")
    print("=" * 60)

    results.append(run_test(
        "quality_email_confirm",
        "send a follow-up email to our newest Clerk user",
        timeout=90,
    ))

    results.append(run_test(
        "quality_error_recovery",
        "check my Trello board for overdue tasks",
        timeout=60,
    ))

    # ===== RESULTS =====
    print("\n\n" + "=" * 60)
    print("FULL RESULTS SUMMARY")
    print("=" * 60)

    em_count = 0
    en_count = 0
    timeouts = 0
    slow_tests = []
    for r in results:
        status = "OK" if r.get("status") == "ok" else "FAIL"
        if r.get("status") != "ok":
            timeouts += 1
        elapsed = r.get("elapsed_s") or r.get("initial_s", "?")
        if isinstance(elapsed, (int, float)) and elapsed > 90:
            slow_tests.append(r["label"])
        resp = (r.get("response") or r.get("initial_response") or "")[:200]
        label = r["label"]
        flags = []
        if r.get("has_em_dash"):
            flags.append("EM")
            em_count += 1
        if r.get("has_en_dash"):
            flags.append("EN")
            en_count += 1
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        is_progress = _is_progress_msg(resp) if resp else False
        prog_str = " [PROGRESS_MSG]" if is_progress else ""
        print(f"  [{status}] {label}: {elapsed}s{flag_str}{prog_str}")
        print(f"        {resp[:200]}")

    print(f"\n  --- Quality Metrics ---")
    print(f"  Total tests: {len(results)}")
    print(f"  Timeouts: {timeouts}")
    print(f"  Em dashes: {em_count}")
    print(f"  En dashes: {en_count}")
    print(f"  Slow (>90s): {len(slow_tests)} - {slow_tests}")

    with open("test_v5c_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to test_v5c_results.json")
