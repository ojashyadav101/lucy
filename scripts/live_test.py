#!/usr/bin/env python3
"""Live integration test: invoke Lucy's handler directly with real
Slack client so responses appear in #lucy-my-ai. Monitor logs and
analyze every response for issues.
"""
from __future__ import annotations

import asyncio
import os
import ssl
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import certifi
from slack_sdk.web.async_client import AsyncWebClient

from lucy.config import settings

CHANNEL = "C0AEZ241C3V"
BOT_USER_ID = "U0AG8LVAB4M"

TESTS = [
    {
        "id": "T01",
        "label": "Simple Research (should be sync, no background ack)",
        "text": "Research the top 3 AI code assistant tools and their pricing. Compare them briefly.",
        "max_time_s": 60,
        "checks": ["no_background_ack", "no_raw_json", "no_internal_lang", "response_length>200"],
    },
    {
        "id": "T02",
        "label": "Calendar check (tool use)",
        "text": "Check my calendar for this week and summarize what my busiest day is",
        "max_time_s": 60,
        "checks": ["no_raw_json", "no_internal_lang", "response_length>50"],
    },
    {
        "id": "T03",
        "label": "Salesforce connect (no hallucinated URL)",
        "text": "Connect me to Salesforce",
        "max_time_s": 30,
        "checks": ["no_broken_url", "no_raw_json"],
    },
    {
        "id": "T04",
        "label": "Complex multi-tool (calendar + email gaps)",
        "text": (
            "Check my calendar for next week, find any gaps, "
            "and suggest the best time for a 1-hour team meeting. "
            "Also check if I have any relevant emails about team meetings."
        ),
        "max_time_s": 90,
        "checks": ["no_raw_json", "no_internal_lang", "response_length>100"],
    },
]

LOG_FILE = Path(
    os.environ.get(
        "LUCY_LOG_FILE",
        str(
            Path.home()
            / ".cursor/projects/Users-ojashyadav-SEO-Code-lucy/terminals/625846.txt"
        ),
    )
)


@dataclass
class TestResult:
    test_id: str
    label: str
    response_text: str = ""
    response_time_s: float = 0.0
    all_messages: list[str] = field(default_factory=list)
    log_lines: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    passed: bool = False


def get_log_snapshot_line_count() -> int:
    if not LOG_FILE.exists():
        return 0
    with open(LOG_FILE) as f:
        return sum(1 for _ in f)


def get_new_log_lines(start_line: int) -> list[str]:
    if not LOG_FILE.exists():
        return []
    lines = []
    with open(LOG_FILE) as f:
        for i, line in enumerate(f):
            if i >= start_line:
                stripped = line.strip()
                if "[info" in stripped or "[warning" in stripped or "[error" in stripped:
                    lines.append(stripped)
    return lines


def run_checks(result: TestResult, checks: list[str]) -> None:
    text = result.response_text
    all_text = " ".join(result.all_messages)

    for check in checks:
        if check == "no_background_ack":
            if any("background" in m.lower() for m in result.all_messages):
                result.issues.append("Unwanted background-task ack message")

        elif check == "no_raw_json":
            for m in result.all_messages:
                if '{"data"' in m or '"execution_guidance"' in m or '"results"' in m:
                    result.issues.append(f"RAW JSON exposed: {m[:80]}...")
                    break

        elif check == "no_internal_lang":
            bad = [
                "found the right tools", "executed some actions",
                "gathered tool details", "checked your integrations",
                "tool_count", "schemas",
            ]
            for phrase in bad:
                if phrase.lower() in all_text.lower():
                    result.issues.append(f"Internal language: '{phrase}'")

        elif check == "no_broken_url":
            for m in result.all_messages:
                if "https://connect." in m and "link unavailable" not in m:
                    result.issues.append("Broken/truncated URL")

        elif check.startswith("response_length>"):
            min_len = int(check.split(">")[1])
            if len(text) < min_len:
                result.issues.append(
                    f"Response too short: {len(text)} < {min_len} chars"
                )

    if result.response_time_s > 0:
        for test in TESTS:
            if test["id"] == result.test_id:
                if result.response_time_s > test["max_time_s"]:
                    result.issues.append(
                        f"Too slow: {result.response_time_s:.1f}s > {test['max_time_s']}s"
                    )

    result.passed = len(result.issues) == 0


async def run_test(
    slack_client: AsyncWebClient,
    test: dict,
) -> TestResult:
    result = TestResult(test_id=test["id"], label=test["label"])

    print(f"\n{'='*65}")
    print(f"  {test['id']}: {test['label']}")
    print(f"{'='*65}")

    # Post a marker message in the thread so responses are grouped
    marker = await slack_client.chat_postMessage(
        channel=CHANNEL,
        text=f"*[Test {test['id']}]* {test['text']}",
    )
    thread_ts = marker.get("ts", "")

    # Snapshot log position
    log_start = get_log_snapshot_line_count()
    start = time.monotonic()

    # Build a mock `say` that captures messages AND posts them to Slack
    captured: list[str] = []

    async def mock_say(**kwargs):
        text_val = kwargs.get("text", "")
        captured.append(text_val)
        # Also post to Slack so the user can see the response
        post_kwargs = {k: v for k, v in kwargs.items() if k in ("text", "blocks")}
        post_kwargs["channel"] = CHANNEL
        post_kwargs["thread_ts"] = thread_ts
        await slack_client.chat_postMessage(**post_kwargs)

    # Build a mock context
    mock_context = {
        "workspace_id": "1d18c417-b53c-4ab1-80da-4959a622da17",
        "team_id": "T043VTH8V4N",
        "bot_user_id": BOT_USER_ID,
    }

    class DictContext(dict):
        pass

    ctx = DictContext(mock_context)

    print(f"  Sending: {test['text'][:80]}...")

    # Import and call the handler directly
    from lucy.slack.handlers import _handle_message

    try:
        await _handle_message(
            text=test["text"],
            channel_id=CHANNEL,
            thread_ts=thread_ts,
            event_ts=thread_ts,
            say=mock_say,
            client=slack_client,
            context=ctx,
        )
    except Exception as e:
        result.issues.append(f"Handler exception: {e}")
        print(f"  EXCEPTION: {e}")

    elapsed = time.monotonic() - start
    result.response_time_s = elapsed
    result.all_messages = captured
    result.response_text = max(captured, key=len) if captured else ""

    # Collect log lines
    result.log_lines = get_new_log_lines(log_start)

    # Run checks
    run_checks(result, test["checks"])

    # Print
    status = "PASS" if result.passed else "FAIL"
    print(f"\n  Status: {status}")
    print(f"  Time: {elapsed:.1f}s")
    print(f"  Messages posted: {len(captured)}")
    if result.response_text:
        preview = result.response_text[:300].replace("\n", " ")
        print(f"  Response: {preview}")
    if result.issues:
        print(f"  ISSUES:")
        for iss in result.issues:
            print(f"    - {iss}")

    # Show key log events
    key_logs = [
        l for l in result.log_lines
        if any(kw in l for kw in [
            "model_routed", "agent_run_complete", "fast_path",
            "background_task", "tool_turn", "chat_completion_success",
            "empty_response", "error", "warning",
        ])
    ]
    if key_logs:
        print(f"  Log events:")
        for l in key_logs[-10:]:
            print(f"    {l[:160]}")

    return result


async def main() -> None:
    print("=" * 65)
    print("  LUCY LIVE INTEGRATION TEST")
    print(f"  Channel: #lucy-my-ai ({CHANNEL})")
    print(f"  Tests: {len(TESTS)}")
    print("=" * 65)

    bot_token = os.environ.get(
        "SLACK_BOT_TOKEN",
        "xoxb-4131935301158-10552709351157-XWa5YmNyXCb2L8XcpR0m4uuY",
    )
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    slack_client = AsyncWebClient(
        token=bot_token,
        ssl=ssl_ctx,
    )

    # Verify bot token
    auth = await slack_client.auth_test()
    if not auth.get("ok"):
        print(f"ERROR: Bot token invalid: {auth.get('error')}")
        return
    print(f"  Bot: {auth.get('user')} ({auth.get('user_id')})")
    print(f"  Posting responses to real Slack channel")

    results: list[TestResult] = []

    for test in TESTS:
        result = await run_test(slack_client, test)
        results.append(result)
        await asyncio.sleep(2)

    # Summary
    print(f"\n{'='*65}")
    print("  FINAL SUMMARY")
    print(f"{'='*65}")
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    print(f"  Passed: {passed}/{total}")
    print()
    for r in results:
        flag = "PASS" if r.passed else "FAIL"
        extra = ""
        if r.issues:
            extra = f" — {'; '.join(r.issues)}"
        print(f"  {r.test_id} [{flag}] {r.label} ({r.response_time_s:.1f}s){extra}")


if __name__ == "__main__":
    asyncio.run(main())
