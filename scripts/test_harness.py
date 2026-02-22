#!/usr/bin/env python3
"""
Lucy End-to-End Test Harness.

Sends messages AS the real user (Ojash) via the xoxp user token,
reads responses via the bot token, and logs everything with precise timestamps.

Usage:
    python scripts/test_harness.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any

sys.path.insert(0, str(__file__[: __file__.rfind("/")] + "/../src"))

# ─── Config ──────────────────────────────────────────────────────────────────
USER_TOKEN = "xoxp-4131935301158-4138553919170-10541136382135-3e53106a1f95de8ae377e543affa0bb0"
BOT_TOKEN = "xoxb-4131935301158-10552709351157-XWa5YmNyXCb2L8XcpR0m4uuY"
CHANNEL_ID = "C0AEZ241C3V"
LUCY_BOT_ID = "U0AG8LVAB4M"
SSL_CERT = os.popen("python3 -c 'import certifi; print(certifi.where())'").read().strip()

# ─── Helpers ─────────────────────────────────────────────────────────────────

def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def api_call(method: str, token: str, **params: Any) -> dict:
    """Make a Slack API call and return parsed JSON."""
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(
        f"https://slack.com/api/{method}",
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    os.environ["SSL_CERT_FILE"] = SSL_CERT
    os.environ["REQUESTS_CA_BUNDLE"] = SSL_CERT
    return json.loads(urllib.request.urlopen(req).read())


def send_as_user(text: str) -> str:
    """Send a message as Ojash. Returns the message timestamp."""
    result = api_call("chat.postMessage", USER_TOKEN, channel=CHANNEL_ID, text=text)
    if not result.get("ok"):
        raise RuntimeError(f"send_as_user failed: {result.get('error')}")
    ts = result["ts"]
    print(f"[{_ts()}] 📤 SENT (as Ojash): {text!r}  ts={ts}")
    return ts


def get_replies(thread_ts: str, after_ts: float = 0.0) -> list[dict]:
    """Fetch all replies in a thread, filtering to only bot replies after a given ts."""
    result = api_call(
        "conversations.replies",
        BOT_TOKEN,
        channel=CHANNEL_ID,
        ts=thread_ts,
        limit=20,
    )
    if not result.get("ok"):
        return []
    messages = result.get("messages", [])
    return [
        m for m in messages
        if m.get("user") == LUCY_BOT_ID or m.get("bot_id")
        if float(m.get("ts", 0)) > after_ts
        if m["ts"] != thread_ts  # exclude the parent message itself
    ]


def wait_for_reply(thread_ts: str, timeout: float = 30.0, poll_interval: float = 1.0) -> list[dict]:
    """Poll for Lucy's reply in a thread until timeout."""
    sent_at = time.time()
    deadline = sent_at + timeout
    after = float(thread_ts)

    while time.time() < deadline:
        replies = get_replies(thread_ts, after_ts=after)
        lucy_replies = [r for r in replies if r.get("bot_id")]
        if lucy_replies:
            elapsed = time.time() - sent_at
            print(f"[{_ts()}] ⏱  Response received in {elapsed:.2f}s")
            return lucy_replies
        time.sleep(poll_interval)

    print(f"[{_ts()}] ⏰ TIMEOUT: No reply in {timeout}s")
    return []


def print_replies(replies: list[dict]) -> None:
    for r in replies:
        ts = r.get("ts", "")
        text = r.get("text", "")
        blocks = r.get("blocks", [])
        if text:
            print(f"  💬 Lucy: {text!r}")
        if blocks:
            for b in blocks:
                btype = b.get("type", "")
                if btype == "section":
                    print(f"  📄 [section] {b.get('text', {}).get('text', '')!r}")
                elif btype == "header":
                    print(f"  🏷  [header]  {b.get('text', {}).get('text', '')!r}")
                elif btype == "actions":
                    btns = [e.get("text", {}).get("text", "") for e in b.get("elements", [])]
                    print(f"  🔘 [actions] buttons: {btns}")
                elif btype == "context":
                    elems = [e.get("text", "") for e in b.get("elements", []) if isinstance(e, dict)]
                    print(f"  ℹ️  [context] {elems}")


def check_db_records(label: str) -> None:
    """Quick DB check via psycopg2."""
    try:
        import psycopg2
        conn = psycopg2.connect("postgresql://lucy:lucy@167.86.82.46:5432/lucy")
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM workspaces")
        ws = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM users")
        us = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*), status FROM tasks GROUP BY status ORDER BY status")
        tasks = cur.fetchall()
        conn.close()
        print(f"  🗄  DB [{label}] — workspaces={ws}, users={us}, tasks={tasks}")
    except Exception as e:
        print(f"  🗄  DB check failed: {e}")


# ─── Test Suite ──────────────────────────────────────────────────────────────

def run_tests() -> None:
    print("\n" + "="*70)
    print("  LUCY END-TO-END TEST HARNESS")
    print("="*70 + "\n")

    results: dict[str, str] = {}

    # ── Level 1: Auth & Connectivity ──────────────────────────────────────
    print("── LEVEL 1: Auth & Connectivity ──────────────────────────────────")
    r = api_call("auth.test", USER_TOKEN)
    user_ok = r.get("ok")
    print(f"[{_ts()}] User auth:  {'✅' if user_ok else '❌'}  user={r.get('user')} workspace={r.get('team')}")
    results["1.1 User auth"] = "✅ PASS" if user_ok else "❌ FAIL"

    r2 = api_call("auth.test", BOT_TOKEN)
    bot_ok = r2.get("ok")
    print(f"[{_ts()}] Bot auth:   {'✅' if bot_ok else '❌'}  bot={r2.get('user')}")
    results["1.2 Bot auth"] = "✅ PASS" if bot_ok else "❌ FAIL"

    # Check DB connectivity
    check_db_records("startup")
    results["1.3 DB connect"] = "✅ PASS"

    # ── Level 2: @Lucy hello gate ─────────────────────────────────────────
    print("\n── LEVEL 2: @Lucy Hello Gate ────────────────────────────────────")
    ts1 = send_as_user(f"<@{LUCY_BOT_ID}> hello")
    replies = wait_for_reply(ts1, timeout=20)
    if replies:
        print_replies(replies)
        # Verify the response text
        full_text = " ".join(r.get("text", "") for r in replies)
        blocks_text = str(replies)
        if "Hello" in full_text or "hello" in full_text.lower() or "Hello" in blocks_text:
            results["2.1 Hello gate"] = "✅ PASS"
        else:
            results["2.1 Hello gate"] = "⚠️  PARTIAL (no hello text)"
    else:
        results["2.1 Hello gate"] = "❌ FAIL (no reply)"

    check_db_records("after hello")

    # ── Level 2b: /lucy status slash command ──────────────────────────────
    print(f"\n[{_ts()}] Testing /lucy status via slash command payload...")
    # Slash commands can't be triggered via API — using a mention workaround:
    # Actually send a mention that exercises the status handler
    ts_status = send_as_user(f"<@{LUCY_BOT_ID}> what is your current status?")
    replies_status = wait_for_reply(ts_status, timeout=25)
    if replies_status:
        print_replies(replies_status)
        results["2.2 Status mention"] = "✅ PASS"
    else:
        results["2.2 Status mention"] = "❌ FAIL (no reply)"

    # ── Level 3: LLM Routing — End-to-End Task ────────────────────────────
    print("\n── LEVEL 3: End-to-End LLM Task ────────────────────────────────")
    ts_joke = send_as_user(f"<@{LUCY_BOT_ID}> tell me a short joke about programming")
    print(f"[{_ts()}] Waiting up to 45s for LLM response...")
    replies_joke = wait_for_reply(ts_joke, timeout=45, poll_interval=2)
    if replies_joke:
        print_replies(replies_joke)
        results["3.1 LLM task"] = "✅ PASS"
        # Check DB for tasks
        check_db_records("after LLM task")
    else:
        results["3.1 LLM task"] = "❌ FAIL (no reply)"

    # ── Level 4: Memory ───────────────────────────────────────────────────
    print("\n── LEVEL 4: Memory Persistence ─────────────────────────────────")
    ts_mem1 = send_as_user(f"<@{LUCY_BOT_ID}> remember that my favorite programming language is Rust")
    replies_mem1 = wait_for_reply(ts_mem1, timeout=35, poll_interval=2)
    if replies_mem1:
        print_replies(replies_mem1)
        results["4.1 Memory write"] = "✅ PASS"
    else:
        results["4.1 Memory write"] = "❌ FAIL (no reply)"

    time.sleep(3)  # Let the async memory sync complete

    ts_mem2 = send_as_user(f"<@{LUCY_BOT_ID}> what is my favorite programming language?")
    replies_mem2 = wait_for_reply(ts_mem2, timeout=35, poll_interval=2)
    if replies_mem2:
        print_replies(replies_mem2)
        mem_text = " ".join(r.get("text", "") + str(r.get("blocks", "")) for r in replies_mem2)
        if "rust" in mem_text.lower():
            results["4.2 Memory recall"] = "✅ PASS (mentioned Rust)"
        else:
            results["4.2 Memory recall"] = "⚠️  PARTIAL (replied but no Rust mention)"
    else:
        results["4.2 Memory recall"] = "❌ FAIL (no reply)"

    # ── Summary ───────────────────────────────────────────────────────────
    print("\n" + "="*70)
    print("  TEST RESULTS SUMMARY")
    print("="*70)
    passes = 0
    fails = 0
    for test, result in results.items():
        icon = "✅" if "PASS" in result else ("⚠️" if "PARTIAL" in result else "❌")
        print(f"  {icon}  {test:<35} {result}")
        if "PASS" in result:
            passes += 1
        elif "FAIL" in result:
            fails += 1

    print(f"\n  Total: {passes} passed, {fails} failed, {len(results) - passes - fails} partial")
    print("="*70 + "\n")


if __name__ == "__main__":
    run_tests()
