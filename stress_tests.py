"""Stress tests for Lucy — concurrency, workflows, model routing, and log analysis.

Tests:
    A: Three concurrent threads
    B: Complex sequential workflow (multi-step tool chain)
    C: Parallel task (3 things at once)
    D: Model routing verification (pure Python, no Slack)
    E: Sustained load (5 messages across 3 threads in 30s)

After each live test, parses the per-thread JSONL logs and prints
timing breakdowns (per-request, p50/p95, anomalies).

Usage:
    python stress_tests.py          # run all tests
    python stress_tests.py A B      # run specific tests
    python stress_tests.py D        # router-only (no Slack needed)
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import sys
import time
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


# ── Slack helpers ───────────────────────────────────────────────────────

def _client() -> httpx.Client:
    return httpx.Client(verify=certifi.where(), timeout=15)


def slack_post(text: str) -> dict:
    """Post a message mentioning Lucy. Returns Slack API response."""
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


def wait_for_reply(thread_ts: str, timeout_s: int = 180) -> dict | None:
    """Poll for a bot reply in a thread.

    Returns the LAST (most recent) bot reply, skipping intermediate
    progress updates.  Waits for the hourglass reaction to be removed
    (indicating processing is complete) or until timeout.
    """
    last_reply: dict | None = None
    stable_count = 0

    with _client() as c:
        for _ in range(timeout_s // 3):
            time.sleep(3)
            resp = c.get(
                "https://slack.com/api/conversations.replies",
                params={"channel": CHANNEL, "ts": thread_ts, "limit": 20},
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


async def async_send_and_wait(text: str, timeout_s: int = 180) -> tuple[str, str | None, float]:
    """Send a message and wait for reply. Returns (thread_ts, reply_text, elapsed_s)."""
    t0 = time.monotonic()
    result = await asyncio.to_thread(slack_post, text)
    ts = result.get("ts", "")

    reply = await asyncio.to_thread(wait_for_reply, ts, timeout_s)
    elapsed = time.monotonic() - t0
    reply_text = reply.get("text") if reply else None
    return ts, reply_text, elapsed


# ── Test A: Three concurrent threads ────────────────────────────────────

async def test_a_concurrent_threads() -> dict:
    """Send 3 messages simultaneously to 3 different threads."""
    print("\n" + "=" * 70)
    print("TEST A: Three concurrent threads")
    print("=" * 70)

    messages = [
        "What time is it for everyone on the team?",
        "What integrations do I currently have connected?",
        "Give me a quick summary of what you can do.",
    ]

    tasks = [async_send_and_wait(m, timeout_s=120) for m in messages]
    results = await asyncio.gather(*tasks)

    passed = True
    details = []
    for i, (ts, reply, elapsed) in enumerate(results):
        got_reply = reply is not None
        if not got_reply:
            passed = False
        details.append({
            "thread": i + 1,
            "message": messages[i][:50],
            "reply": (reply or "NO REPLY")[:150],
            "elapsed_s": round(elapsed, 1),
            "got_reply": got_reply,
            "thread_ts": ts,
        })
        status = "PASS" if got_reply else "FAIL"
        print(f"\n  Thread {i+1} [{status}] ({round(elapsed, 1)}s)")
        print(f"    Q: {messages[i][:60]}")
        print(f"    A: {(reply or 'NO REPLY')[:120]}")

    # Cross-contamination check
    if all(r["got_reply"] for r in details):
        replies = [d["reply"].lower() for d in details]
        contaminated = False
        if "integration" in replies[0] and "connected" in replies[0]:
            contaminated = True
        if "time" in replies[1] and "kolkata" in replies[1]:
            contaminated = True
        if contaminated:
            print("\n  WARNING: Possible cross-contamination detected")
            passed = False

    print(f"\n  RESULT: {'PASS' if passed else 'FAIL'}")
    return {"test": "A", "passed": passed, "details": details}


# ── Test B: Complex sequential workflow ─────────────────────────────────

async def test_b_sequential_workflow() -> dict:
    """Multi-step tool chain: calendar -> email -> confirm."""
    print("\n" + "=" * 70)
    print("TEST B: Complex sequential workflow")
    print("=" * 70)

    msg = (
        "Check my next meeting on Google Calendar, tell me who's attending, "
        "and draft an email asking them if we can reschedule to tomorrow "
        "at the same time. Show me the draft before sending."
    )

    ts, reply, elapsed = await async_send_and_wait(msg, timeout_s=180)

    got_reply = reply is not None
    used_tools = False
    if reply:
        indicators = ["meeting", "calendar", "attending", "email", "draft", "reschedule"]
        used_tools = sum(1 for w in indicators if w in reply.lower()) >= 2

    passed = got_reply and used_tools
    print(f"\n  Elapsed: {round(elapsed, 1)}s")
    print(f"  Reply: {(reply or 'NO REPLY')[:300]}")
    print(f"  Tool indicators found: {used_tools}")
    print(f"  RESULT: {'PASS' if passed else 'FAIL'}")

    return {
        "test": "B",
        "passed": passed,
        "elapsed_s": round(elapsed, 1),
        "thread_ts": ts,
    }


# ── Test C: Parallel task ──────────────────────────────────────────────

async def test_c_parallel_task() -> dict:
    """Single request requiring 3 parallel sub-tasks."""
    print("\n" + "=" * 70)
    print("TEST C: Parallel task (3 things at once)")
    print("=" * 70)

    msg = (
        "I need three things: "
        "1) What integrations do I have connected, "
        "2) What time is it for each team member, "
        "3) What's coming up on my calendar today"
    )

    ts, reply, elapsed = await async_send_and_wait(msg, timeout_s=180)

    answered = {"integrations": False, "time": False, "calendar": False}
    if reply:
        lower = reply.lower()
        answered["integrations"] = any(
            w in lower for w in ["gmail", "google calendar", "connected", "active"]
        )
        answered["time"] = any(
            w in lower for w in ["am", "pm", "ist", "utc", "kolkata"]
        )
        answered["calendar"] = any(
            w in lower for w in ["meeting", "calendar", "event", "schedule", "no events", "nothing scheduled"]
        )

    count = sum(answered.values())
    passed = count >= 2

    print(f"\n  Elapsed: {round(elapsed, 1)}s")
    print(f"  Reply: {(reply or 'NO REPLY')[:300]}")
    print(f"  Sub-tasks answered: {answered} ({count}/3)")
    print(f"  RESULT: {'PASS' if passed else 'FAIL'}")

    return {
        "test": "C",
        "passed": passed,
        "elapsed_s": round(elapsed, 1),
        "answered": answered,
        "thread_ts": ts,
    }


# ── Test D: Model routing (pure Python, no Slack) ──────────────────────

def test_d_model_routing() -> dict:
    """Verify the rule-based router selects correct tiers."""
    print("\n" + "=" * 70)
    print("TEST D: Model routing verification")
    print("=" * 70)

    sys.path.insert(0, str(Path(__file__).parent / "src"))
    from lucy.core.router import classify_and_route

    cases = [
        ("Hi", "chat", "fast"),
        ("thanks!", "chat", "fast"),
        ("ok cool", "followup", "fast"),  # thread_depth=8
        ("What time is it?", "lookup", "fast"),
        ("Build me a Python script that calculates compound interest", "code", "code"),
        ("Deploy my latest code to AWS Lambda", "code", "code"),
        (
            "Research the top 5 AI agent platforms and compare their pricing models in detail",
            "reasoning",
            "frontier",
        ),
        ("What meetings do I have today?", "lookup", "fast"),
        (
            "Send an email to the team about tomorrow's standup being moved to 10am",
            "tool_use",
            "default",
        ),
        ("Check my Google Calendar for open slots this week", "tool_use", "default"),
    ]

    results = []
    all_passed = True
    for text, expected_intent, expected_tier in cases:
        depth = 8 if text == "ok cool" else 0
        route = classify_and_route(text, depth)
        passed = route.tier == expected_tier
        if not passed:
            all_passed = False
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] \"{text[:50]}\" -> intent={route.intent}, tier={route.tier}"
              + (f" (expected {expected_tier})" if not passed else ""))
        results.append({
            "text": text[:50],
            "expected_tier": expected_tier,
            "actual_tier": route.tier,
            "actual_intent": route.intent,
            "passed": passed,
        })

    print(f"\n  RESULT: {'PASS' if all_passed else 'FAIL'} "
          f"({sum(r['passed'] for r in results)}/{len(results)})")

    return {"test": "D", "passed": all_passed, "cases": results}


# ── Test E: Sustained load ─────────────────────────────────────────────

async def test_e_sustained_load() -> dict:
    """5 messages across 3 threads within 30 seconds."""
    print("\n" + "=" * 70)
    print("TEST E: Sustained load (5 messages, 3 threads)")
    print("=" * 70)

    # Create 3 threads
    thread_messages = [
        "What integrations are connected?",
        "What time is it for each person on the team?",
        "Give me a summary of your capabilities.",
    ]

    print("  Phase 1: Sending initial 3 messages...")
    initial_tasks = [async_send_and_wait(m, timeout_s=120) for m in thread_messages]
    initial_results = await asyncio.gather(*initial_tasks)

    # Follow up in 2 of those threads
    thread_tss = [r[0] for r in initial_results]
    followup_messages = [
        (thread_tss[0], "Are there any others I should connect?"),
        (thread_tss[1], "What about Ryan specifically?"),
    ]

    print("  Phase 2: Sending 2 follow-ups in existing threads...")

    async def send_followup(thread_ts: str, text: str) -> tuple[str, str | None, float]:
        t0 = time.monotonic()
        await asyncio.to_thread(slack_post_in_thread, text, thread_ts)
        reply = await asyncio.to_thread(wait_for_reply, thread_ts, 120)
        # Get the last bot reply (might be multiple)
        elapsed = time.monotonic() - t0
        return thread_ts, reply.get("text") if reply else None, elapsed

    followup_tasks = [send_followup(ts, msg) for ts, msg in followup_messages]
    followup_results = await asyncio.gather(*followup_tasks)

    all_results = list(initial_results) + list(followup_results)
    response_times = [r[2] for r in all_results]
    got_replies = sum(1 for r in all_results if r[1] is not None)

    avg_time = sum(response_times) / len(response_times)
    p50 = sorted(response_times)[len(response_times) // 2]
    p95 = sorted(response_times)[int(len(response_times) * 0.95)]

    passed = got_replies >= 4  # Allow 1 miss

    print(f"\n  Responses: {got_replies}/{len(all_results)}")
    print(f"  Avg response time: {avg_time:.1f}s")
    print(f"  P50: {p50:.1f}s | P95: {p95:.1f}s")
    for i, (ts, reply, elapsed) in enumerate(all_results):
        label = f"Thread {i+1}" if i < 3 else f"Follow-up {i-2}"
        status = "OK" if reply else "NO REPLY"
        print(f"    {label}: {elapsed:.1f}s [{status}]")

    print(f"\n  RESULT: {'PASS' if passed else 'FAIL'}")

    return {
        "test": "E",
        "passed": passed,
        "response_count": got_replies,
        "avg_s": round(avg_time, 1),
        "p50_s": round(p50, 1),
        "p95_s": round(p95, 1),
        "times": [round(t, 1) for t in response_times],
    }


# ── Log Analysis ───────────────────────────────────────────────────────

def analyze_thread_logs() -> None:
    """Parse per-thread JSONL logs and print timing analysis."""
    print("\n" + "=" * 70)
    print("LOG ANALYSIS: Per-thread trace breakdown")
    print("=" * 70)

    log_dir = WORKSPACE_ROOT / WORKSPACE_ID / "logs" / "threads"
    if not log_dir.exists():
        print(f"  No thread logs found at {log_dir}")
        return

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
        return

    print(f"\n  Total traces: {len(all_traces)}")

    # Per-trace breakdown
    print("\n  Per-request breakdown:")
    print(f"  {'Trace ID':<14} {'Total':>8} {'LLM':>8} {'Tools':>8} {'Model':<30} {'Intent':<10} {'Tools#':>6}")
    print("  " + "-" * 94)

    total_times = []
    llm_times = []
    tool_times = []
    models_used = {}

    for trace in all_traces:
        total_ms = trace.get("total_ms", 0)
        total_times.append(total_ms)

        spans = trace.get("spans", [])
        llm_ms = sum(s.get("duration_ms", 0) for s in spans if s["name"].startswith("llm_call"))
        tool_ms = sum(s.get("duration_ms", 0) for s in spans if s["name"].startswith("tool_exec"))
        llm_times.append(llm_ms)
        tool_times.append(tool_ms)

        model = trace.get("model_used", "unknown")
        models_used[model] = models_used.get(model, 0) + 1

        print(
            f"  {trace.get('trace_id', '?'):<14} "
            f"{total_ms:>7.0f}ms "
            f"{llm_ms:>7.0f}ms "
            f"{tool_ms:>7.0f}ms "
            f"{model:<30} "
            f"{trace.get('intent', '?'):<10} "
            f"{len(trace.get('tool_calls_made', [])):>6}"
        )

    # Aggregates
    if total_times:
        print(f"\n  Aggregated timing:")
        for label, times in [("Total", total_times), ("LLM", llm_times), ("Tools", tool_times)]:
            if not times or all(t == 0 for t in times):
                continue
            sorted_t = sorted(times)
            avg = sum(sorted_t) / len(sorted_t)
            p50 = sorted_t[len(sorted_t) // 2]
            p95 = sorted_t[min(len(sorted_t) - 1, int(len(sorted_t) * 0.95))]
            print(f"    {label:>6}: avg={avg:>7.0f}ms  p50={p50:>7.0f}ms  p95={p95:>7.0f}ms")

    # Model distribution
    if models_used:
        print(f"\n  Model distribution:")
        for model, count in sorted(models_used.items(), key=lambda x: -x[1]):
            print(f"    {model:<35} {count} requests")

    # Anomalies
    anomalies = [t for t in all_traces if t.get("total_ms", 0) > 60_000]
    if anomalies:
        print(f"\n  ANOMALIES (>60s):")
        for t in anomalies:
            print(f"    {t.get('trace_id', '?')} — {t.get('total_ms', 0):.0f}ms — {t.get('user_message', '')[:60]}")

    # Token usage
    total_prompt = sum(t.get("usage", {}).get("prompt_tokens", 0) for t in all_traces)
    total_completion = sum(t.get("usage", {}).get("completion_tokens", 0) for t in all_traces)
    if total_prompt or total_completion:
        print(f"\n  Token usage: {total_prompt:,} prompt + {total_completion:,} completion = {total_prompt + total_completion:,} total")


# ── Main ────────────────────────────────────────────────────────────────

async def main() -> None:
    selected = set(sys.argv[1:]) if len(sys.argv) > 1 else {"A", "B", "C", "D", "E"}
    selected = {s.upper() for s in selected}

    results: list[dict] = []

    if "D" in selected:
        results.append(test_d_model_routing())

    if "A" in selected:
        results.append(await test_a_concurrent_threads())

    if "B" in selected:
        results.append(await test_b_sequential_workflow())

    if "C" in selected:
        results.append(await test_c_parallel_task())

    if "E" in selected:
        results.append(await test_e_sustained_load())

    # Analyze logs after live tests
    live_tests = selected - {"D"}
    if live_tests:
        analyze_thread_logs()

    # Summary
    print("\n" + "=" * 70)
    print("FINAL SUMMARY")
    print("=" * 70)
    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        print(f"  [{status}] Test {r['test']}")

    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    print(f"\n  Total: {passed}/{total}")

    if passed < total:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
