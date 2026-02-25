"""Lucy v6 Cross-Connector Stress Test.

Tests cross-connector workflows, search-before-ask behavior,
data compaction, and multi-tool aggregation with NEW prompts
(different from v5d to avoid overfitting).

Phases:
1. Single connector (varied prompts)
2. Cross-connector thread workflows
3. Multi-tool stress (single message, multiple integrations)
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
        reply_text = reply.get("text", "")[:2000]
        print(f"  OK ({elapsed}s): {reply_text[:400]}")
        has_em = "\u2014" in reply_text
        has_en = "\u2013" in reply_text
        if has_em:
            print("  !! EM DASH")
        if has_en:
            print("  !! EN DASH")
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
    label: str, initial: str, followup: str,
    timeout_initial: int = 180, timeout_followup: int = 180,
) -> dict:
    print(f"\n{'='*60}")
    print(f"THREAD: {label}")
    print(f"  > Initial: {initial[:140]}")
    t0 = time.time()
    msg_ts = send_mention(initial)
    reply1 = wait_for_reply(msg_ts, msg_ts, timeout=timeout_initial)
    if not reply1:
        print(f"  TIMEOUT on initial")
        return {"label": label, "status": "timeout_initial", "thread_ts": msg_ts}

    e1 = round(time.time() - t0, 1)
    r1_text = reply1["text"][:2000]
    print(f"  OK Initial ({e1}s): {r1_text[:400]}")

    time.sleep(3)
    print(f"  > Follow-up: {followup[:140]}")
    t1 = time.time()
    fu_ts = send_thread_reply(msg_ts, followup)
    reply2 = wait_for_reply(msg_ts, fu_ts, timeout=timeout_followup)
    e2 = round(time.time() - t1, 1)
    r2_text = (reply2 or {}).get("text", "")[:2000]
    if reply2:
        print(f"  OK Follow-up ({e2}s): {r2_text[:400]}")
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

    # ═══════════════════════════════════════════════════
    # PHASE 1: Single Connector (different prompts)
    # ═══════════════════════════════════════════════════
    print("\n" + "=" * 60)
    print("PHASE 1: SINGLE CONNECTOR BASICS")
    print("=" * 60)

    results.append(run_test(
        "clerk_oldest_users",
        "what's our total user count on Clerk and who were the first 5 people to ever sign up?",
        timeout=150,
    ))

    results.append(run_test(
        "gmail_github_emails",
        "check if I got any emails from GitHub notifications in the past week",
        timeout=120,
    ))

    results.append(run_test(
        "sheets_search_by_name",
        "find the Google Sheet called 'Contact Login + Tool Info' and tell me what data is in it",
        timeout=120,
    ))

    # ═══════════════════════════════════════════════════
    # PHASE 2: Cross-Connector Thread Workflows
    # ═══════════════════════════════════════════════════
    print("\n" + "=" * 60)
    print("PHASE 2: CROSS-CONNECTOR WORKFLOWS")
    print("=" * 60)

    results.append(run_thread_test(
        "gmail_to_sheets",
        "find all emails from Razorpay in the last 2 weeks and list the payment amounts",
        "export those payment amounts to a new Google Sheet with columns: Date, Amount, Payment ID",
        timeout_initial=150,
        timeout_followup=180,
    ))

    results.append(run_thread_test(
        "clerk_to_gmail",
        "show me the 5 newest Clerk users with their emails",
        "draft a welcome email to the newest one introducing our team",
        timeout_initial=150,
        timeout_followup=120,
    ))

    # ═══════════════════════════════════════════════════
    # PHASE 3: Multi-Tool Stress Tests
    # ═══════════════════════════════════════════════════
    print("\n" + "=" * 60)
    print("PHASE 3: MULTI-TOOL STRESS")
    print("=" * 60)

    results.append(run_test(
        "multi_tool_dashboard",
        "create a Google Sheet called 'Company Dashboard' with 3 rows: "
        "Row 1: Total Clerk Users with the count, "
        "Row 2: Unread Emails with the count, "
        "Row 3: Google Sheets Files with the count",
        timeout=240,
    ))

    results.append(run_test(
        "github_plus_sheets",
        "list the 3 most recently updated repos in our GitHub org and put them in a Google Sheet with columns: Repo Name, Last Updated, Stars",
        timeout=240,
    ))

    results.append(run_test(
        "ambiguous_clarify",
        "update the document with the latest numbers",
        timeout=60,
    ))

    # ═══════════════════════════════════════════════════
    # RESULTS SUMMARY
    # ═══════════════════════════════════════════════════
    print("\n\n" + "=" * 60)
    print("V6 RESULTS SUMMARY")
    print("=" * 60)

    em_count = 0
    en_count = 0
    timeouts = 0
    progress_captured = 0
    for r in results:
        status = "OK" if r.get("status") == "ok" else "FAIL"
        if r.get("status") != "ok":
            timeouts += 1
        elapsed = r.get("elapsed_s") or r.get("initial_s", "?")
        resp = (r.get("response") or r.get("initial_response") or "")[:250]
        label = r["label"]
        flags = []
        if r.get("has_em_dash"):
            flags.append("EM")
            em_count += 1
        if r.get("has_en_dash"):
            flags.append("EN")
            en_count += 1
        is_prog = _is_progress_msg(resp) if resp else False
        if is_prog:
            flags.append("PROGRESS")
            progress_captured += 1
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        print(f"  [{status}] {label}: {elapsed}s{flag_str}")
        print(f"        {resp[:250]}")

    print(f"\n  --- Quality Metrics ---")
    print(f"  Total tests: {len(results)}")
    print(f"  Timeouts: {timeouts}")
    print(f"  Em dashes: {em_count}")
    print(f"  En dashes: {en_count}")
    print(f"  Progress captured: {progress_captured}")

    with open("test_v6_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to test_v6_results.json")
