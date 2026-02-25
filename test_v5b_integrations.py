"""Lucy v5b Integration Workflow Test Suite - Iteration 2.

Tests the root-level fixes:
1. Per-tool-name call cap (prevents looping on same tool with varied params)
2. Clerk date filtering (created_after_unix_ms)
3. Cross-connector workflows with different prompts

Different prompts, same themes as v5 to avoid overfitting.
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
        print(f"  OK ({elapsed}s): {reply_text[:300]}")
        has_em_dash = "\u2014" in reply_text
        has_en_dash = "\u2013" in reply_text
        if has_em_dash:
            print(f"  !! EM DASH DETECTED")
        if has_en_dash:
            print(f"  !! EN DASH DETECTED")
        return {
            "label": label, "elapsed_s": elapsed,
            "response": reply_text, "thread_ts": msg_ts,
            "status": "ok",
            "has_em_dash": has_em_dash,
            "has_en_dash": has_en_dash,
        }
    else:
        print(f"  TIMEOUT after {elapsed}s")
        return {
            "label": label, "elapsed_s": elapsed,
            "response": None, "thread_ts": msg_ts,
            "status": "timeout",
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
    print(f"  OK Initial ({e1}s): {r1_text[:300]}")

    time.sleep(3)
    print(f"  > Follow-up: {followup[:140]}")
    t1 = time.time()
    fu_ts = send_thread_reply(msg_ts, followup)
    reply2 = wait_for_reply(msg_ts, fu_ts, timeout=timeout)
    e2 = round(time.time() - t1, 1)
    r2_text = (reply2 or {}).get("text", "")[:1500]
    if reply2:
        print(f"  OK Follow-up ({e2}s): {r2_text[:300]}")
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

    # ===== FIX VERIFICATION: Clerk date filtering =====
    print("\n" + "=" * 60)
    print("FIX VERIFICATION: Clerk date filtering + tool loop cap")
    print("=" * 60)

    results.append(run_test(
        "fix_clerk_recent_signups",
        "tell me how many new users registered in Clerk in the past week",
        timeout=120,
    ))

    results.append(run_test(
        "fix_clerk_today_signups",
        "did anyone sign up on Clerk today?",
        timeout=120,
    ))

    # ===== ROUND 1: Different single-connector tests =====
    print("\n" + "=" * 60)
    print("ROUND 1: Single-Connector Accuracy (Different Prompts)")
    print("=" * 60)

    results.append(run_test(
        "r1_gmail_specific",
        "do I have any emails from GitHub in my inbox? If so, what are they about?",
        timeout=180,
    ))

    results.append(run_test(
        "r1_sheets_list",
        "show me all my Google Sheets and tell me which one was updated most recently",
        timeout=120,
    ))

    results.append(run_test(
        "r1_github_prs",
        "are there any open pull requests in the Serprisingly repos on GitHub?",
        timeout=120,
    ))

    results.append(run_test(
        "r1_drive_search",
        "search my Google Drive for anything related to 'SEO' and list the top results",
        timeout=120,
    ))

    # ===== ROUND 2: Cross-connector with different phrasing =====
    print("\n" + "=" * 60)
    print("ROUND 2: Cross-Connector Workflows (Different Phrasing)")
    print("=" * 60)

    results.append(run_thread_test(
        "r2_github_to_summary",
        "what are the most active repositories in our GitHub org based on recent commits?",
        "create a Google Sheet summarizing the top 5 repos with their names, last commit date, and activity level",
        timeout=240,
    ))

    results.append(run_thread_test(
        "r2_clerk_to_email",
        "who was the last person to sign up on Clerk?",
        "draft a short welcome email for them and show it to me before sending",
        timeout=180,
    ))

    # ===== ROUND 3: Complex multi-tool tasks =====
    print("\n" + "=" * 60)
    print("ROUND 3: Complex Multi-Tool + Reasoning")
    print("=" * 60)

    results.append(run_test(
        "r3_weekly_digest",
        "create a weekly team digest: 1) count new Clerk signups this week 2) list any unread emails from today 3) find the most recent file in Google Drive. Format it as a clean report.",
        timeout=300,
    ))

    results.append(run_test(
        "r3_data_comparison",
        "compare the total number of Clerk users to the number of GitHub repos we have. Which number is bigger and by how much?",
        timeout=180,
    ))

    # ===== ROUND 4: Edge cases and error recovery =====
    print("\n" + "=" * 60)
    print("ROUND 4: Edge Cases & Smart Responses")
    print("=" * 60)

    results.append(run_test(
        "r4_partial_connector",
        "send an email to john@example.com saying 'hello from the team'",
        timeout=120,
    ))

    results.append(run_test(
        "r4_ambiguous_v2",
        "schedule a daily standup reminder for the engineering team at 9am every weekday",
        timeout=90,
    ))

    results.append(run_test(
        "r4_impossible_task",
        "deploy our latest code to Vercel production right now",
        timeout=90,
    ))

    # ===== RESULTS =====
    print("\n\n" + "=" * 60)
    print("FULL RESULTS SUMMARY")
    print("=" * 60)

    em_dash_count = 0
    en_dash_count = 0
    timeouts = 0
    for r in results:
        status = "OK" if r.get("status") == "ok" else "FAIL"
        if r.get("status") != "ok":
            timeouts += 1
        elapsed = r.get("elapsed_s") or r.get("initial_s", "?")
        resp = (r.get("response") or r.get("initial_response") or "")[:180]
        label = r["label"]
        flags = []
        if r.get("has_em_dash"):
            flags.append("EM_DASH")
            em_dash_count += 1
        if r.get("has_en_dash"):
            flags.append("EN_DASH")
            en_dash_count += 1
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        print(f"  [{status}] {label}: {elapsed}s{flag_str}")
        print(f"        {resp}")

    print(f"\n  --- Quality Metrics ---")
    print(f"  Total tests: {len(results)}")
    print(f"  Timeouts: {timeouts}")
    print(f"  Em dashes found: {em_dash_count}")
    print(f"  En dashes found: {en_dash_count}")

    with open("test_v5b_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to test_v5b_results.json")
