"""Direct end-to-end test for Lucy's task pipeline.

Bypasses Slack event dispatch and directly exercises the full pipeline:
  1. Post a user-visible question to the channel
  2. Create a task in the DB
  3. Execute via OpenClaw
  4. Post result to the correct Slack thread
  5. Verify response landed in the right thread

This tests the ENTIRE backend (DB, classifier, router, OpenClaw, Slack posting).
"""
import asyncio
import os
import sys
import time
import uuid
import json

import requests

os.environ.setdefault("LUCY_DATABASE_URL", "postgresql+asyncpg://lucy:lucy@167.86.82.46:5432/lucy")
os.environ.setdefault("LUCY_QDRANT_URL", "http://167.86.82.46:6333")
os.environ.setdefault("OPENROUTER_API_KEY", "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")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

BOT_TOKEN = "xoxb-4131935301158-10552709351157-XWa5YmNyXCb2L8XcpR0m4uuY"
CHANNEL = "C0AEZ241C3V"
LUCY_BOT_USER_ID = "U0AG8LVAB4M"
WORKSPACE_ID = uuid.UUID("5b7e536d-242b-4f74-b116-7c7af1a729d3")
USER_ID = uuid.UUID("b7617381-fcd0-424a-b34e-5c624e03d12d")
HEADERS = {"Authorization": f"Bearer {BOT_TOKEN}", "Content-Type": "application/json"}

QUESTIONS = [
    "What is the capital of Japan and what is it known for?",
    "Write me a 4-line poem about the ocean.",
    "Explain what an API is in simple terms.",
    "What are three benefits of remote work?",
    "If I have 150 apples and give away 37, how many do I have left?",
]


def slack_post(text: str) -> str | None:
    """Post a message to the channel as a question label. Returns ts."""
    resp = requests.post(
        "https://slack.com/api/chat.postMessage",
        json={"channel": CHANNEL, "text": text},
        headers=HEADERS,
    )
    d = resp.json()
    return d["ts"] if d.get("ok") else None


def slack_get_replies(thread_ts: str) -> list[dict]:
    """Get all replies in a thread."""
    resp = requests.get(
        "https://slack.com/api/conversations.replies",
        params={"channel": CHANNEL, "ts": thread_ts, "limit": 50},
        headers=HEADERS,
    )
    d = resp.json()
    return d.get("messages", [])


def extract_text(msg: dict) -> str:
    """Extract readable text from a Slack message."""
    text = msg.get("text", "")
    if not text and msg.get("blocks"):
        parts = []
        for block in msg["blocks"]:
            if block.get("type") == "section" and block.get("text"):
                parts.append(block["text"].get("text", ""))
        text = " | ".join(parts)
    return text


async def run_single_test(index: int, question: str) -> dict:
    """Run a single question through the full pipeline."""
    from lucy.db.session import AsyncSessionLocal
    from lucy.db.models import Task, TaskPriority, TaskStatus
    from lucy.core.agent import execute_task

    print(f"\n{'='*60}")
    print(f"[Q{index}] {question}")
    print(f"{'='*60}")

    # Step 1: Post the question to Slack as a visible message
    parent_ts = slack_post(f"*Test Q{index}:* {question}")
    if not parent_ts:
        return {"index": index, "status": "FAIL", "reason": "Could not post to Slack"}
    print(f"  [1/4] Posted to Slack. thread_ts={parent_ts}")

    # Step 2: Create task in DB
    async with AsyncSessionLocal() as db:
        event_ts = f"test-{uuid.uuid4().hex[:12]}"
        task = Task(
            workspace_id=WORKSPACE_ID,
            requester_id=USER_ID,
            intent="chat",
            priority=TaskPriority.NORMAL,
            status=TaskStatus.CREATED,
            config={
                "source": "integration_test",
                "channel_id": CHANNEL,
                "thread_ts": parent_ts,
                "original_text": question,
                "event_ts": event_ts,
            },
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        task_id = task.id

    print(f"  [2/4] Task created: {task_id}")

    # Step 3: Execute task via OpenClaw
    t0 = time.time()
    try:
        final_status = await execute_task(task_id)
        elapsed = time.time() - t0
        print(f"  [3/4] Task executed in {elapsed:.1f}s. Status: {final_status}")
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  [3/4] EXECUTION FAILED after {elapsed:.1f}s: {e}")
        return {"index": index, "status": "FAIL", "reason": str(e), "elapsed": elapsed}

    # Step 4: Get result from DB and post to correct thread
    async with AsyncSessionLocal() as db:
        from sqlalchemy import select
        result = await db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one()

    response_text = ""
    if task.result_data and task.result_data.get("full_response"):
        response_text = task.result_data["full_response"]
        resp = requests.post(
            "https://slack.com/api/chat.postMessage",
            json={
                "channel": CHANNEL,
                "text": response_text,
                "thread_ts": parent_ts,
            },
            headers=HEADERS,
        )
        if resp.json().get("ok"):
            print(f"  [4/4] Response posted to thread. Length: {len(response_text)} chars")
        else:
            print(f"  [4/4] FAILED to post response: {resp.json().get('error')}")
    else:
        print(f"  [4/4] No response generated. Status reason: {task.status_reason}")

    # Verify: check thread replies
    await asyncio.sleep(1)
    replies = slack_get_replies(parent_ts)
    non_parent = [r for r in replies if r["ts"] != parent_ts]

    print(f"\n  --- Thread Audit for Q{index} ---")
    print(f"  Parent TS: {parent_ts}")
    print(f"  Replies in thread: {len(non_parent)}")
    for j, r in enumerate(non_parent):
        txt = extract_text(r)
        who = "Lucy" if r.get("bot_id") or r.get("user") == LUCY_BOT_USER_ID else "Other"
        print(f"    [{j+1}] ({who}) {txt[:100]}...")

    return {
        "index": index,
        "question": question,
        "thread_ts": parent_ts,
        "status": "PASS" if response_text else "FAIL",
        "reply_count": len(non_parent),
        "response_length": len(response_text),
        "elapsed_seconds": elapsed,
        "response_preview": response_text[:200] if response_text else None,
    }


async def main():
    print("=" * 60)
    print("LUCY END-TO-END INTEGRATION TEST")
    print(f"Channel: #lucy-my-ai ({CHANNEL})")
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    results = []
    for i, q in enumerate(QUESTIONS, 1):
        result = await run_single_test(i, q)
        results.append(result)
        await asyncio.sleep(2)

    # Summary
    print("\n" + "=" * 60)
    print("FINAL SUMMARY")
    print("=" * 60)

    for r in results:
        emoji = "PASS" if r["status"] == "PASS" else "FAIL"
        print(f"  Q{r['index']}: {emoji} | replies={r.get('reply_count', 0)} | "
              f"time={r.get('elapsed_seconds', 0):.1f}s | len={r.get('response_length', 0)}")

    passed = sum(1 for r in results if r["status"] == "PASS")
    print(f"\nResult: {passed}/{len(results)} passed")

    with open("/tmp/lucy_e2e_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
    print("Detailed results saved to /tmp/lucy_e2e_results.json")


if __name__ == "__main__":
    asyncio.run(main())
