"""Round 2 test suite — validates Viktor patches + concurrency isolation.

Tests:
  R1:  Memory persistence (live — store + recall across threads)
  R2:  Memory classification (offline — should_persist, classify_target)
  R3:  Contextual emoji reactions (offline regex + live Slack)
  R4:  Rich formatting pipeline (offline — links, emojis, splitting)
  R5:  UX micro-interactions (offline — error messages, progress language)
  R6:  Tone pipeline (offline — anti-pattern stripping)
  R7:  Concurrent memory isolation (live — two threads, same workspace)
  R8:  Thread context isolation (live — three concurrent threads)
  R9:  Composio session isolation (offline — cache keying)
  R10: Load test (live — 5 concurrent messages, latency distribution)

Usage:
    python round2_tests.py                # all tests
    python round2_tests.py R2 R4 R6       # offline only
    python round2_tests.py --report       # all + markdown report
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx
import certifi

# ── Configuration ────────────────────────────────────────────────────────

TOKEN = os.environ.get("SLACK_USER_TOKEN", "")
BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
CHANNEL = os.environ.get("SLACK_CHANNEL", "C0AEZ241C3V")
LUCY_ID = os.environ.get("LUCY_BOT_ID", "U0AG8LVAB4M")
WORKSPACE_ROOT = Path(os.environ.get(
    "WORKSPACE_ROOT", str(Path(__file__).parent / "workspaces"),
))
WORKSPACE_ID = os.environ.get(
    "WORKSPACE_ID", "1d18c417-b53c-4ab1-80da-4959a622da17",
)
IST = timezone(timedelta(hours=5, minutes=30))
ALL_RESULTS: list[dict] = []


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def now_ist() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")

def _client() -> httpx.Client:
    return httpx.Client(verify=certifi.where(), timeout=15)

def slack_post(text: str) -> dict:
    with _client() as c:
        return c.post(
            "https://slack.com/api/chat.postMessage",
            json={"channel": CHANNEL, "text": f"<@{LUCY_ID}> {text}", "as_user": True},
            headers={"Authorization": f"Bearer {TOKEN}"},
        ).json()

def slack_post_in_thread(text: str, thread_ts: str) -> dict:
    with _client() as c:
        return c.post(
            "https://slack.com/api/chat.postMessage",
            json={
                "channel": CHANNEL, "text": f"<@{LUCY_ID}> {text}",
                "thread_ts": thread_ts, "as_user": True,
            },
            headers={"Authorization": f"Bearer {TOKEN}"},
        ).json()

def get_thread_messages(thread_ts: str) -> list[dict]:
    with _client() as c:
        return c.get(
            "https://slack.com/api/conversations.replies",
            params={"channel": CHANNEL, "ts": thread_ts, "limit": 50},
            headers={"Authorization": f"Bearer {BOT_TOKEN}"},
        ).json().get("messages", [])

def get_reactions(channel: str, ts: str) -> list[dict]:
    with _client() as c:
        resp = c.get(
            "https://slack.com/api/reactions.get",
            params={"channel": channel, "timestamp": ts, "full": "true"},
            headers={"Authorization": f"Bearer {BOT_TOKEN}"},
        ).json()
        msg = resp.get("message", {})
        return msg.get("reactions", [])

def wait_for_reply(thread_ts: str, timeout_s: int = 180) -> dict | None:
    last_reply: dict | None = None
    stable = 0
    with _client() as c:
        for _ in range(timeout_s // 3):
            time.sleep(3)
            msgs = c.get(
                "https://slack.com/api/conversations.replies",
                params={"channel": CHANNEL, "ts": thread_ts, "limit": 30},
                headers={"Authorization": f"Bearer {BOT_TOKEN}"},
            ).json().get("messages", [])
            bot = [m for m in msgs if m.get("ts") != thread_ts and (m.get("bot_id") or m.get("app_id"))]
            if bot:
                newest = bot[-1]
                if newest.get("text", "").startswith("Working on it"):
                    stable = 0
                    continue
                if last_reply and last_reply.get("ts") == newest.get("ts"):
                    stable += 1
                else:
                    last_reply = newest
                    stable = 0
                if stable >= 2:
                    return last_reply
        return last_reply

async def send_and_wait(text: str, timeout_s: int = 180):
    t0 = time.monotonic()
    result = await asyncio.to_thread(slack_post, text)
    ts = result.get("ts", "")
    reply = await asyncio.to_thread(wait_for_reply, ts, timeout_s)
    elapsed = time.monotonic() - t0
    return ts, reply.get("text") if reply else None, elapsed, reply

def record(tid: str, name: str, passed: bool, details: dict) -> dict:
    entry = {"test": tid, "name": name, "passed": passed,
             "timestamp_utc": now_utc(), "timestamp_ist": now_ist(), **details}
    ALL_RESULTS.append(entry)
    print(f"  [{'PASS' if passed else 'FAIL'}] {tid}: {name}")
    return entry


# ═══════════════════════════════════════════════════════════════════════════
# R2 — Memory Classification (offline)
# ═══════════════════════════════════════════════════════════════════════════

def test_r2_memory_classification() -> dict:
    print("\n" + "=" * 70)
    print("R2: Memory classification (offline)")
    print("=" * 70)

    sys.path.insert(0, str(Path(__file__).parent / "src"))
    from lucy.workspace.memory import should_persist_memory, classify_memory_target

    cases = [
        ("Remember this: our MRR target is $500K", True, "company"),
        ("Our company uses React and Python", True, "company"),
        ("I'm the head of marketing", True, "team"),
        ("We switched to Vercel last month", True, "company"),
        ("My timezone is IST", True, "team"),
        ("Note that our budget is $50K for Q1", True, "session"),
        ("Going forward, always CC jake on emails", True, "session"),
        ("Our revenue is $2M ARR", True, "company"),
        ("What time is it?", False, "session"),
        ("Check my calendar", False, "session"),
        ("Send an email to Jake", False, "session"),
        ("Hi Lucy", False, "session"),
    ]

    results = []
    all_ok = True
    for msg, expect_persist, expect_target in cases:
        persists = should_persist_memory(msg)
        target = classify_memory_target(msg) if persists else "session"
        persist_ok = persists == expect_persist
        target_ok = target == expect_target if persists else True
        ok = persist_ok and target_ok
        if not ok:
            all_ok = False
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] \"{msg[:50]}\" persist={persists}({expect_persist}) target={target}({expect_target})")
        results.append({"msg": msg[:50], "persist_ok": persist_ok, "target_ok": target_ok, "ok": ok})

    return record("R2", "Memory classification", all_ok, {
        "cases": results, "pass_count": sum(r["ok"] for r in results), "total": len(results),
    })


# ═══════════════════════════════════════════════════════════════════════════
# R3 — Contextual Emoji Reactions (offline + live)
# ═══════════════════════════════════════════════════════════════════════════

def test_r3_reactions_offline() -> dict:
    print("\n" + "=" * 70)
    print("R3: Contextual emoji reactions (offline)")
    print("=" * 70)

    sys.path.insert(0, str(Path(__file__).parent / "src"))
    from lucy.slack.reactions import classify_reaction, get_working_emoji

    reaction_cases = [
        ("thanks!", "saluting_face", True),
        ("Thank you so much", "saluting_face", True),
        ("got it", "white_check_mark", True),
        ("sounds good!", "white_check_mark", True),
        ("ship it", "thumbsup", True),
        ("lgtm", "thumbsup", True),
        ("Can you check our calendar?", "eyes", False),
        ("urgent: deploy is down", "zap", False),
        ("There's a bug in checkout", "mag", False),
        ("Create a PDF report for Q4", "hammer_and_wrench", False),
        ("Research our competitors", "bar_chart", False),
        ("Deploy to production", "rocket", False),
        ("fyi we changed the API key", "memo", True),
    ]

    results = []
    all_ok = True
    for msg, expect_emoji, expect_react_only in reaction_cases:
        r = classify_reaction(msg)
        emoji_ok = r.emoji == expect_emoji
        react_ok = r.react_only == expect_react_only
        ok = emoji_ok and react_ok
        if not ok:
            all_ok = False
        print(f"  [{'PASS' if ok else 'FAIL'}] \"{msg[:40]}\" → {r.emoji}({'react' if r.react_only else 'reply'})"
              + (f" expected {expect_emoji}({'react' if expect_react_only else 'reply'})" if not ok else ""))
        results.append({"msg": msg[:40], "ok": ok})

    working_cases = [
        ("research competitors", "mag"),
        ("build me a report", "hammer_and_wrench"),
        ("deploy to staging", "rocket"),
        ("what time is it", "hourglass_flowing_sand"),
    ]
    for msg, expect in working_cases:
        actual = get_working_emoji(msg)
        ok = actual == expect
        if not ok:
            all_ok = False
        print(f"  [{'PASS' if ok else 'FAIL'}] working(\"{msg[:30]}\") → {actual}" +
              (f" expected {expect}" if not ok else ""))
        results.append({"msg": f"working:{msg[:30]}", "ok": ok})

    return record("R3", "Emoji reactions (offline)", all_ok, {
        "pass_count": sum(r["ok"] for r in results), "total": len(results),
    })


# ═══════════════════════════════════════════════════════════════════════════
# R4 — Rich Formatting Pipeline (offline)
# ═══════════════════════════════════════════════════════════════════════════

def test_r4_rich_formatting() -> dict:
    print("\n" + "=" * 70)
    print("R4: Rich formatting pipeline (offline)")
    print("=" * 70)

    sys.path.insert(0, str(Path(__file__).parent / "src"))
    from lucy.slack.rich_output import format_links, add_section_emoji, split_response, should_split_response

    results = []
    all_ok = True

    link_cases = [
        ("Check https://github.com/org/repo/pull/42 for details",
         "GitHub PR #42", True),
        ("Docs at https://docs.google.com/spreadsheets/d/abc123",
         "Google Docs", True),
        ("See https://linear.app/team/issue/ENG-123",
         "ENG-123 on Linear", True),
        ("Already formatted <https://foo.com|Foo>", "<https://foo.com|Foo>", True),
    ]
    for text, expected_fragment, should_contain in link_cases:
        result = format_links(text)
        ok = (expected_fragment in result) == should_contain
        if not ok:
            all_ok = False
        print(f"  [{'PASS' if ok else 'FAIL'}] link: \"{text[:50]}\" → contains '{expected_fragment}': {ok}")
        results.append({"type": "link", "ok": ok})

    emoji_cases = [
        ("Summary of findings", True),
        ("Warning about deploy", True),
        ("Random header", False),
    ]
    for header, expect_emoji in emoji_cases:
        result = add_section_emoji(header)
        has_emoji = result != header
        ok = has_emoji == expect_emoji
        if not ok:
            all_ok = False
        print(f"  [{'PASS' if ok else 'FAIL'}] emoji: \"{header}\" → \"{result[:40]}\"")
        results.append({"type": "emoji", "ok": ok})

    long_text = ("*Section One*\n" + "x " * 500 + "\n\n---\n\n*Section Two*\n" + "y " * 500 +
                 "\n\n*Section Three*\n" + "z " * 500)
    assert should_split_response(long_text), "should_split_response should be True for long text"
    chunks = split_response(long_text)
    split_ok = len(chunks) >= 2 and all(len(c) <= 3100 for c in chunks)
    if not split_ok:
        all_ok = False
    print(f"  [{'PASS' if split_ok else 'FAIL'}] split: {len(long_text)} chars → {len(chunks)} chunks, "
          f"max chunk={max(len(c) for c in chunks)}")
    results.append({"type": "split", "ok": split_ok})

    return record("R4", "Rich formatting (offline)", all_ok, {
        "pass_count": sum(r["ok"] for r in results), "total": len(results),
    })


# ═══════════════════════════════════════════════════════════════════════════
# R5 — UX Micro-Interactions (offline)
# ═══════════════════════════════════════════════════════════════════════════

def test_r5_ux_micro() -> dict:
    print("\n" + "=" * 70)
    print("R5: UX micro-interactions (offline)")
    print("=" * 70)

    sys.path.insert(0, str(Path(__file__).parent / "src"))
    from lucy.core.agent import _describe_progress

    results = []
    all_ok = True

    p1 = _describe_progress(["COMPOSIO_SEARCH_TOOLS", "COMPOSIO_MULTI_EXECUTE_TOOL"], turn=1)
    ok = p1.startswith("Working on it")
    print(f"  [{'PASS' if ok else 'FAIL'}] turn=1: \"{p1[:60]}\"")
    results.append({"turn": 1, "ok": ok})
    if not ok:
        all_ok = False

    p3 = _describe_progress(["COMPOSIO_SEARCH_TOOLS"], turn=4)
    ok = p3.startswith("Making progress")
    print(f"  [{'PASS' if ok else 'FAIL'}] turn=4: \"{p3[:60]}\"")
    results.append({"turn": 4, "ok": ok})
    if not ok:
        all_ok = False

    p6 = _describe_progress(["COMPOSIO_SEARCH_TOOLS"], turn=7)
    ok = "deep one" in p6
    print(f"  [{'PASS' if ok else 'FAIL'}] turn=7: \"{p6[:60]}\"")
    results.append({"turn": 7, "ok": ok})
    if not ok:
        all_ok = False

    p_tools = _describe_progress(["COMPOSIO_SEARCH_TOOLS", "COMPOSIO_MULTI_EXECUTE_TOOL"], turn=1)
    ok = "found the right tools" in p_tools and "executed some actions" in p_tools
    print(f"  [{'PASS' if ok else 'FAIL'}] tool labels: \"{p_tools[:80]}\"")
    results.append({"check": "tool_labels", "ok": ok})
    if not ok:
        all_ok = False

    return record("R5", "UX micro-interactions (offline)", all_ok, {"results": results})


# ═══════════════════════════════════════════════════════════════════════════
# R6 — Tone Pipeline (offline)
# ═══════════════════════════════════════════════════════════════════════════

def test_r6_tone_pipeline() -> dict:
    print("\n" + "=" * 70)
    print("R6: Tone pipeline (offline)")
    print("=" * 70)

    sys.path.insert(0, str(Path(__file__).parent / "src"))
    from lucy.core.output import process_output_sync

    cases = [
        ("Great question! Here's what I found...", "great question", False),
        ("I'd be happy to help with that.", "happy to help", False),
        ("It's worth noting that the data shows...", "worth noting", False),
        ("Let me delve into the details.", "delve into", False),
        ("I wasn't able to complete the request.", "wasn't able to", False),
        ("Here are the results for Q4.", "results for Q4", True),
    ]

    results = []
    all_ok = True
    for text, check_fragment, should_contain in cases:
        output = process_output_sync(text)
        contains = check_fragment.lower() in output.lower()
        ok = contains == should_contain
        if not ok:
            all_ok = False
        print(f"  [{'PASS' if ok else 'FAIL'}] \"{text[:50]}\" → '{check_fragment}' present={contains}")
        results.append({"text": text[:50], "ok": ok})

    return record("R6", "Tone pipeline (offline)", all_ok, {
        "pass_count": sum(r["ok"] for r in results), "total": len(results),
    })


# ═══════════════════════════════════════════════════════════════════════════
# R9 — Composio Session Isolation (offline)
# ═══════════════════════════════════════════════════════════════════════════

def test_r9_composio_isolation() -> dict:
    print("\n" + "=" * 70)
    print("R9: Composio session isolation (offline)")
    print("=" * 70)

    sys.path.insert(0, str(Path(__file__).parent / "src"))
    source = (Path(__file__).parent / "src" / "lucy" / "integrations" / "composio_client.py").read_text()

    results = {}
    results["sessions_keyed_by_workspace"] = "workspace_id" in source and "_session_cache" in source
    results["has_cache_lock"] = "_cache_lock" in source
    results["has_session_lock"] = "_session_lock" in source
    results["double_checked_locking"] = "self._session_lock" in source and "_cache_lock" in source
    results["lru_eviction"] = "_MAX_CACHED_SESSIONS" in source and "oldest_key" in source
    results["stale_recovery"] = "_get_session_with_recovery" in source

    passed = all(results.values())
    for k, v in results.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")

    return record("R9", "Composio session isolation (offline)", passed, results)


# ═══════════════════════════════════════════════════════════════════════════
# R1 — Memory Persistence (live)
# ═══════════════════════════════════════════════════════════════════════════

async def test_r1_memory_persistence() -> dict:
    print("\n" + "=" * 70)
    print("R1: Memory persistence (live)")
    print("=" * 70)

    ts1, reply1, e1, _ = await send_and_wait(
        "Remember this important fact: our Q1 revenue target is $800K and current revenue is $620K. "
        "Keep this in your memory for future reference."
    )
    stored = reply1 is not None and any(
        w in (reply1 or "").lower() for w in ["noted", "got it", "remember", "stored", "800", "620"]
    )
    print(f"  Store: {e1:.1f}s — {'OK' if stored else 'FAIL'}")
    print(f"    Response: {(reply1 or 'NO REPLY')[:200]}")

    time.sleep(5)

    mem_path = WORKSPACE_ROOT / WORKSPACE_ID / "data" / "session_memory.json"
    mem_on_disk = False
    if mem_path.exists():
        content = mem_path.read_text()
        mem_on_disk = "800" in content or "revenue" in content.lower()
    print(f"  Persisted to disk: {'YES' if mem_on_disk else 'NO'}")

    ts2, reply2, e2, _ = await send_and_wait(
        "What's our revenue target for Q1? And what's our current revenue?"
    )
    recalled = reply2 is not None and ("800" in (reply2 or "") or "620" in (reply2 or ""))
    print(f"  Recall (new thread): {e2:.1f}s — {'OK' if recalled else 'FAIL'}")
    print(f"    Response: {(reply2 or 'NO REPLY')[:200]}")

    passed = stored and (mem_on_disk or recalled)
    return record("R1", "Memory persistence", passed, {
        "store_elapsed_s": round(e1, 1), "recall_elapsed_s": round(e2, 1),
        "stored": stored, "mem_on_disk": mem_on_disk, "recalled": recalled,
        "store_response": (reply1 or "")[:300], "recall_response": (reply2 or "")[:300],
    })


# ═══════════════════════════════════════════════════════════════════════════
# R3 Live — Emoji Reactions (live)
# ═══════════════════════════════════════════════════════════════════════════

async def test_r3_reactions_live() -> dict:
    print("\n" + "=" * 70)
    print("R3 Live: Emoji reactions (live Slack)")
    print("=" * 70)

    emoji_aliases = {"thumbsup": {"+1", "thumbsup"}}

    react_only_cases = [
        ("thanks!", "saluting_face"),
        ("got it", "white_check_mark"),
        ("ship it", "thumbsup"),
    ]

    results = []
    all_ok = True
    for msg, expected_emoji in react_only_cases:
        result = await asyncio.to_thread(slack_post, msg)
        ts = result.get("ts", "")
        await asyncio.sleep(5)

        reactions = await asyncio.to_thread(get_reactions, CHANNEL, ts)
        emoji_names = [r.get("name", "") for r in reactions]
        valid_names = emoji_aliases.get(expected_emoji, {expected_emoji})
        has_emoji = bool(valid_names & set(emoji_names))

        thread_msgs = await asyncio.to_thread(get_thread_messages, ts)
        bot_replies = [m for m in thread_msgs if m.get("ts") != ts and (m.get("bot_id") or m.get("app_id"))]
        no_reply = len(bot_replies) == 0

        ok = has_emoji and no_reply
        if not ok:
            all_ok = False
        print(f"  [{'PASS' if ok else 'FAIL'}] \"{msg}\" → emoji={emoji_names} no_reply={no_reply}")
        results.append({"msg": msg, "has_emoji": has_emoji, "no_reply": no_reply, "ok": ok})

    return record("R3L", "Emoji reactions (live)", all_ok, {"results": results})


# ═══════════════════════════════════════════════════════════════════════════
# R7 — Concurrent Memory Isolation (live)
# ═══════════════════════════════════════════════════════════════════════════

async def test_r7_concurrent_memory() -> dict:
    print("\n" + "=" * 70)
    print("R7: Concurrent memory isolation")
    print("=" * 70)

    msgs = [
        "Remember this: our main competitor is Acme Corp and they raised $10M last week.",
        "Note that our next board meeting is on March 15th. Keep this in your memory.",
    ]

    tasks = [send_and_wait(m, timeout_s=120) for m in msgs]
    results = await asyncio.gather(*tasks)

    both_replied = all(r[1] is not None for r in results)
    for i, (ts, reply, elapsed, _) in enumerate(results):
        print(f"  Thread {i+1}: {elapsed:.1f}s — {(reply or 'NO REPLY')[:100]}")

    time.sleep(3)
    mem_path = WORKSPACE_ROOT / WORKSPACE_ID / "data" / "session_memory.json"
    both_persisted = False
    if mem_path.exists():
        content = mem_path.read_text().lower()
        has_acme = "acme" in content or "competitor" in content
        has_board = "board" in content or "march 15" in content
        both_persisted = has_acme and has_board
        print(f"  Disk: acme={has_acme}, board={has_board}")

    passed = both_replied and both_persisted
    print(f"  RESULT: {'PASS' if passed else 'FAIL'} (both_replied={both_replied}, both_persisted={both_persisted})")

    return record("R7", "Concurrent memory isolation", passed, {
        "both_replied": both_replied, "both_persisted": both_persisted,
    })


# ═══════════════════════════════════════════════════════════════════════════
# R8 — Thread Context Isolation (live)
# ═══════════════════════════════════════════════════════════════════════════

async def test_r8_thread_isolation() -> dict:
    print("\n" + "=" * 70)
    print("R8: Thread context isolation")
    print("=" * 70)

    msgs = [
        "What time is it for each team member right now?",
        "What integrations do I have connected?",
        "What events do I have on my calendar today?",
    ]

    tasks = [send_and_wait(m, timeout_s=120) for m in msgs]
    results = await asyncio.gather(*tasks)

    details = []
    contaminated = False
    for i, (ts, reply, elapsed, _) in enumerate(results):
        got = reply is not None
        details.append({"thread": i + 1, "msg": msgs[i][:40], "elapsed": round(elapsed, 1), "got": got,
                        "reply": (reply or "")[:150]})
        print(f"  Thread {i+1} [{elapsed:.1f}s]: {(reply or 'NO REPLY')[:100]}")

    if all(d["got"] for d in details):
        r0 = details[0]["reply"].lower()
        r1 = details[1]["reply"].lower()
        if "integration" in r0 and "gmail" in r0 and "time" not in r0:
            contaminated = True
        if "calendar" in r1 and "event" in r1 and "integration" not in r1:
            contaminated = True

    passed = all(d["got"] for d in details) and not contaminated
    print(f"  Contamination: {'DETECTED' if contaminated else 'clean'}")

    return record("R8", "Thread context isolation", passed, {
        "threads": details, "contaminated": contaminated,
    })


# ═══════════════════════════════════════════════════════════════════════════
# R10 — Load Test (live)
# ═══════════════════════════════════════════════════════════════════════════

async def test_r10_load() -> dict:
    print("\n" + "=" * 70)
    print("R10: Load test — 5 concurrent, varying complexity")
    print("=" * 70)

    msgs = [
        ("Hi Lucy!", "greeting"),
        ("What time is it?", "lookup"),
        ("What integrations are connected?", "tool_use"),
        ("Check my calendar for today and list all events", "multi_step"),
        ("Give me a detailed breakdown of everything you can do", "complex"),
    ]

    tasks = [send_and_wait(m, timeout_s=150) for m, _ in msgs]
    results = await asyncio.gather(*tasks)

    details = []
    for i, (ts, reply, elapsed, _) in enumerate(results):
        got = reply is not None
        details.append({
            "msg": msgs[i][0][:40], "type": msgs[i][1],
            "elapsed_s": round(elapsed, 1), "got_reply": got,
            "reply_len": len(reply or ""),
        })
        print(f"  [{msgs[i][1]:<12}] {elapsed:>6.1f}s {'OK' if got else 'FAIL'} — {msgs[i][0][:40]}")

    times = [d["elapsed_s"] for d in details]
    got_count = sum(d["got_reply"] for d in details)
    avg = sum(times) / len(times) if times else 0
    p50 = sorted(times)[len(times) // 2] if times else 0
    p95 = sorted(times)[-1] if times else 0

    passed = got_count >= 4
    print(f"\n  Responses: {got_count}/5 | Avg: {avg:.1f}s | P50: {p50:.1f}s | P95: {p95:.1f}s")

    return record("R10", "Load test", passed, {
        "details": details, "avg_s": round(avg, 1), "p50_s": round(p50, 1), "p95_s": round(p95, 1),
        "response_count": got_count,
    })


# ═══════════════════════════════════════════════════════════════════════════
# Report Generator
# ═══════════════════════════════════════════════════════════════════════════

def generate_report() -> str:
    lines = [
        "# Lucy Round 2 Test Report",
        f"\n**Generated:** {now_utc()} / {now_ist()}",
        f"**Tests run:** {len(ALL_RESULTS)}",
        f"**Passed:** {sum(1 for r in ALL_RESULTS if r['passed'])}/{len(ALL_RESULTS)}",
        "\n---\n",
        "| Test | Name | Status | Notes |",
        "|------|------|--------|-------|",
    ]
    for r in ALL_RESULTS:
        status = "PASS" if r["passed"] else "FAIL"
        elapsed = r.get("store_elapsed_s", r.get("avg_s", "-"))
        lines.append(f"| {r['test']} | {r['name']} | {status} | {elapsed} |")

    lines.extend(["\n---\n"])
    for r in ALL_RESULTS:
        lines.append(f"## {r['test']}: {r['name']}")
        lines.append(f"**Status:** {'PASS' if r['passed'] else 'FAIL'} | **Time:** {r['timestamp_utc']}")
        for k, v in r.items():
            if k in ("test", "name", "passed", "timestamp_utc", "timestamp_ist"):
                continue
            if isinstance(v, str) and len(v) > 200:
                v = v[:200] + "..."
            lines.append(f"- {k}: {v}")
        lines.append("")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

async def main() -> None:
    selected = set(sys.argv[1:]) if len(sys.argv) > 1 else set()
    selected.discard("--report")
    if not selected:
        selected = {"R1", "R2", "R3", "R3L", "R4", "R5", "R6", "R7", "R8", "R9", "R10"}
    selected = {s.upper() for s in selected}

    print(f"\nLucy Round 2 Tests — {now_utc()}")
    print(f"Selected: {sorted(selected)}\n")

    if "R2" in selected:
        test_r2_memory_classification()
    if "R3" in selected:
        test_r3_reactions_offline()
    if "R4" in selected:
        test_r4_rich_formatting()
    if "R5" in selected:
        test_r5_ux_micro()
    if "R6" in selected:
        test_r6_tone_pipeline()
    if "R9" in selected:
        test_r9_composio_isolation()

    if "R1" in selected:
        await test_r1_memory_persistence()
    if "R3L" in selected:
        await test_r3_reactions_live()
    if "R7" in selected:
        await test_r7_concurrent_memory()
    if "R8" in selected:
        await test_r8_thread_isolation()
    if "R10" in selected:
        await test_r10_load()

    print("\n" + "=" * 70)
    print("FINAL SUMMARY")
    print("=" * 70)
    for r in ALL_RESULTS:
        print(f"  [{'PASS' if r['passed'] else 'FAIL'}] {r['test']}: {r['name']}")
    passed = sum(1 for r in ALL_RESULTS if r["passed"])
    print(f"\n  Total: {passed}/{len(ALL_RESULTS)}")

    report = generate_report()
    report_path = Path(__file__).parent / "docs" / "tests" / "round2_test_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    print(f"  Report: {report_path}")


if __name__ == "__main__":
    asyncio.run(main())
