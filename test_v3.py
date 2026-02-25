"""Lucy v3 Stress Test Suite — higher intensity, integration-heavy.

Phases:
  1. Formatting & AI-tell checks (em dashes, TLDR-first, numbered lists)
  2. Integration tests (Gmail, Drive, Sheets, GitHub, Vercel)
  3. Multi-step workflows (sequential tool chains)
  4. Complex requests & edge cases
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


_PROGRESS_PHRASES = {
    "on it", "pulling that together", "working on", "hang tight",
    "still working", "looking into", "checking on",
}


def _is_progress_msg(text: str) -> bool:
    lower = text.lower().strip()
    return any(p in lower for p in _PROGRESS_PHRASES) and len(lower) < 80


def wait_for_reply(thread_ts: str, after_ts: str, timeout: int = 120) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(4)
        with httpx.Client(verify=SSL, timeout=15) as c:
            r = c.get(
                "https://slack.com/api/conversations.replies",
                headers={"Authorization": f"Bearer {BOT_TOKEN}"},
                params={"channel": CHANNEL, "ts": thread_ts, "limit": 20},
            ).json()
        bot_msgs = [
            msg for msg in r.get("messages", [])
            if msg.get("user") == BOT_USER_ID and msg["ts"] > after_ts
        ]
        for msg in reversed(bot_msgs):
            if not _is_progress_msg(msg.get("text", "")):
                return msg
        if bot_msgs and time.time() < deadline:
            continue
        if bot_msgs:
            return bot_msgs[-1]
    return None


def run_test(label: str, text: str, timeout: int = 120) -> dict:
    print(f"\n{'='*60}")
    print(f"TEST: {label}")
    print(f"  > {text[:100]}")
    t0 = time.time()
    msg_ts = send_mention(text)
    reply = wait_for_reply(msg_ts, msg_ts, timeout=timeout)
    elapsed = round(time.time() - t0, 1)
    if reply:
        reply_text = reply.get("text", "")[:800]
        print(f"  OK ({elapsed}s): {reply_text[:200]}")
        return {
            "label": label, "elapsed_s": elapsed,
            "response": reply_text, "thread_ts": msg_ts,
            "status": "ok",
        }
    else:
        print(f"  TIMEOUT after {elapsed}s")
        return {
            "label": label, "elapsed_s": elapsed,
            "response": None, "thread_ts": msg_ts,
            "status": "timeout",
        }


def run_thread_test(
    label: str, initial: str, followup: str, timeout: int = 120
) -> dict:
    print(f"\n{'='*60}")
    print(f"THREAD: {label}")
    print(f"  > Initial: {initial[:100]}")
    t0 = time.time()
    msg_ts = send_mention(initial)
    reply1 = wait_for_reply(msg_ts, msg_ts, timeout=timeout)
    if not reply1:
        print(f"  TIMEOUT on initial")
        return {"label": label, "status": "timeout_initial"}

    e1 = round(time.time() - t0, 1)
    print(f"  OK Initial ({e1}s): {reply1['text'][:200]}")

    time.sleep(2)
    print(f"  > Follow-up: {followup[:100]}")
    t1 = time.time()
    fu_ts = send_thread_reply(msg_ts, followup)
    reply2 = wait_for_reply(msg_ts, fu_ts, timeout=timeout)
    e2 = round(time.time() - t1, 1)
    if reply2:
        print(f"  OK Follow-up ({e2}s): {reply2['text'][:200]}")
    else:
        print(f"  TIMEOUT on follow-up")
    return {
        "label": label, "initial_s": e1, "followup_s": e2,
        "initial_response": reply1["text"][:800],
        "followup_response": (reply2 or {}).get("text", "")[:800],
        "thread_ts": msg_ts,
        "status": "ok" if reply2 else "timeout_followup",
    }


if __name__ == "__main__":
    results = []

    # ===== PHASE 1: Formatting & AI-Tell Checks =====
    print("\n" + "=" * 60)
    print("PHASE 1: Formatting & Writing Quality")
    print("=" * 60)

    results.append(run_test(
        "p1_comparison_tldr",
        "compare React, Vue, and Svelte for me, what are the key differences?",
        timeout=180,
    ))

    results.append(run_test(
        "p1_ranked_list",
        "what are the top 5 Python web frameworks and why?",
        timeout=180,
    ))

    results.append(run_test(
        "p1_greeting_warm",
        "hey Lucy, how's it going?",
    ))

    results.append(run_test(
        "p1_cron_listing",
        "what recurring tasks do I have set up?",
    ))

    results.append(run_test(
        "p1_clerk_users",
        "how many users do we have in Clerk?",
        timeout=90,
    ))

    # ===== PHASE 2: Integration Tests =====
    print("\n" + "=" * 60)
    print("PHASE 2: Integration Tests (Connected Services)")
    print("=" * 60)

    results.append(run_test(
        "p2_gmail_recent",
        "check my Gmail and tell me about the 3 most recent emails",
        timeout=120,
    ))

    results.append(run_test(
        "p2_github_repos",
        "list my GitHub repositories",
        timeout=90,
    ))

    results.append(run_test(
        "p2_drive_files",
        "what files do I have in my Google Drive?",
        timeout=90,
    ))

    results.append(run_test(
        "p2_vercel_projects",
        "show me my Vercel projects and their deployment status",
        timeout=90,
    ))

    results.append(run_test(
        "p2_sheets_list",
        "list any Google Sheets I have access to",
        timeout=90,
    ))

    # ===== PHASE 3: Multi-Step Workflows =====
    print("\n" + "=" * 60)
    print("PHASE 3: Multi-Step Workflows")
    print("=" * 60)

    results.append(run_thread_test(
        "p3_email_then_sheet",
        "find my most recent email from any newsletter and summarize it for me",
        "now create a Google Sheet with a summary of the last 5 newsletter emails",
        timeout=180,
    ))

    results.append(run_thread_test(
        "p3_github_pr_review",
        "check my latest GitHub pull requests across all repos",
        "for the most recent one, give me a summary of what changed",
        timeout=180,
    ))

    # ===== PHASE 4: Complex Requests =====
    print("\n" + "=" * 60)
    print("PHASE 4: Complex Requests & Edge Cases")
    print("=" * 60)

    results.append(run_test(
        "p4_multi_integration",
        "give me a status update: check my latest emails, any open GitHub PRs, and my Vercel deployment status. Combine everything into one summary.",
        timeout=240,
    ))

    results.append(run_test(
        "p4_knowledge_deep",
        "explain the differences between SQL and NoSQL databases, when to use each, and give me real-world examples",
        timeout=180,
    ))

    results.append(run_test(
        "p4_date_math",
        "what day is it today and what is 2847 divided by 39?",
    ))

    results.append(run_test(
        "p4_self_intro",
        "introduce yourself, who are you and what can you help me with?",
    ))

    # ===== RESULTS =====
    print("\n\n" + "=" * 60)
    print("FULL RESULTS SUMMARY")
    print("=" * 60)
    for r in results:
        status = "OK" if r.get("status") == "ok" else "FAIL"
        elapsed = r.get("elapsed_s") or r.get("initial_s", "?")
        resp = (r.get("response") or r.get("initial_response") or "")[:120]
        print(f"  [{status}] {r['label']}: {elapsed}s")
        print(f"        {resp}")

    with open("test_v3_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to test_v3_results.json")
