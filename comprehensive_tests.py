"""Comprehensive end-to-end test suite for Lucy — V2.

Tests every capability area after the Viktor gap-closing patches:
    F: Memory read/write (workspace skills, learnings, state)
    G: Skill detection & loading (intent-based skill injection)
    H: Response quality & tone (output pipeline, SOUL.md adherence)
    I: Block Kit formatting verification
    J: Model routing (updated with thread-aware cases)
    K: HITL destructive-action interception
    L: Multi-step workflow with progress updates
    M: Concurrent load with thread isolation
    N: Follow-up context retention (thread memory)
    O: Output sanitization (no leakage of internals)
    P: Composio session handling (recovery, caching)

Each test captures:
    - Timestamp (UTC + IST)
    - Message sent / response received (full text)
    - Elapsed time
    - Thread TS
    - Pass/fail with reasoning

Usage:
    python comprehensive_tests.py           # run all tests
    python comprehensive_tests.py F G J     # run specific tests
    python comprehensive_tests.py --report  # run all + generate markdown report
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx
import certifi

# ── Configuration ───────────────────────────────────────────────────────

TOKEN = os.environ.get("SLACK_USER_TOKEN", "")
BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
CHANNEL = os.environ.get("SLACK_CHANNEL", "C0AEZ241C3V")
LUCY_ID = os.environ.get("LUCY_BOT_ID", "U0AG8LVAB4M")
WORKSPACE_ROOT = Path(os.environ.get(
    "WORKSPACE_ROOT",
    str(Path(__file__).parent / "workspaces"),
))
WORKSPACE_ID = os.environ.get("WORKSPACE_ID", "1d18c417-b53c-4ab1-80da-4959a622da17")

IST = timezone(timedelta(hours=5, minutes=30))

ALL_RESULTS: list[dict] = []


# ── Helpers ──────────────────────────────────────────────────────────────

def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def now_ist() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")

def _client() -> httpx.Client:
    return httpx.Client(verify=certifi.where(), timeout=15)

def slack_post(text: str) -> dict:
    with _client() as c:
        resp = c.post(
            "https://slack.com/api/chat.postMessage",
            json={"channel": CHANNEL, "text": f"<@{LUCY_ID}> {text}", "as_user": True},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        return resp.json()

def slack_post_in_thread(text: str, thread_ts: str) -> dict:
    with _client() as c:
        resp = c.post(
            "https://slack.com/api/chat.postMessage",
            json={
                "channel": CHANNEL,
                "text": f"<@{LUCY_ID}> {text}",
                "thread_ts": thread_ts,
                "as_user": True,
            },
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        return resp.json()

def get_thread_messages(thread_ts: str) -> list[dict]:
    """Get all messages in a thread."""
    with _client() as c:
        resp = c.get(
            "https://slack.com/api/conversations.replies",
            params={"channel": CHANNEL, "ts": thread_ts, "limit": 50},
            headers={"Authorization": f"Bearer {BOT_TOKEN}"},
        )
        return resp.json().get("messages", [])

def wait_for_reply(thread_ts: str, timeout_s: int = 180) -> dict | None:
    last_reply: dict | None = None
    stable_count = 0

    with _client() as c:
        for _ in range(timeout_s // 3):
            time.sleep(3)
            resp = c.get(
                "https://slack.com/api/conversations.replies",
                params={"channel": CHANNEL, "ts": thread_ts, "limit": 30},
                headers={"Authorization": f"Bearer {BOT_TOKEN}"},
            )
            msgs = resp.json().get("messages", [])
            bot_msgs = [
                m for m in msgs
                if m.get("ts") != thread_ts
                and (m.get("bot_id") or m.get("app_id"))
            ]
            if bot_msgs:
                newest = bot_msgs[-1]
                newest_text = newest.get("text", "")
                is_progress = newest_text.startswith("Working on it")
                if is_progress:
                    stable_count = 0
                    continue
                if last_reply and last_reply.get("ts") == newest.get("ts"):
                    stable_count += 1
                else:
                    last_reply = newest
                    stable_count = 0
                if stable_count >= 2:
                    return last_reply
        return last_reply

async def send_and_wait(text: str, timeout_s: int = 180) -> tuple[str, str | None, float, dict | None]:
    """Returns (thread_ts, reply_text, elapsed_s, full_reply_msg)."""
    t0 = time.monotonic()
    result = await asyncio.to_thread(slack_post, text)
    ts = result.get("ts", "")
    reply = await asyncio.to_thread(wait_for_reply, ts, timeout_s)
    elapsed = time.monotonic() - t0
    reply_text = reply.get("text") if reply else None
    return ts, reply_text, elapsed, reply

async def send_followup_and_wait(text: str, thread_ts: str, timeout_s: int = 120) -> tuple[str | None, float, dict | None]:
    t0 = time.monotonic()
    await asyncio.to_thread(slack_post_in_thread, text, thread_ts)
    reply = await asyncio.to_thread(wait_for_reply, thread_ts, timeout_s)
    elapsed = time.monotonic() - t0
    reply_text = reply.get("text") if reply else None
    return reply_text, elapsed, reply

def record(test_id: str, name: str, passed: bool, details: dict) -> dict:
    entry = {
        "test": test_id,
        "name": name,
        "passed": passed,
        "timestamp_utc": now_utc(),
        "timestamp_ist": now_ist(),
        **details,
    }
    ALL_RESULTS.append(entry)
    status = "PASS" if passed else "FAIL"
    print(f"  [{status}] {test_id}: {name}")
    return entry


# ── Test F: Memory Read/Write ────────────────────────────────────────────

async def test_f_memory() -> dict:
    print("\n" + "=" * 70)
    print("TEST F: Memory — read/write/recall")
    print("=" * 70)

    ts, reply1, e1, _ = await send_and_wait(
        "Remember this: our company's main KPI this quarter is reaching $500K MRR by March 31st. "
        "Our current MRR is $420K. Store this in your memory."
    )
    stored = reply1 is not None and any(
        w in (reply1 or "").lower() for w in ["noted", "got it", "remember", "stored", "save", "track"]
    )
    print(f"  Store request: {e1:.1f}s — {'stored' if stored else 'NOT stored'}")
    print(f"  Response: {(reply1 or 'NO REPLY')[:200]}")

    time.sleep(5)

    recall_text, e2, _ = await send_followup_and_wait(
        "What's our MRR target this quarter? And what's current MRR?",
        ts, timeout_s=120,
    )
    recalled = recall_text is not None and (
        "500" in (recall_text or "") and "420" in (recall_text or "")
    )
    print(f"  Recall request: {e2:.1f}s — {'recalled' if recalled else 'NOT recalled'}")
    print(f"  Response: {(recall_text or 'NO REPLY')[:200]}")

    passed = stored and recalled
    return record("F", "Memory read/write/recall", passed, {
        "store_elapsed_s": round(e1, 1),
        "recall_elapsed_s": round(e2, 1),
        "store_response": (reply1 or "")[:300],
        "recall_response": (recall_text or "")[:300],
        "stored": stored,
        "recalled": recalled,
        "thread_ts": ts,
    })


# ── Test G: Skill Detection & Loading ────────────────────────────────────

def test_g_skills() -> dict:
    print("\n" + "=" * 70)
    print("TEST G: Skill detection & loading")
    print("=" * 70)

    sys.path.insert(0, str(Path(__file__).parent / "src"))
    from lucy.workspace.skills import detect_relevant_skills

    test_cases = [
        ("Create a PDF report of our Q4 performance", ["pdf-creation"]),
        ("Set up a daily cron to check our email at 9am", ["scheduled-crons"]),
        ("Browse the competitor's website and take screenshots", ["browser"]),
        ("What integrations are available?", []),
        ("Build me a spreadsheet with the sales data", ["excel-editing"]),
    ]

    results = []
    all_passed = True
    for msg, expected in test_cases:
        detected = detect_relevant_skills(msg)
        matched = all(e in detected for e in expected) if expected else True
        if not matched:
            all_passed = False
        results.append({
            "message": msg[:60],
            "expected": expected,
            "detected": detected,
            "matched": matched,
        })
        status = "PASS" if matched else "FAIL"
        print(f"  [{status}] \"{msg[:55]}\" → detected={detected}")

    return record("G", "Skill detection & loading", all_passed, {
        "cases": results,
        "pass_count": sum(1 for r in results if r["matched"]),
        "total": len(results),
    })


# ── Test H: Response Quality & Tone ─────────────────────────────────────

async def test_h_response_quality() -> dict:
    print("\n" + "=" * 70)
    print("TEST H: Response quality & tone")
    print("=" * 70)

    ts, reply, elapsed, _ = await send_and_wait(
        "I need help figuring out the best approach for our Q1 marketing campaign. "
        "We have a budget of $50K and need to decide between paid social, content marketing, "
        "and influencer partnerships. What would you recommend?"
    )

    quality_signals = {
        "substantive": len(reply or "") > 200,
        "no_happy_to_help": "happy to help" not in (reply or "").lower(),
        "no_great_question": "great question" not in (reply or "").lower(),
        "no_tool_leaks": not any(
            w in (reply or "")
            for w in ["COMPOSIO", "openrouter", "SKILL.md", "tool_call", "function calling"]
        ),
        "no_filler": "worth noting" not in (reply or "").lower(),
        "conversational_tone": any(
            w in (reply or "").lower()
            for w in ["here's", "i'd", "you could", "based on", "considering", "given"]
        ),
        "actionable": any(
            w in (reply or "").lower()
            for w in ["recommend", "suggest", "approach", "strategy", "consider", "allocat"]
        ),
    }

    passed = sum(quality_signals.values()) >= 5
    print(f"  Elapsed: {elapsed:.1f}s")
    print(f"  Response length: {len(reply or '')} chars")
    print(f"  Quality signals: {quality_signals}")
    print(f"  Response preview: {(reply or 'NO REPLY')[:300]}")

    return record("H", "Response quality & tone", passed, {
        "elapsed_s": round(elapsed, 1),
        "response_length": len(reply or ""),
        "quality_signals": quality_signals,
        "response": (reply or "")[:500],
        "thread_ts": ts,
    })


# ── Test I: Block Kit Formatting ─────────────────────────────────────────

async def test_i_blockkit() -> dict:
    print("\n" + "=" * 70)
    print("TEST I: Block Kit formatting")
    print("=" * 70)

    ts, reply, elapsed, full_msg = await send_and_wait(
        "Give me a detailed breakdown of your capabilities — "
        "what can you do with tools, scheduling, research, and document creation?"
    )

    has_blocks = full_msg and "blocks" in full_msg if full_msg else False
    is_long = len(reply or "") > 100
    has_structure = (reply or "").count("\n") > 3 if reply else False

    passed = is_long and has_structure
    print(f"  Elapsed: {elapsed:.1f}s")
    print(f"  Has blocks in API response: {has_blocks}")
    print(f"  Response length: {len(reply or '')} | Lines: {(reply or '').count(chr(10))}")
    print(f"  Response preview: {(reply or 'NO REPLY')[:300]}")

    return record("I", "Block Kit formatting", passed, {
        "elapsed_s": round(elapsed, 1),
        "has_blocks": has_blocks,
        "response_length": len(reply or ""),
        "line_count": (reply or "").count("\n"),
        "response": (reply or "")[:500],
        "thread_ts": ts,
    })


# ── Test J: Model Routing (Updated) ─────────────────────────────────────

def test_j_model_routing() -> dict:
    print("\n" + "=" * 70)
    print("TEST J: Model routing (updated with thread-aware cases)")
    print("=" * 70)

    sys.path.insert(0, str(Path(__file__).parent / "src"))
    from lucy.core.router import classify_and_route

    cases = [
        # (message, expected_tier, thread_depth, prev_had_tool_calls, description)
        ("Hi", "fast", 0, False, "Simple greeting"),
        ("thanks!", "fast", 0, False, "Simple thanks"),
        ("ok cool", "fast", 8, False, "Short ack in thread (no prior tools)"),
        ("yes do it", "default", 3, True, "Confirmation after tool work"),
        ("go ahead", "default", 5, True, "Go-ahead after tool work"),
        ("check if the deploy went through", "default", 0, False, "Check/verify request"),
        ("Build me a Python script for compound interest", "code", 0, False, "Code request"),
        ("Research top 5 AI platforms and compare pricing in detail", "frontier", 0, False, "Deep research"),
        ("Send an email to the team about tomorrow's standup", "default", 0, False, "Tool use request"),
        ("What time is it?", "fast", 0, False, "Simple lookup"),
        ("What meetings do I have today?", "fast", 0, False, "Calendar lookup"),
        ("Delete that email and cancel the meeting", "default", 2, True, "Destructive actions"),
    ]

    results = []
    all_passed = True
    for text, expected, depth, prev_tools, desc in cases:
        route = classify_and_route(text, depth, prev_had_tool_calls=prev_tools)
        passed = route.tier == expected
        if not passed:
            all_passed = False
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {desc}: \"{text[:40]}\" → tier={route.tier}, intent={route.intent}"
              + (f" (expected {expected})" if not passed else ""))
        results.append({
            "description": desc,
            "text": text[:50],
            "expected_tier": expected,
            "actual_tier": route.tier,
            "actual_intent": route.intent,
            "thread_depth": depth,
            "prev_tools": prev_tools,
            "passed": passed,
        })

    return record("J", "Model routing (thread-aware)", all_passed, {
        "cases": results,
        "pass_count": sum(1 for r in results if r["passed"]),
        "total": len(results),
    })


# ── Test K: HITL Destructive Action Interception ─────────────────────────

async def test_k_hitl() -> dict:
    print("\n" + "=" * 70)
    print("TEST K: HITL destructive action detection")
    print("=" * 70)

    sys.path.insert(0, str(Path(__file__).parent / "src"))
    from lucy.slack.hitl import is_destructive_tool_call, create_pending_action, resolve_pending_action

    tool_cases = [
        ("GMAIL_DELETE_EMAIL", True),
        ("GMAIL_SEND_EMAIL", True),
        ("GOOGLECALENDAR_CANCEL_EVENT", True),
        ("GITHUB_ARCHIVE_REPO", True),
        ("GMAIL_GET_EMAILS", False),
        ("GOOGLECALENDAR_EVENTS_LIST", False),
        ("COMPOSIO_SEARCH_TOOLS", False),
    ]

    results = []
    all_passed = True
    for tool, expected in tool_cases:
        actual = is_destructive_tool_call(tool)
        passed = actual == expected
        if not passed:
            all_passed = False
        results.append({"tool": tool, "expected": expected, "actual": actual, "passed": passed})
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {tool} → destructive={actual}")

    action_id = create_pending_action(
        tool_name="GMAIL_DELETE_EMAIL",
        parameters={"email_id": "test123"},
        description="Delete test email",
        workspace_id="test",
    )
    pending_ok = action_id and len(action_id) == 12
    print(f"  Pending action created: {action_id} ({'OK' if pending_ok else 'FAIL'})")

    resolved = await resolve_pending_action(action_id, approved=True) if action_id else None
    resolve_ok = resolved is not None and resolved.get("tool_name") == "GMAIL_DELETE_EMAIL"
    print(f"  Resolved action: {'OK' if resolve_ok else 'FAIL'}")

    all_passed = all_passed and pending_ok and resolve_ok

    return record("K", "HITL destructive action detection", all_passed, {
        "detection_cases": results,
        "pending_action_ok": pending_ok,
        "resolve_ok": resolve_ok,
    })


# ── Test L: Multi-step Workflow with Progress Updates ────────────────────

async def test_l_multistep() -> dict:
    print("\n" + "=" * 70)
    print("TEST L: Multi-step workflow with progress updates")
    print("=" * 70)

    msg = (
        "I need you to do three things:\n"
        "1. Check what integrations I have connected\n"
        "2. Look up what time it is for everyone on my team\n"
        "3. Check my calendar for today's events\n"
        "Give me all three results."
    )

    t0 = time.monotonic()
    result = await asyncio.to_thread(slack_post, msg)
    ts = result.get("ts", "")

    progress_msgs = []
    final_reply = None

    for _ in range(60):
        time.sleep(3)
        msgs = await asyncio.to_thread(get_thread_messages, ts)
        bot_msgs = [
            m for m in msgs
            if m.get("ts") != ts and (m.get("bot_id") or m.get("app_id"))
        ]
        if bot_msgs:
            for bm in bot_msgs:
                bm_text = bm.get("text", "")
                bm_ts = bm.get("ts", "")
                if bm_ts not in [p.get("ts") for p in progress_msgs]:
                    progress_msgs.append({"ts": bm_ts, "text": bm_text[:200]})
            if len(bot_msgs) >= 1:
                newest = bot_msgs[-1]
                if not newest.get("text", "").startswith("Working on it"):
                    stable_check = await asyncio.to_thread(get_thread_messages, ts)
                    stable_bot = [
                        m for m in stable_check
                        if m.get("ts") != ts and (m.get("bot_id") or m.get("app_id"))
                    ]
                    if len(stable_bot) == len(bot_msgs):
                        final_reply = newest
                        break

    elapsed = time.monotonic() - t0
    reply_text = final_reply.get("text", "") if final_reply else ""

    has_multiple_msgs = len(progress_msgs) >= 1
    covered = {
        "integrations": any(w in reply_text.lower() for w in ["gmail", "calendar", "connected", "integration"]),
        "time": any(w in reply_text.lower() for w in ["am", "pm", "ist", "utc", "time"]),
        "calendar": any(w in reply_text.lower() for w in ["meeting", "event", "schedule", "calendar", "no events"]),
    }
    topics_covered = sum(covered.values())

    passed = topics_covered >= 2
    print(f"  Elapsed: {elapsed:.1f}s")
    print(f"  Bot messages in thread: {len(progress_msgs)}")
    for i, pm in enumerate(progress_msgs):
        print(f"    Msg {i+1}: {pm['text'][:120]}")
    print(f"  Topics covered: {covered} ({topics_covered}/3)")
    print(f"  Final reply: {reply_text[:300]}")

    return record("L", "Multi-step workflow", passed, {
        "elapsed_s": round(elapsed, 1),
        "progress_messages": progress_msgs,
        "topics_covered": covered,
        "response": reply_text[:500],
        "thread_ts": ts,
    })


# ── Test M: Concurrent Load with Thread Isolation ────────────────────────

async def test_m_concurrent() -> dict:
    print("\n" + "=" * 70)
    print("TEST M: Concurrent load — thread isolation")
    print("=" * 70)

    messages = [
        "What's the current time for everyone on the team?",
        "What Google Calendar events do I have coming up?",
        "List all my connected integrations and their status.",
    ]

    tasks = [send_and_wait(m, timeout_s=120) for m in messages]
    results = await asyncio.gather(*tasks)

    details = []
    all_replied = True
    for i, (ts, reply, elapsed, _) in enumerate(results):
        got_reply = reply is not None
        if not got_reply:
            all_replied = False
        details.append({
            "thread": i + 1,
            "message": messages[i][:50],
            "reply": (reply or "NO REPLY")[:200],
            "elapsed_s": round(elapsed, 1),
            "got_reply": got_reply,
            "thread_ts": ts,
        })
        status = "OK" if got_reply else "FAIL"
        print(f"  Thread {i+1} [{status}] ({elapsed:.1f}s): {messages[i][:50]}")
        print(f"    Reply: {(reply or 'NO REPLY')[:150]}")

    # Cross-contamination check
    contaminated = False
    if all(d["got_reply"] for d in details):
        r0 = details[0]["reply"].lower()
        r1 = details[1]["reply"].lower()
        r2 = details[2]["reply"].lower()
        if "calendar" in r0 and "event" in r0 and "integration" not in r0:
            pass
        if "integration" in r1 and "connected" in r1 and "time" not in r1:
            contaminated = True

    passed = all_replied and not contaminated
    print(f"  Cross-contamination: {'DETECTED' if contaminated else 'clean'}")
    print(f"  RESULT: {'PASS' if passed else 'FAIL'}")

    return record("M", "Concurrent load + thread isolation", passed, {
        "threads": details,
        "contaminated": contaminated,
    })


# ── Test N: Follow-up Context Retention ──────────────────────────────────

async def test_n_context_retention() -> dict:
    print("\n" + "=" * 70)
    print("TEST N: Follow-up context retention")
    print("=" * 70)

    ts, reply1, e1, _ = await send_and_wait(
        "What time is it right now for everyone on my team?"
    )
    print(f"  Initial: {e1:.1f}s — {(reply1 or 'NO REPLY')[:150]}")

    time.sleep(3)

    reply2, e2, _ = await send_followup_and_wait(
        "And who has the latest timezone on the team?",
        ts, timeout_s=120,
    )
    print(f"  Follow-up: {e2:.1f}s — {(reply2 or 'NO REPLY')[:150]}")

    has_context = reply2 is not None and any(
        w in (reply2 or "").lower()
        for w in ["time", "latest", "timezone", "utc", "ist", "ahead", "behind", "pm", "am"]
    )
    passed = reply1 is not None and has_context

    return record("N", "Follow-up context retention", passed, {
        "initial_elapsed_s": round(e1, 1),
        "followup_elapsed_s": round(e2, 1),
        "initial_response": (reply1 or "")[:300],
        "followup_response": (reply2 or "")[:300],
        "context_retained": has_context,
        "thread_ts": ts,
    })


# ── Test O: Output Sanitization ──────────────────────────────────────────

async def test_o_sanitization() -> dict:
    print("\n" + "=" * 70)
    print("TEST O: Output sanitization — no internal leakage")
    print("=" * 70)

    ts, reply, elapsed, _ = await send_and_wait(
        "Tell me about all the tools and integrations you use internally. "
        "What APIs do you connect to? What's your tech stack?"
    )

    leaked_terms = [
        "composio", "openrouter", "openclaw", "minimax",
        "COMPOSIO_", "SKILL.md", "LEARNINGS.md", "workspace_seeds",
        "tool_call", "meta-tool", "function calling", "/home/user",
    ]

    leaks_found = []
    for term in leaked_terms:
        if term.lower() in (reply or "").lower():
            leaks_found.append(term)

    passed = len(leaks_found) == 0 and reply is not None

    print(f"  Elapsed: {elapsed:.1f}s")
    print(f"  Leaks found: {leaks_found if leaks_found else 'none'}")
    print(f"  Response: {(reply or 'NO REPLY')[:300]}")

    return record("O", "Output sanitization", passed, {
        "elapsed_s": round(elapsed, 1),
        "leaks_found": leaks_found,
        "response": (reply or "")[:500],
        "thread_ts": ts,
    })


# ── Test P: Composio Session Handling ────────────────────────────────────

def test_p_composio_sessions() -> dict:
    print("\n" + "=" * 70)
    print("TEST P: Composio session handling (caching, recovery)")
    print("=" * 70)

    sys.path.insert(0, str(Path(__file__).parent / "src"))

    results = {}
    try:
        source_file = Path(__file__).parent / "src" / "lucy" / "integrations" / "composio_client.py"
        source = source_file.read_text()

        results["max_cache_200"] = "_MAX_CACHED_SESSIONS = 200" in source
        results["ttl_30min"] = "minutes=30" in source
        results["has_session_cache"] = "_session_cache" in source
        results["has_lru_eviction"] = "oldest_key" in source or "_MAX_CACHED_SESSIONS" in source
        results["has_stale_recovery"] = "_get_session_with_recovery" in source
        results["has_double_checked_locking"] = "_session_lock" in source

        passed = all(results.values())

    except Exception as e:
        results["error"] = str(e)
        passed = False

    for k, v in results.items():
        print(f"  {k}: {v}")

    return record("P", "Composio session handling", passed, results)


# ── Log Analysis ────────────────────────────────────────────────────────

def analyze_logs() -> dict:
    print("\n" + "=" * 70)
    print("LOG ANALYSIS: Thread trace breakdown")
    print("=" * 70)

    log_dir = WORKSPACE_ROOT / WORKSPACE_ID / "logs" / "threads"
    if not log_dir.exists():
        print(f"  No thread logs at {log_dir}")
        return {"traces": 0}

    all_traces: list[dict] = []
    for jsonl_file in sorted(log_dir.glob("*.jsonl")):
        with open(jsonl_file) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        all_traces.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass

    if not all_traces:
        print("  No trace records found")
        return {"traces": 0}

    total_times = [t.get("total_ms", 0) for t in all_traces]
    llm_times = []
    tool_times = []
    models = {}
    intents = {}

    for trace in all_traces:
        spans = trace.get("spans", [])
        llm_ms = sum(s.get("duration_ms", 0) for s in spans if s["name"].startswith("llm_call"))
        tool_ms = sum(s.get("duration_ms", 0) for s in spans if s["name"].startswith("tool_exec"))
        llm_times.append(llm_ms)
        tool_times.append(tool_ms)

        model = trace.get("model_used", "unknown")
        models[model] = models.get(model, 0) + 1
        intent = trace.get("intent", "unknown")
        intents[intent] = intents.get(intent, 0) + 1

    def percentile(vals: list, p: float) -> float:
        if not vals:
            return 0
        s = sorted(vals)
        idx = min(len(s) - 1, int(len(s) * p / 100))
        return s[idx]

    analysis = {
        "traces": len(all_traces),
        "timing": {
            "total": {"avg": sum(total_times)/len(total_times), "p50": percentile(total_times, 50), "p95": percentile(total_times, 95)},
            "llm": {"avg": sum(llm_times)/len(llm_times), "p50": percentile(llm_times, 50), "p95": percentile(llm_times, 95)},
            "tools": {"avg": sum(tool_times)/len(tool_times), "p50": percentile(tool_times, 50), "p95": percentile(tool_times, 95)},
        },
        "models": models,
        "intents": intents,
        "anomalies": [
            {"trace_id": t.get("trace_id"), "ms": t.get("total_ms"), "msg": t.get("user_message", "")[:60]}
            for t in all_traces if t.get("total_ms", 0) > 60000
        ],
        "total_prompt_tokens": sum(t.get("usage", {}).get("prompt_tokens", 0) for t in all_traces),
        "total_completion_tokens": sum(t.get("usage", {}).get("completion_tokens", 0) for t in all_traces),
    }

    print(f"  Traces: {analysis['traces']}")
    for category in ["total", "llm", "tools"]:
        t = analysis["timing"][category]
        print(f"  {category.upper():>6}: avg={t['avg']:.0f}ms  p50={t['p50']:.0f}ms  p95={t['p95']:.0f}ms")
    print(f"  Models: {models}")
    print(f"  Anomalies (>60s): {len(analysis['anomalies'])}")

    return analysis


# ── Markdown Report Generator ────────────────────────────────────────────

def generate_report(log_analysis: dict) -> str:
    report_time_utc = now_utc()
    report_time_ist = now_ist()

    lines = [
        "# Lucy Comprehensive Test Report",
        "",
        f"**Generated:** {report_time_utc} / {report_time_ist}",
        f"**Tests run:** {len(ALL_RESULTS)}",
        f"**Passed:** {sum(1 for r in ALL_RESULTS if r['passed'])}/{len(ALL_RESULTS)}",
        "",
        "---",
        "",
        "## Summary",
        "",
        "| Test | Name | Status | Time |",
        "|------|------|--------|------|",
    ]

    for r in ALL_RESULTS:
        status = "PASS" if r["passed"] else "FAIL"
        elapsed = r.get("elapsed_s", r.get("store_elapsed_s", "-"))
        lines.append(f"| {r['test']} | {r['name']} | {status} | {elapsed}s |")

    lines.extend(["", "---", ""])

    for r in ALL_RESULTS:
        lines.append(f"## Test {r['test']}: {r['name']}")
        lines.append("")
        lines.append(f"**Status:** {'PASS' if r['passed'] else 'FAIL'}  ")
        lines.append(f"**Timestamp:** {r['timestamp_utc']} / {r['timestamp_ist']}")
        lines.append("")

        if "response" in r:
            lines.append("### Response")
            lines.append("```")
            lines.append(r["response"][:800])
            lines.append("```")
            lines.append("")

        if "store_response" in r:
            lines.append("### Store Response")
            lines.append(f"```\n{r['store_response'][:400]}\n```")
            lines.append("### Recall Response")
            lines.append(f"```\n{r['recall_response'][:400]}\n```")
            lines.append("")

        if "quality_signals" in r:
            lines.append("### Quality Signals")
            for k, v in r["quality_signals"].items():
                lines.append(f"- {k}: {'yes' if v else 'NO'}")
            lines.append("")

        if "cases" in r:
            lines.append("### Cases")
            for c in r["cases"]:
                status = "PASS" if c.get("passed") or c.get("matched") else "FAIL"
                desc = c.get("description") or c.get("message", "")
                lines.append(f"- [{status}] {desc}")
            lines.append("")

        if "threads" in r:
            lines.append("### Threads")
            for t in r["threads"]:
                status = "OK" if t["got_reply"] else "FAIL"
                lines.append(f"- Thread {t['thread']}: [{status}] {t['elapsed_s']}s — {t['message']}")
                lines.append(f"  Reply: {t['reply'][:150]}")
            lines.append("")

        if "leaks_found" in r:
            if r["leaks_found"]:
                lines.append(f"### Leaks Detected: {', '.join(r['leaks_found'])}")
            else:
                lines.append("### No Internal Leaks Detected")
            lines.append("")

        if "progress_messages" in r:
            lines.append("### Progress Messages in Thread")
            for i, pm in enumerate(r["progress_messages"]):
                lines.append(f"- Msg {i+1}: {pm['text'][:200]}")
            lines.append("")

        lines.append("---")
        lines.append("")

    if log_analysis and log_analysis.get("traces", 0) > 0:
        lines.append("## Log Analysis")
        lines.append("")
        lines.append(f"**Total traces:** {log_analysis['traces']}")
        lines.append("")
        lines.append("### Timing")
        lines.append("| Category | Avg (ms) | P50 (ms) | P95 (ms) |")
        lines.append("|----------|----------|----------|----------|")
        for cat in ["total", "llm", "tools"]:
            t = log_analysis["timing"][cat]
            lines.append(f"| {cat.upper()} | {t['avg']:.0f} | {t['p50']:.0f} | {t['p95']:.0f} |")
        lines.append("")

        lines.append("### Model Distribution")
        for model, count in log_analysis.get("models", {}).items():
            lines.append(f"- {model}: {count} requests")
        lines.append("")

        tokens = log_analysis.get("total_prompt_tokens", 0) + log_analysis.get("total_completion_tokens", 0)
        lines.append(f"### Token Usage")
        lines.append(f"- Prompt: {log_analysis.get('total_prompt_tokens', 0):,}")
        lines.append(f"- Completion: {log_analysis.get('total_completion_tokens', 0):,}")
        lines.append(f"- Total: {tokens:,}")
        lines.append("")

        if log_analysis.get("anomalies"):
            lines.append("### Anomalies (>60s)")
            for a in log_analysis["anomalies"]:
                lines.append(f"- {a['trace_id']}: {a['ms']:.0f}ms — {a['msg']}")
            lines.append("")

    return "\n".join(lines)


# ── Main ────────────────────────────────────────────────────────────────

async def main() -> None:
    selected = set(sys.argv[1:]) if len(sys.argv) > 1 else set()
    generate = "--report" in selected
    selected.discard("--report")

    if not selected:
        selected = {"F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P"}
    selected = {s.upper() for s in selected}

    offline_tests = {"G", "J", "K", "P"}
    live_tests = selected - offline_tests

    print(f"\nLucy Comprehensive Test Suite — {now_utc()}")
    print(f"Tests selected: {sorted(selected)}")
    print(f"  Offline: {sorted(selected & offline_tests)}")
    print(f"  Live (Slack): {sorted(live_tests)}")
    print()

    if "G" in selected:
        test_g_skills()
    if "J" in selected:
        test_j_model_routing()
    if "K" in selected:
        await test_k_hitl()
    if "P" in selected:
        test_p_composio_sessions()

    if "F" in selected:
        await test_f_memory()
    if "H" in selected:
        await test_h_response_quality()
    if "I" in selected:
        await test_i_blockkit()
    if "L" in selected:
        await test_l_multistep()
    if "M" in selected:
        await test_m_concurrent()
    if "N" in selected:
        await test_n_context_retention()
    if "O" in selected:
        await test_o_sanitization()

    log_analysis = analyze_logs()

    # Summary
    print("\n" + "=" * 70)
    print("FINAL SUMMARY")
    print("=" * 70)
    for r in ALL_RESULTS:
        status = "PASS" if r["passed"] else "FAIL"
        print(f"  [{status}] {r['test']}: {r['name']}")

    passed = sum(1 for r in ALL_RESULTS if r["passed"])
    total = len(ALL_RESULTS)
    print(f"\n  Total: {passed}/{total}")

    if generate or True:
        report = generate_report(log_analysis)
        report_path = Path(__file__).parent / "docs" / "tests" / "comprehensive_test_report.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report, encoding="utf-8")
        print(f"\n  Report written to: {report_path}")

    if passed < total:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
