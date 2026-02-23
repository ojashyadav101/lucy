"""Round 3 Test Suite — Viktor PR 10-13 (applied from Viktor's actual patches).

Tests organized by PR:
  PR10: Request queue + priority system (core/request_queue.py)
  PR11: Fast path bypass (core/fast_path.py)
  PR12: Rate limiting layer (core/rate_limiter.py)
  PR13: Async task manager (core/task_manager.py)
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

RESULTS: list[dict] = []


def record(tid: str, name: str, pr: str, passed: bool, ms: float, details: str):
    icon = "PASS" if passed else "FAIL"
    print(f"  [{icon}] {tid}: {name} ({ms:.0f}ms) — {details}")
    RESULTS.append({"id": tid, "name": name, "pr": pr, "status": icon, "ms": round(ms, 1), "details": details})
    return passed


# ═══════════════════════════════════════════════════════════════
# PR 10: REQUEST QUEUE + PRIORITY SYSTEM
# ═══════════════════════════════════════════════════════════════


def test_pr10_01():
    t0 = time.perf_counter()
    try:
        from lucy.core.request_queue import RequestQueue, Priority, classify_priority, get_request_queue
        return record("PR10-01", "Queue module imports", "PR10", True, (time.perf_counter()-t0)*1000,
                       "RequestQueue, Priority, classify_priority, get_request_queue")
    except Exception as e:
        return record("PR10-01", "Queue module imports", "PR10", False, (time.perf_counter()-t0)*1000, str(e))


def test_pr10_02():
    t0 = time.perf_counter()
    from lucy.core.request_queue import Priority
    has_high = hasattr(Priority, "HIGH")
    has_normal = hasattr(Priority, "NORMAL")
    has_low = hasattr(Priority, "LOW")
    ok = has_high and has_normal and has_low
    return record("PR10-02", "3 priority levels (HIGH/NORMAL/LOW)", "PR10", ok,
                   (time.perf_counter()-t0)*1000, f"HIGH={has_high}, NORMAL={has_normal}, LOW={has_low}")


def test_pr10_03():
    t0 = time.perf_counter()
    from lucy.core.request_queue import classify_priority
    h = classify_priority("hi", "fast")
    n = classify_priority("check calendar", "default")
    l = classify_priority("research competitors", "frontier")
    ok = h.value < n.value < l.value
    return record("PR10-03", "Priority classification correct", "PR10", ok,
                   (time.perf_counter()-t0)*1000, f"fast→{h.name}, default→{n.name}, frontier→{l.name}")


def test_pr10_04():
    t0 = time.perf_counter()
    from lucy.core.request_queue import RequestQueue
    q = RequestQueue()
    has_metrics = hasattr(q, "metrics")
    has_busy = hasattr(q, "is_busy")
    ok = has_metrics and has_busy
    return record("PR10-04", "Queue has metrics + backpressure", "PR10", ok,
                   (time.perf_counter()-t0)*1000, f"metrics={has_metrics}, is_busy={has_busy}")


def test_pr10_05():
    t0 = time.perf_counter()
    from lucy.core.request_queue import RequestQueue, Priority
    q = RequestQueue()
    async def dummy(): pass
    ok = q.enqueue("ws-test", Priority.HIGH, dummy, request_id="test-1")
    return record("PR10-05", "Enqueue accepts requests", "PR10", ok,
                   (time.perf_counter()-t0)*1000, f"enqueued={ok}, queue_size={q._queue.qsize()}")


# ═══════════════════════════════════════════════════════════════
# PR 11: FAST PATH BYPASS
# ═══════════════════════════════════════════════════════════════


def test_pr11_01():
    t0 = time.perf_counter()
    try:
        from lucy.core.fast_path import FastPathResult, evaluate_fast_path
        return record("PR11-01", "Fast path module imports", "PR11", True,
                       (time.perf_counter()-t0)*1000, "FastPathResult, evaluate_fast_path")
    except Exception as e:
        return record("PR11-01", "Fast path module imports", "PR11", False, (time.perf_counter()-t0)*1000, str(e))


def test_pr11_02():
    t0 = time.perf_counter()
    from lucy.core.fast_path import evaluate_fast_path
    greetings = ["hi", "hey", "hello", "Hi Lucy!", "good morning"]
    fast_count = sum(1 for g in greetings if evaluate_fast_path(g).is_fast)
    ok = fast_count == len(greetings)
    return record("PR11-02", "Greetings trigger fast path", "PR11", ok,
                   (time.perf_counter()-t0)*1000, f"{fast_count}/{len(greetings)} greetings detected")


def test_pr11_03():
    t0 = time.perf_counter()
    from lucy.core.fast_path import evaluate_fast_path
    complex_msgs = [
        "Research the top 10 AI agent competitors and their pricing",
        "Check my calendar for today and list all events",
        "Deploy my latest code to AWS Lambda",
        "What was our exact MRR last month?",
    ]
    non_fast = sum(1 for m in complex_msgs if not evaluate_fast_path(m).is_fast)
    ok = non_fast == len(complex_msgs)
    return record("PR11-03", "Complex queries skip fast path", "PR11", ok,
                   (time.perf_counter()-t0)*1000, f"{non_fast}/{len(complex_msgs)} correctly skipped")


def test_pr11_04():
    t0 = time.perf_counter()
    from lucy.core.fast_path import evaluate_fast_path
    result = evaluate_fast_path("hi", thread_depth=3, has_thread_context=True)
    ok = not result.is_fast
    return record("PR11-04", "In-thread messages skip fast path", "PR11", ok,
                   (time.perf_counter()-t0)*1000, f"is_fast={result.is_fast}, reason={result.reason}")


def test_pr11_05():
    t0 = time.perf_counter()
    from lucy.core.fast_path import evaluate_fast_path
    result = evaluate_fast_path("are you there?")
    ok = result.is_fast and result.response is not None
    return record("PR11-05", "Status checks get fast response", "PR11", ok,
                   (time.perf_counter()-t0)*1000, f"response='{result.response}'")


def test_pr11_06():
    t0 = time.perf_counter()
    from lucy.core.fast_path import evaluate_fast_path
    result = evaluate_fast_path("help")
    ok = result.is_fast and result.response is not None and "integrations" in result.response.lower()
    return record("PR11-06", "Help request gets capabilities overview", "PR11", ok,
                   (time.perf_counter()-t0)*1000, f"has_capabilities={'integrations' in (result.response or '').lower()}")


def test_pr11_07():
    """Fast path <1ms for greetings."""
    from lucy.core.fast_path import evaluate_fast_path
    t0 = time.perf_counter()
    for _ in range(100):
        evaluate_fast_path("hi")
    elapsed = (time.perf_counter() - t0) * 1000
    avg_ms = elapsed / 100
    ok = avg_ms < 1.0
    return record("PR11-07", f"Fast path latency <1ms (avg {avg_ms:.3f}ms)", "PR11", ok,
                   elapsed, f"100 evaluations in {elapsed:.1f}ms")


# ═══════════════════════════════════════════════════════════════
# PR 12: RATE LIMITING LAYER
# ═══════════════════════════════════════════════════════════════


def test_pr12_01():
    t0 = time.perf_counter()
    try:
        from lucy.core.rate_limiter import RateLimiter, TokenBucket, get_rate_limiter
        return record("PR12-01", "Rate limiter module imports", "PR12", True,
                       (time.perf_counter()-t0)*1000, "RateLimiter, TokenBucket, get_rate_limiter")
    except Exception as e:
        return record("PR12-01", "Rate limiter module imports", "PR12", False, (time.perf_counter()-t0)*1000, str(e))


def test_pr12_02():
    t0 = time.perf_counter()
    async def _test():
        from lucy.core.rate_limiter import get_rate_limiter
        rl = get_rate_limiter()
        ok = await rl.acquire_model("google/gemini-2.5-flash", timeout=1.0)
        return ok
    ok = asyncio.run(_test())
    return record("PR12-02", "Model rate limit acquire works", "PR12", ok,
                   (time.perf_counter()-t0)*1000, f"google/gemini acquired={ok}")


def test_pr12_03():
    t0 = time.perf_counter()
    async def _test():
        from lucy.core.rate_limiter import get_rate_limiter
        rl = get_rate_limiter()
        ok = await rl.acquire_api("google_calendar", timeout=1.0)
        return ok
    ok = asyncio.run(_test())
    return record("PR12-03", "API rate limit acquire works", "PR12", ok,
                   (time.perf_counter()-t0)*1000, f"google_calendar acquired={ok}")


def test_pr12_04():
    t0 = time.perf_counter()
    from lucy.core.rate_limiter import get_rate_limiter
    rl = get_rate_limiter()
    api = rl.classify_api_from_tool("COMPOSIO_MULTI_EXECUTE_TOOL", {"actions": ["GOOGLECALENDAR_CREATE_EVENT"]})
    ok = api == "google_calendar"
    return record("PR12-04", "classify_api_from_tool detects API", "PR12", ok,
                   (time.perf_counter()-t0)*1000, f"detected={api}")


def test_pr12_05():
    t0 = time.perf_counter()
    from lucy.core.rate_limiter import TokenBucket
    bucket = TokenBucket(rate=1.0, capacity=3)
    async def _test():
        r1 = await bucket.acquire(timeout=0.1)
        r2 = await bucket.acquire(timeout=0.1)
        r3 = await bucket.acquire(timeout=0.1)
        r4 = await bucket.acquire(timeout=0.1)
        return r1, r2, r3, r4
    r1, r2, r3, r4 = asyncio.run(_test())
    ok = r1 and r2 and r3 and not r4
    return record("PR12-05", "Token bucket enforces capacity", "PR12", ok,
                   (time.perf_counter()-t0)*1000, f"3 acquired, 4th rejected={not r4}")


# ═══════════════════════════════════════════════════════════════
# PR 13: ASYNC TASK MANAGER
# ═══════════════════════════════════════════════════════════════


def test_pr13_01():
    t0 = time.perf_counter()
    try:
        from lucy.core.task_manager import TaskManager, TaskState, BackgroundTask, get_task_manager, should_run_as_background_task
        return record("PR13-01", "Task manager module imports", "PR13", True,
                       (time.perf_counter()-t0)*1000, "TaskManager, TaskState, BackgroundTask, should_run_as_background_task")
    except Exception as e:
        return record("PR13-01", "Task manager module imports", "PR13", False, (time.perf_counter()-t0)*1000, str(e))


def test_pr13_02():
    t0 = time.perf_counter()
    from lucy.core.task_manager import TaskState
    states = [TaskState.PENDING, TaskState.ACKNOWLEDGED, TaskState.WORKING,
              TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED]
    ok = len(states) == 6
    return record("PR13-02", "6 task states defined", "PR13", ok,
                   (time.perf_counter()-t0)*1000, f"states={[s.value for s in states]}")


def test_pr13_03():
    t0 = time.perf_counter()
    from lucy.core.task_manager import should_run_as_background_task
    bg1 = should_run_as_background_task("Research competitor pricing and create a report", "frontier")
    bg2 = should_run_as_background_task("Hi!", "fast")
    bg3 = should_run_as_background_task("Check my calendar", "default")
    ok = bg1 and not bg2 and not bg3
    return record("PR13-03", "Background task classification", "PR13", ok,
                   (time.perf_counter()-t0)*1000, f"research+frontier={bg1}, hi+fast={bg2}, calendar+default={bg3}")


def test_pr13_04():
    t0 = time.perf_counter()
    from lucy.core.task_manager import get_task_manager
    tm = get_task_manager()
    has_metrics = hasattr(tm, "metrics")
    ok = has_metrics and tm.metrics["total_tasks"] == 0
    return record("PR13-04", "Task manager metrics", "PR13", ok,
                   (time.perf_counter()-t0)*1000, f"metrics={tm.metrics}")


def test_pr13_05():
    t0 = time.perf_counter()
    async def _test():
        from lucy.core.task_manager import get_task_manager
        tm = get_task_manager()
        async def dummy_handler(): return "done"
        task = await tm.start_task(
            workspace_id="test-ws",
            channel_id="C-test",
            thread_ts="123.456",
            description="Test task",
            handler=dummy_handler,
        )
        await asyncio.sleep(0.2)
        return task.state.value, task.result
    state, result = asyncio.run(_test())
    ok = state == "completed" and result == "done"
    return record("PR13-05", "Background task completes", "PR13", ok,
                   (time.perf_counter()-t0)*1000, f"state={state}, result={result}")


# ═══════════════════════════════════════════════════════════════
# INTEGRATION: Check handlers.py wiring
# ═══════════════════════════════════════════════════════════════


def test_int_01():
    t0 = time.perf_counter()
    source = Path("src/lucy/slack/handlers.py").read_text()
    ok = "fast_path" in source or "evaluate_fast_path" in source
    return record("INT-01", "Handlers use fast path", "INT", ok,
                   (time.perf_counter()-t0)*1000, "fast_path in handlers.py")


def test_int_02():
    t0 = time.perf_counter()
    source = Path("src/lucy/slack/handlers.py").read_text()
    ok = "request_queue" in source or "classify_priority" in source
    return record("INT-02", "Handlers use request queue", "INT", ok,
                   (time.perf_counter()-t0)*1000, "request_queue in handlers.py")


def test_int_03():
    t0 = time.perf_counter()
    source = Path("src/lucy/core/agent.py").read_text()
    ok = "rate_limiter" in source or "acquire_api" in source
    return record("INT-03", "Agent uses rate limiter", "INT", ok,
                   (time.perf_counter()-t0)*1000, "rate_limiter in agent.py")


def test_int_04():
    t0 = time.perf_counter()
    source = Path("src/lucy/slack/handlers.py").read_text()
    ok = "task_manager" in source or "should_run_as_background" in source
    return record("INT-04", "Handlers use task manager", "INT", ok,
                   (time.perf_counter()-t0)*1000, "task_manager in handlers.py")


# ═══════════════════════════════════════════════════════════════
# RUNNER
# ═══════════════════════════════════════════════════════════════


def run_all():
    print("=" * 72)
    print("Round 3 Tests — Viktor PR 10-13 (Applied from Viktor's Patches)")
    print("=" * 72)

    tests = {
        "PR10: Priority Request Queue": [test_pr10_01, test_pr10_02, test_pr10_03, test_pr10_04, test_pr10_05],
        "PR11: Fast Path Bypass": [test_pr11_01, test_pr11_02, test_pr11_03, test_pr11_04, test_pr11_05, test_pr11_06, test_pr11_07],
        "PR12: Rate Limiting": [test_pr12_01, test_pr12_02, test_pr12_03, test_pr12_04, test_pr12_05],
        "PR13: Async Task Manager": [test_pr13_01, test_pr13_02, test_pr13_03, test_pr13_04, test_pr13_05],
        "Integration": [test_int_01, test_int_02, test_int_03, test_int_04],
    }

    total = passed = 0
    for section, fns in tests.items():
        print(f"\n--- {section} ---")
        for fn in fns:
            total += 1
            if fn():
                passed += 1

    print(f"\n{'='*72}")
    print(f"TOTAL: {passed}/{total} PASSED")
    print(f"{'='*72}")

    report_path = Path("docs/tests/round3_test_report.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(RESULTS, indent=2))
    print(f"\nReport saved: {report_path}")
    return passed, total


if __name__ == "__main__":
    p, t = run_all()
    sys.exit(0 if p == t else 1)
