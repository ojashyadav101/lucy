"""Lucy v4 Stress Test Suite, high intensity.

Tests:
  Phase 1: Race condition validation (concurrent thread messages)
  Phase 2: Integration stress (Gmail, Drive, Sheets, GitHub, Vercel)
  Phase 3: Multi-tool workflows (sequential + parallel tool chains)
  Phase 4: Complex/confusing requests (multiple asks in one message)
  Phase 5: Thread context under pressure (rapid follow-ups)
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
    "bit longer than",
}


def _is_progress_msg(text: str) -> bool:
    lower = text.lower().strip()
    return any(p in lower for p in _PROGRESS_PHRASES) and len(lower) < 100


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


def get_all_replies(thread_ts: str) -> list[dict]:
    """Get all Lucy replies in a thread, skipping progress messages."""
    with httpx.Client(verify=SSL, timeout=15) as c:
        r = c.get(
            "https://slack.com/api/conversations.replies",
            headers={"Authorization": f"Bearer {BOT_TOKEN}"},
            params={"channel": CHANNEL, "ts": thread_ts, "limit": 50},
        ).json()
    return [
        msg for msg in r.get("messages", [])
        if msg.get("user") == BOT_USER_ID
        and not _is_progress_msg(msg.get("text", ""))
    ]


def wait_for_reply(thread_ts: str, after_ts: str, timeout: int = 120) -> dict | None:
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
        substantive = [m for m in bot_msgs if not _is_progress_msg(m.get("text", ""))]
        if substantive:
            return substantive[-1]
        if bot_msgs and time.time() > deadline - 10:
            return bot_msgs[-1]
    return None


def run_test(label: str, text: str, timeout: int = 120) -> dict:
    print(f"\n{'='*60}")
    print(f"TEST: {label}")
    print(f"  > {text[:120]}")
    t0 = time.time()
    msg_ts = send_mention(text)
    reply = wait_for_reply(msg_ts, msg_ts, timeout=timeout)
    elapsed = round(time.time() - t0, 1)
    if reply:
        reply_text = reply.get("text", "")[:1000]
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
    print(f"  > Initial: {initial[:120]}")
    t0 = time.time()
    msg_ts = send_mention(initial)
    reply1 = wait_for_reply(msg_ts, msg_ts, timeout=timeout)
    if not reply1:
        print(f"  TIMEOUT on initial")
        return {"label": label, "status": "timeout_initial"}

    e1 = round(time.time() - t0, 1)
    print(f"  OK Initial ({e1}s): {reply1['text'][:200]}")

    time.sleep(3)
    print(f"  > Follow-up: {followup[:120]}")
    t1 = time.time()
    fu_ts = send_thread_reply(msg_ts, followup)
    reply2 = wait_for_reply(msg_ts, fu_ts, timeout=timeout)
    e2 = round(time.time() - t1, 1)
    if reply2:
        print(f"  OK Follow-up ({e2}s): {reply2['text'][:200]}")
    else:
        print(f"  TIMEOUT on follow-up")

    all_replies = get_all_replies(msg_ts)
    return {
        "label": label, "initial_s": e1, "followup_s": e2,
        "initial_response": reply1["text"][:1000],
        "followup_response": (reply2 or {}).get("text", "")[:1000],
        "thread_ts": msg_ts,
        "total_replies": len(all_replies),
        "status": "ok" if reply2 else "timeout_followup",
    }


def run_race_test(label: str, initial: str, rapid_followup: str) -> dict:
    """Send a follow-up IMMEDIATELY (within 2s) to test race condition."""
    print(f"\n{'='*60}")
    print(f"RACE TEST: {label}")
    print(f"  > Initial: {initial[:120]}")
    print(f"  > Rapid follow-up: {rapid_followup[:120]}")

    t0 = time.time()
    msg_ts = send_mention(initial)
    time.sleep(2)
    fu_ts = send_thread_reply(msg_ts, rapid_followup)

    time.sleep(60)

    all_replies = get_all_replies(msg_ts)
    elapsed = round(time.time() - t0, 1)

    print(f"  Total substantive replies: {len(all_replies)}")
    for i, r in enumerate(all_replies):
        print(f"  Reply {i+1}: {r.get('text', '')[:150]}")

    return {
        "label": label, "elapsed_s": elapsed,
        "total_replies": len(all_replies),
        "replies": [r.get("text", "")[:500] for r in all_replies],
        "thread_ts": msg_ts,
        "status": "ok",
    }


if __name__ == "__main__":
    results = []

    # ===== PHASE 1: Race Condition Test =====
    print("\n" + "=" * 60)
    print("PHASE 1: Race Condition / Thread Locking")
    print("=" * 60)

    results.append(run_race_test(
        "race_github_pr",
        "check my recent GitHub pull requests",
        "also while you're at it, list my repos",
    ))

    # ===== PHASE 2: Integration Stress =====
    print("\n" + "=" * 60)
    print("PHASE 2: Integration Stress Tests")
    print("=" * 60)

    results.append(run_test(
        "p2_gmail_search",
        "search my Gmail for any emails from Razorpay in the last week and tell me how much I've been charged total",
        timeout=180,
    ))

    results.append(run_test(
        "p2_drive_specific",
        "find a file called 'USP Product Page Copy' in my Google Drive and tell me what it's about",
        timeout=120,
    ))

    results.append(run_test(
        "p2_sheets_read",
        "open the 'Contact Login + Tool Info' spreadsheet and tell me what data is in it",
        timeout=120,
    ))

    results.append(run_test(
        "p2_clerk_details",
        "show me the 10 most recently created users in Clerk with their email addresses",
        timeout=120,
    ))

    # ===== PHASE 3: Multi-Tool Workflows =====
    print("\n" + "=" * 60)
    print("PHASE 3: Multi-Tool Workflows")
    print("=" * 60)

    results.append(run_test(
        "p3_cross_service",
        "give me a morning briefing: check my latest 3 emails, see if there are any GitHub notifications, and tell me what day it is and what my schedule looks like",
        timeout=240,
    ))

    results.append(run_thread_test(
        "p3_sequential_workflow",
        "find all Google Sheets I own that were modified in the last 2 days",
        "for the most recently modified one, read the first few rows and summarize what it contains",
        timeout=180,
    ))

    # ===== PHASE 4: Complex / Confusing Requests =====
    print("\n" + "=" * 60)
    print("PHASE 4: Complex & Confusing Requests")
    print("=" * 60)

    results.append(run_test(
        "p4_multi_ask",
        "three things: 1) how many users do we have in Clerk 2) what cron jobs do I have running 3) what's the weather like today",
        timeout=120,
    ))

    results.append(run_test(
        "p4_code_request",
        "write me a Python function that takes a list of email addresses and validates them using regex, then write unit tests for it",
        timeout=180,
    ))

    results.append(run_test(
        "p4_vague_request",
        "can you help me with that thing we talked about earlier?",
        timeout=60,
    ))

    results.append(run_test(
        "p4_contradictory",
        "create a cron job that runs every 5 minutes to check my Gmail for new emails and summarize them in a thread here. Actually wait, make it every hour instead. No wait, every 30 minutes.",
        timeout=120,
    ))

    # ===== PHASE 5: Thread Context Under Pressure =====
    print("\n" + "=" * 60)
    print("PHASE 5: Thread Context Under Pressure")
    print("=" * 60)

    results.append(run_thread_test(
        "p5_context_chain",
        "I'm thinking about migrating our backend from Express to FastAPI. What do you think?",
        "what about the authentication layer specifically? We use JWT tokens with refresh rotation",
        timeout=120,
    ))

    results.append(run_thread_test(
        "p5_tool_then_question",
        "check how many users we have in Clerk",
        "that's interesting. Based on that number, what pricing tier should we be on if we're using Auth0 instead?",
        timeout=120,
    ))

    # ===== RESULTS =====
    print("\n\n" + "=" * 60)
    print("FULL RESULTS SUMMARY")
    print("=" * 60)
    for r in results:
        status = "OK" if r.get("status") == "ok" else "FAIL"
        elapsed = r.get("elapsed_s") or r.get("initial_s", "?")
        resp = (r.get("response") or r.get("initial_response") or "")[:140]
        label = r["label"]
        print(f"  [{status}] {label}: {elapsed}s")
        if "total_replies" in r:
            print(f"        Replies: {r['total_replies']}")
        print(f"        {resp}")

    with open("test_v4_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to test_v4_results.json")
