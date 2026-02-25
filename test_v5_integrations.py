"""Lucy v5 Integration Workflow Test Suite.

Focus: Cross-service workflows, connector reliability, response quality.
Connectors: Gmail, Google Drive, Google Sheets, GitHub, Vercel, Clerk

Cycle 1: Integration accuracy and cross-service workflows
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
    "bit longer than", "wrapping up", "thorough one",
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
    last_progress_ts = None
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
        if bot_msgs:
            last_progress_ts = bot_msgs[-1]["ts"]

    if last_progress_ts:
        with httpx.Client(verify=SSL, timeout=15) as c:
            r = c.get(
                "https://slack.com/api/conversations.replies",
                headers={"Authorization": f"Bearer {BOT_TOKEN}"},
                params={"channel": CHANNEL, "ts": thread_ts, "limit": 50},
            ).json()
        all_bot = [
            msg for msg in r.get("messages", [])
            if msg.get("user") == BOT_USER_ID and msg["ts"] > after_ts
        ]
        if all_bot:
            return all_bot[-1]
    return None


def run_test(label: str, text: str, timeout: int = 180) -> dict:
    print(f"\n{'='*60}")
    print(f"TEST: {label}")
    print(f"  > {text[:130]}")
    t0 = time.time()
    msg_ts = send_mention(text)
    reply = wait_for_reply(msg_ts, msg_ts, timeout=timeout)
    elapsed = round(time.time() - t0, 1)
    if reply:
        reply_text = reply.get("text", "")[:1200]
        print(f"  OK ({elapsed}s): {reply_text[:250]}")
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
    print(f"  > Initial: {initial[:130]}")
    t0 = time.time()
    msg_ts = send_mention(initial)
    reply1 = wait_for_reply(msg_ts, msg_ts, timeout=timeout)
    if not reply1:
        print(f"  TIMEOUT on initial")
        return {"label": label, "status": "timeout_initial"}

    e1 = round(time.time() - t0, 1)
    r1_text = reply1["text"][:1200]
    print(f"  OK Initial ({e1}s): {r1_text[:250]}")

    time.sleep(3)
    print(f"  > Follow-up: {followup[:130]}")
    t1 = time.time()
    fu_ts = send_thread_reply(msg_ts, followup)
    reply2 = wait_for_reply(msg_ts, fu_ts, timeout=timeout)
    e2 = round(time.time() - t1, 1)
    r2_text = (reply2 or {}).get("text", "")[:1200]
    if reply2:
        print(f"  OK Follow-up ({e2}s): {r2_text[:250]}")
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

    # ===== ROUND 1: Single-connector deep tests =====
    print("\n" + "=" * 60)
    print("ROUND 1: Deep Single-Connector Tests")
    print("=" * 60)

    # Gmail: search + filter + summarize
    results.append(run_test(
        "r1_gmail_filter",
        "check my Gmail and find any unread emails. For each one, tell me who sent it, the subject, and when it arrived",
        timeout=240,
    ))

    # Sheets: read specific data
    results.append(run_thread_test(
        "r1_sheets_deep",
        "open the 'Contact Login + Tool Info' sheet and tell me how many clients are listed",
        "which of those clients have the most tools or logins associated with them?",
        timeout=180,
    ))

    # Clerk: filtered query
    results.append(run_test(
        "r1_clerk_search",
        "search Clerk for users who signed up in the last 24 hours and tell me how many there are",
        timeout=120,
    ))

    # GitHub: specific repo info
    results.append(run_test(
        "r1_github_specific",
        "check the Serprisingly organization on GitHub and tell me what repositories exist there",
        timeout=120,
    ))

    # ===== ROUND 2: Cross-connector workflows =====
    print("\n" + "=" * 60)
    print("ROUND 2: Cross-Connector Workflows")
    print("=" * 60)

    # Gmail + Sheets: read email data, create sheet
    results.append(run_thread_test(
        "r2_email_to_sheet",
        "check my last 5 emails and give me a summary of each",
        "now put that email summary into a new Google Sheet with columns: Sender, Subject, Date, Summary",
        timeout=240,
    ))

    # Clerk + analysis: use data for reasoning
    results.append(run_thread_test(
        "r2_clerk_analysis",
        "how many users signed up in Clerk this month?",
        "based on that growth rate, project how many users we'll have by the end of March",
        timeout=180,
    ))

    # Drive + Sheets: find and read
    results.append(run_test(
        "r2_drive_to_analysis",
        "find the most recently modified spreadsheet in my Google Drive and give me a summary of what data it contains",
        timeout=180,
    ))

    # ===== ROUND 3: Complex multi-step workflows =====
    print("\n" + "=" * 60)
    print("ROUND 3: Complex Multi-Step Workflows")
    print("=" * 60)

    # All-in-one status report
    results.append(run_test(
        "r3_full_status",
        "create a status report for me: 1) count of Clerk users 2) any unread emails 3) latest GitHub activity 4) list my Google Sheets. Format it nicely.",
        timeout=300,
    ))

    # Sequential tool chain with reasoning
    results.append(run_thread_test(
        "r3_research_then_create",
        "I want to send a professional email to our most recently signed up Clerk user welcoming them. First, find who they are.",
        "great, now draft that welcome email for me. Make it warm and professional, mentioning our product Serprisingly.",
        timeout=240,
    ))

    # ===== ROUND 4: Error handling and edge cases =====
    print("\n" + "=" * 60)
    print("ROUND 4: Error Handling & Edge Cases")
    print("=" * 60)

    # Non-existent file
    results.append(run_test(
        "r4_missing_file",
        "find a file called 'Q4 Revenue Report 2025' in my Google Drive",
        timeout=90,
    ))

    # Wrong connector assumption
    results.append(run_test(
        "r4_wrong_connector",
        "check my Jira tickets for this sprint",
        timeout=60,
    ))

    # Ambiguous connector request
    results.append(run_test(
        "r4_ambiguous",
        "send a message to the team about tomorrow's standup being moved to 11am",
        timeout=60,
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
        resp = (r.get("response") or r.get("initial_response") or "")[:150]
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

    with open("test_v5_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to test_v5_results.json")
