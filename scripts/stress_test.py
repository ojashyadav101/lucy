"""Sequential stress test for Lucy — 15 tests from simple to complex.

Calls Lucy's handler pipeline directly, bypassing Slack event delivery.
This gives us accurate timing and full visibility into model selection,
tool calls, and response quality.

Usage:
    PYTHONUNBUFFERED=1 python scripts/stress_test.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from lucy.config import settings

IST = timezone(timedelta(hours=5, minutes=30))
WORKSPACE_ID = os.environ.get("LUCY_WORKSPACE_ID", "T08115EMN0H")
CHANNEL_ID = os.environ.get("SLACK_CHANNEL", "C0AEZ241C3V")


@dataclass
class CapturedResponse:
    """Captures everything say() receives."""
    text: str = ""
    blocks: list[dict] = field(default_factory=list)
    thread_ts: str | None = None
    call_count: int = 0


@dataclass
class TestResult:
    id: str
    name: str
    category: str
    complexity: str
    message: str
    status: str = "PENDING"
    total_time_s: float = 0.0
    phase_times: dict[str, float] = field(default_factory=dict)
    model_selected: str = ""
    intent_classified: str = ""
    tier: str = ""
    tool_calls: list[str] = field(default_factory=list)
    response_text: str = ""
    response_length: int = 0
    has_blocks: bool = False
    is_fast_path: bool = False
    is_background: bool = False
    issues: list[str] = field(default_factory=list)
    error: str = ""
    expected: str = ""
    max_time_s: float = 0.0


TESTS = [
    {
        "id": "T01", "name": "Simple greeting",
        "category": "fast_path", "complexity": "trivial",
        "message": "Hi Lucy!",
        "expect": "fast_path (<1s), no LLM call, greeting response",
        "max_time_s": 2,
    },
    {
        "id": "T02", "name": "Status check",
        "category": "fast_path", "complexity": "trivial",
        "message": "Are you there?",
        "expect": "fast_path (<1s), status response",
        "max_time_s": 2,
    },
    {
        "id": "T03", "name": "Help request",
        "category": "fast_path", "complexity": "trivial",
        "message": "What can you do?",
        "expect": "fast_path (<1s), capabilities overview",
        "max_time_s": 2,
    },
    {
        "id": "T04", "name": "Simple factual question",
        "category": "chat", "complexity": "easy",
        "message": "What day is it today?",
        "expect": "fast model, direct answer, <10s",
        "max_time_s": 20,
    },
    {
        "id": "T05", "name": "Calendar check",
        "category": "tool_use", "complexity": "medium",
        "message": "What's on my calendar for tomorrow?",
        "expect": "tool call to Google Calendar, formatted result, <20s",
        "max_time_s": 45,
    },
    {
        "id": "T06", "name": "Email summary",
        "category": "tool_use", "complexity": "medium",
        "message": "Do I have any unread emails?",
        "expect": "tool call to Gmail, summary of unread, <25s",
        "max_time_s": 45,
    },
    {
        "id": "T07", "name": "Web search",
        "category": "tool_use", "complexity": "medium",
        "message": "What's the latest news about AI agents?",
        "expect": "web search tool call, summarized results, <25s",
        "max_time_s": 45,
    },
    {
        "id": "T08", "name": "Code question",
        "category": "code", "complexity": "medium",
        "message": "Write me a Python function that calculates the Fibonacci sequence up to n terms",
        "expect": "code model, clean Python code with docstring, <15s",
        "max_time_s": 30,
    },
    {
        "id": "T09", "name": "Multi-step tool use",
        "category": "tool_use", "complexity": "hard",
        "message": "Check my calendar for this week and summarize what my busiest day is",
        "expect": "calendar fetch + analysis, formatted summary, <30s",
        "max_time_s": 60,
    },
    {
        "id": "T10", "name": "Formatting quality",
        "category": "chat", "complexity": "medium",
        "message": "Give me a comparison of React vs Vue vs Svelte in a table format with pros and cons",
        "expect": "well-formatted table/blocks, structured comparison, no tool use",
        "max_time_s": 20,
    },
    {
        "id": "T11", "name": "Research task",
        "category": "research", "complexity": "hard",
        "message": "Research the top 3 AI code assistant tools and their pricing. Compare them briefly.",
        "expect": "frontier model, multi-source research, structured output",
        "max_time_s": 120,
    },
    {
        "id": "T12", "name": "Workflow: calendar + prep",
        "category": "workflow", "complexity": "hard",
        "message": "Find what meetings I have tomorrow and draft a quick prep note for each one",
        "expect": "calendar fetch → analysis → structured prep notes",
        "max_time_s": 90,
    },
    {
        "id": "T13", "name": "Error handling",
        "category": "edge_case", "complexity": "medium",
        "message": "Connect me to Salesforce",
        "expect": "graceful handling of unsupported integration, helpful suggestion",
        "max_time_s": 30,
    },
    {
        "id": "T14", "name": "Ambiguous request",
        "category": "chat", "complexity": "medium",
        "message": "Can you help me with that thing we talked about?",
        "expect": "asks for clarification, doesn't hallucinate, <10s",
        "max_time_s": 30,
    },
    {
        "id": "T15", "name": "Complex multi-tool workflow",
        "category": "workflow", "complexity": "very_hard",
        "message": (
            "Check my calendar for next week, find any gaps, and suggest "
            "the best time for a 1-hour team meeting. Also check if I have "
            "any relevant emails about team meetings."
        ),
        "expect": "calendar + email tools, analysis, specific time suggestion",
        "max_time_s": 120,
    },
]


async def run_fast_path_test(test: dict) -> TestResult:
    """Test that goes through fast path only (no agent loop)."""
    from lucy.core.fast_path import evaluate_fast_path

    result = TestResult(
        id=test["id"], name=test["name"], category=test["category"],
        complexity=test["complexity"], message=test["message"],
        expected=test["expect"], max_time_s=test["max_time_s"],
    )

    t0 = time.monotonic()
    fp = evaluate_fast_path(test["message"])
    elapsed = time.monotonic() - t0

    result.total_time_s = round(elapsed, 4)
    result.phase_times["fast_path_eval"] = round(elapsed, 4)
    result.is_fast_path = fp.is_fast

    if fp.is_fast and fp.response:
        result.status = "PASS"
        result.response_text = fp.response
        result.response_length = len(fp.response)
        result.intent_classified = fp.reason
        if elapsed > 0.01:
            result.issues.append(f"Fast path took {elapsed*1000:.1f}ms (should be <10ms)")
    else:
        result.status = "FAIL"
        result.issues.append(f"Fast path did NOT match: reason={fp.reason}")
        result.response_text = fp.response or "(no response)"

    return result


async def run_agent_test(test: dict) -> TestResult:
    """Test that goes through the full agent loop."""
    from lucy.core.agent import AgentContext, get_agent
    from lucy.core.router import classify_and_route
    from lucy.core.task_manager import should_run_as_background_task

    result = TestResult(
        id=test["id"], name=test["name"], category=test["category"],
        complexity=test["complexity"], message=test["message"],
        expected=test["expect"], max_time_s=test["max_time_s"],
    )

    msg = test["message"]

    # Phase 1: Classification
    t_classify = time.monotonic()
    route = classify_and_route(msg)
    classify_time = time.monotonic() - t_classify

    result.model_selected = route.model
    result.intent_classified = route.intent
    result.tier = route.tier
    result.phase_times["classify"] = round(classify_time, 4)

    is_bg = should_run_as_background_task(msg, route.tier)
    result.is_background = is_bg

    # Phase 2: Agent run
    agent = get_agent()
    ctx = AgentContext(
        workspace_id=WORKSPACE_ID,
        channel_id=CHANNEL_ID,
        thread_ts=None,
    )

    t_agent = time.monotonic()
    try:
        response = await asyncio.wait_for(
            agent.run(message=msg, ctx=ctx, slack_client=None),
            timeout=test["max_time_s"],
        )
        agent_time = time.monotonic() - t_agent
        result.phase_times["agent_run"] = round(agent_time, 2)
        result.response_text = response[:2000]
        result.response_length = len(response)
        result.total_time_s = round(time.monotonic() - t_classify, 2)

        from lucy.core.trace import Trace
        trace = Trace.current()
        if trace and trace.tool_calls_made:
            result.tool_calls = list(trace.tool_calls_made)

        if not response.strip():
            result.status = "FAIL"
            result.issues.append("Empty response")
        elif any(p in response.lower() for p in [
            "working on getting that sorted",
            "follow up right here in a moment",
            "i couldn't determine your workspace",
        ]):
            result.status = "FAIL"
            result.issues.append("Got error/fallback message instead of real answer")
        elif result.total_time_s > test["max_time_s"]:
            result.status = "FAIL"
            result.issues.append(
                f"Exceeded time limit ({result.total_time_s:.1f}s > {test['max_time_s']}s)"
            )
        else:
            result.status = "PASS"

    except asyncio.TimeoutError:
        result.total_time_s = test["max_time_s"]
        result.status = "FAIL"
        result.issues.append(f"Timed out after {test['max_time_s']}s")
    except Exception as e:
        agent_time = time.monotonic() - t_agent
        result.phase_times["agent_run"] = round(agent_time, 2)
        result.total_time_s = round(time.monotonic() - t_classify, 2)
        result.status = "FAIL"
        result.error = str(e)[:300]
        result.issues.append(f"Exception: {str(e)[:200]}")

    return result


async def run_single_test(test: dict, num: int) -> TestResult:
    """Run one test and print results."""
    tid = test["id"]
    print(f"\n{'='*75}")
    print(f"  TEST {num}/15: [{tid}] {test['name']}")
    print(f"  Category: {test['category']} | Complexity: {test['complexity']}")
    print(f"  Message: \"{test['message'][:80]}\"")
    print(f"  Expected: {test['expect']}")
    print(f"{'='*75}")

    if test["category"] == "fast_path":
        result = await run_fast_path_test(test)
    else:
        result = await run_agent_test(test)

    icon = "PASS" if result.status == "PASS" else "FAIL"
    color = "\033[92m" if result.status == "PASS" else "\033[91m"
    reset = "\033[0m"

    print(f"\n  {color}{icon}{reset} in {result.total_time_s:.2f}s")
    print(f"  Model: {result.model_selected or 'N/A'} | Intent: {result.intent_classified}")
    print(f"  Tier: {result.tier or 'fast_path'} | Background: {result.is_background}")

    if result.tool_calls:
        print(f"  Tool calls ({len(result.tool_calls)}): {', '.join(result.tool_calls[:8])}")

    resp_preview = result.response_text[:200].replace('\n', ' ')
    print(f"  Response ({result.response_length} chars): {resp_preview}")

    if result.issues:
        for iss in result.issues:
            print(f"  WARNING: {iss}")

    if result.phase_times:
        phases = " | ".join(f"{k}: {v}s" for k, v in result.phase_times.items())
        print(f"  Phases: {phases}")

    return result


async def main():
    print("=" * 75)
    print("  LUCY STRESS TEST — 15 Sequential Tests (Direct Handler)")
    print(f"  Workspace: {WORKSPACE_ID}")
    print(f"  Time: {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S IST')}")
    print("=" * 75)

    results: list[TestResult] = []

    for i, test in enumerate(TESTS, 1):
        result = await run_single_test(test, i)
        results.append(result)

        if i < len(TESTS):
            print(f"\n  Waiting 2s before next test...")
            await asyncio.sleep(2)

    # ── Summary ──
    print(f"\n\n{'='*75}")
    print("  STRESS TEST SUMMARY")
    print(f"{'='*75}")

    passed = sum(1 for r in results if r.status == "PASS")
    failed = sum(1 for r in results if r.status == "FAIL")

    print(f"\n  Results: {passed} PASS | {failed} FAIL")

    print(f"\n  {'ID':<6} {'Name':<35} {'Status':<6} {'Time':>7} "
          f"{'Model':<30} {'Intent':<15} {'Tools'}")
    print(f"  {'-'*6} {'-'*35} {'-'*6} {'-'*7} {'-'*30} {'-'*15} {'-'*20}")

    for r in results:
        t = f"{r.total_time_s:.2f}s"
        model = (r.model_selected or "fast_path")[:29]
        tools = ",".join(r.tool_calls[:3]) if r.tool_calls else "—"
        if len(tools) > 20:
            tools = tools[:17] + "..."
        print(f"  {r.id:<6} {r.name:<35} {r.status:<6} {t:>7} "
              f"{model:<30} {r.intent_classified:<15} {tools}")

    # Timing analysis
    pass_times = [r.total_time_s for r in results if r.status == "PASS"]
    if pass_times:
        avg = sum(pass_times) / len(pass_times)
        mx = max(pass_times)
        print(f"\n  Avg response time (passing): {avg:.2f}s")
        print(f"  Max response time (passing): {mx:.2f}s")

    # Model usage
    models_used = {}
    for r in results:
        m = r.model_selected or "fast_path"
        models_used.setdefault(m, []).append(r.id)
    print(f"\n  Model Usage:")
    for m, ids in models_used.items():
        print(f"    {m}: {', '.join(ids)}")

    # Issues
    all_issues = []
    for r in results:
        for iss in r.issues:
            all_issues.append(f"[{r.id}] {iss}")
    if all_issues:
        print(f"\n  All Issues ({len(all_issues)}):")
        for iss in all_issues:
            print(f"    - {iss}")

    # Background task routing
    bg_tests = [r for r in results if r.is_background]
    if bg_tests:
        print(f"\n  Background-routed tests: {', '.join(r.id for r in bg_tests)}")

    # Save JSON report
    report_data = []
    for r in results:
        report_data.append({
            "id": r.id, "name": r.name, "category": r.category,
            "complexity": r.complexity, "message": r.message,
            "status": r.status, "total_time_s": r.total_time_s,
            "phase_times": r.phase_times,
            "model_selected": r.model_selected,
            "intent_classified": r.intent_classified,
            "tier": r.tier, "tool_calls": r.tool_calls,
            "response_text": r.response_text[:500],
            "response_length": r.response_length,
            "is_fast_path": r.is_fast_path,
            "is_background": r.is_background,
            "issues": r.issues, "error": r.error,
            "expected": r.expected, "max_time_s": r.max_time_s,
        })

    report_path = Path("stress_test_results.json")
    report_path.write_text(json.dumps(report_data, indent=2, default=str))
    print(f"\n  Full report saved: {report_path}")

    return results


if __name__ == "__main__":
    asyncio.run(main())
