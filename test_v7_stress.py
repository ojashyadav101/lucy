"""Lucy v7 Stress Test — Increased Complexity.

Tests:
1. Retry: multi-tool dashboard (previously failed due to BASH cap)
2. Complex sequential: gather data → analyze → create output
3. Cross-connector with 3+ services in one workflow
4. Edge cases: conflicting info, very specific requests
5. Concurrent-style: rapid back-to-back messages
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
    "almost have what you need",
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
    # 1. RETRY: Multi-tool dashboard (previously failed)
    # ═══════════════════════════════════════════════════
    print("\n" + "=" * 60)
    print("1. RETRY: MULTI-TOOL DASHBOARD")
    print("=" * 60)

    results.append(run_test(
        "dashboard_retry",
        "pull the total Clerk user count, count of unread emails, and number of Google Sheets files, then put all three into a new spreadsheet called 'Quick Stats'",
        timeout=300,
    ))

    # ═══════════════════════════════════════════════════
    # 2. THREE-SERVICE WORKFLOW (Clerk → Gmail → Sheets)
    # ═══════════════════════════════════════════════════
    print("\n" + "=" * 60)
    print("2. THREE-SERVICE SEQUENTIAL WORKFLOW")
    print("=" * 60)

    results.append(run_thread_test(
        "clerk_gmail_sheets",
        "find our top 3 Clerk users by signup date (oldest accounts) and check if any of them have emailed us recently",
        "save whatever you found into a Google Sheet called 'VIP Users Report'",
        timeout_initial=180,
        timeout_followup=180,
    ))

    # ═══════════════════════════════════════════════════
    # 3. SPECIFIC DATA EXTRACTION + FORMATTING
    # ═══════════════════════════════════════════════════
    print("\n" + "=" * 60)
    print("3. SPECIFIC DATA EXTRACTION")
    print("=" * 60)

    results.append(run_test(
        "clerk_breakdown",
        "give me a breakdown of Clerk signups by authentication method - how many used Google OAuth vs email/password?",
        timeout=150,
    ))

    results.append(run_test(
        "gmail_specific_search",
        "find any emails with the word 'invoice' in the subject line from this month and list the senders",
        timeout=120,
    ))

    # ═══════════════════════════════════════════════════
    # 4. EDGE CASES
    # ═══════════════════════════════════════════════════
    print("\n" + "=" * 60)
    print("4. EDGE CASES")
    print("=" * 60)

    results.append(run_test(
        "nonexistent_sheet",
        "open the Google Sheet called 'Q4 Revenue Projections 2027' and tell me the totals",
        timeout=90,
    ))

    results.append(run_test(
        "impossible_data",
        "how many Clerk users signed up exactly 3 hours ago?",
        timeout=120,
    ))

    # ═══════════════════════════════════════════════════
    # 5. RAPID BACK-TO-BACK (stress concurrency)
    # ═══════════════════════════════════════════════════
    print("\n" + "=" * 60)
    print("5. RAPID BACK-TO-BACK")
    print("=" * 60)

    t0 = time.time()
    ts1 = send_mention("what's the latest Razorpay payment amount?")
    time.sleep(2)
    ts2 = send_mention("how many repos do we have on GitHub?")
    print(f"  Sent 2 messages 2s apart")
    r1 = wait_for_reply(ts1, ts1, timeout=150)
    r2 = wait_for_reply(ts2, ts2, timeout=150)
    e_total = round(time.time() - t0, 1)

    r1_text = (r1 or {}).get("text", "")[:2000]
    r2_text = (r2 or {}).get("text", "")[:2000]

    print(f"  Reply 1 ({e_total}s): {r1_text[:300]}")
    print(f"  Reply 2: {r2_text[:300]}")

    results.append({
        "label": "rapid_razorpay", "elapsed_s": e_total,
        "response": r1_text, "thread_ts": ts1,
        "status": "ok" if r1 else "timeout",
        "has_em_dash": "\u2014" in r1_text,
        "has_en_dash": "\u2013" in r1_text,
    })
    results.append({
        "label": "rapid_github", "elapsed_s": e_total,
        "response": r2_text, "thread_ts": ts2,
        "status": "ok" if r2 else "timeout",
        "has_em_dash": "\u2014" in r2_text,
        "has_en_dash": "\u2013" in r2_text,
    })

    # ═══════════════════════════════════════════════════
    # RESULTS SUMMARY
    # ═══════════════════════════════════════════════════
    print("\n\n" + "=" * 60)
    print("V7 RESULTS SUMMARY")
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

    with open("test_v7_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to test_v7_results.json")
