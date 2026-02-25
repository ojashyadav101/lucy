"""Lucy 10-Phase Test Harness — sends messages, monitors responses, logs results."""
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


def wait_for_reply(thread_ts: str, after_ts: str, timeout: int = 120) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(3)
        with httpx.Client(verify=SSL, timeout=15) as c:
            r = c.get(
                "https://slack.com/api/conversations.replies",
                headers={"Authorization": f"Bearer {BOT_TOKEN}"},
                params={"channel": CHANNEL, "ts": thread_ts, "limit": 20},
            ).json()
        for msg in r.get("messages", []):
            if msg.get("user") == BOT_USER_ID and msg["ts"] > after_ts:
                return msg
    return None


def run_test(label: str, text: str, timeout: int = 120) -> dict:
    print(f"\n{'='*60}")
    print(f"TEST: {label}")
    print(f"  → {text[:80]}")
    t0 = time.time()
    msg_ts = send_mention(text)
    reply = wait_for_reply(msg_ts, msg_ts, timeout=timeout)
    elapsed = round(time.time() - t0, 1)
    if reply:
        reply_text = reply.get("text", "")[:300]
        print(f"  ✓ Response ({elapsed}s): {reply_text}")
        return {
            "label": label, "elapsed_s": elapsed,
            "response": reply_text, "thread_ts": msg_ts,
            "status": "ok",
        }
    else:
        print(f"  ✗ No response after {elapsed}s")
        return {
            "label": label, "elapsed_s": elapsed,
            "response": None, "thread_ts": msg_ts,
            "status": "timeout",
        }


def run_thread_test(
    label: str, initial: str, followup: str, timeout: int = 120
) -> dict:
    print(f"\n{'='*60}")
    print(f"THREAD TEST: {label}")
    print(f"  → Initial: {initial[:80]}")
    t0 = time.time()
    msg_ts = send_mention(initial)
    reply1 = wait_for_reply(msg_ts, msg_ts, timeout=timeout)
    if not reply1:
        print(f"  ✗ No initial response")
        return {"label": label, "status": "timeout_initial"}

    e1 = round(time.time() - t0, 1)
    print(f"  ✓ Initial response ({e1}s): {reply1['text'][:200]}")

    time.sleep(2)
    print(f"  → Follow-up: {followup[:80]}")
    t1 = time.time()
    fu_ts = send_thread_reply(msg_ts, followup)
    reply2 = wait_for_reply(msg_ts, fu_ts, timeout=timeout)
    e2 = round(time.time() - t1, 1)
    if reply2:
        print(f"  ✓ Follow-up response ({e2}s): {reply2['text'][:200]}")
    else:
        print(f"  ✗ No follow-up response")
    return {
        "label": label, "initial_s": e1, "followup_s": e2,
        "initial_response": reply1["text"][:300],
        "followup_response": (reply2 or {}).get("text", "")[:300],
        "thread_ts": msg_ts,
        "status": "ok" if reply2 else "timeout_followup",
    }


if __name__ == "__main__":
    import sys
    phase = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    results = []

    if phase == 1:
        print("\n" + "="*60)
        print("PHASE 1: Basic Greetings & Simple Q&A")
        print("="*60)
        results.append(run_test("greeting_hi", "hi"))
        results.append(run_test("greeting_hello", "hello! how are you doing today?"))
        results.append(run_test("simple_qa", "what day is it today?"))
        results.append(run_test("simple_math", "what is 47 * 23?"))
        results.append(run_test(
            "about_self",
            "tell me about yourself in 2 sentences"
        ))

    elif phase == 2:
        print("\n" + "="*60)
        print("PHASE 2: Model Routing Verification")
        print("="*60)
        results.append(run_test("route_code",
            "write me a Python function that checks if a number is prime"))
        results.append(run_test("route_research",
            "do a deep analysis of the current AI agent landscape and compare the top 5 frameworks"))
        results.append(run_test("route_lookup",
            "what is Docker?", timeout=60))
        results.append(run_test("route_tool",
            "check what integrations I have connected"))
        results.append(run_test("route_document",
            "create a report about our team's productivity this month"))

    elif phase == 3:
        print("\n" + "="*60)
        print("PHASE 3: Thread Follow-ups & Context Retention")
        print("="*60)
        results.append(run_thread_test(
            "context_name",
            "my name is TestBot and I work on the marketing team",
            "what's my name and what team am I on?",
        ))
        results.append(run_thread_test(
            "context_task",
            "I need help planning a product launch for next month",
            "what were we talking about? can you continue?",
        ))

    elif phase == 4:
        print("\n" + "="*60)
        print("PHASE 4: Tool Use — Clerk/Composio")
        print("="*60)
        results.append(run_test("clerk_users",
            "list the users in our Clerk system", timeout=90))
        results.append(run_test("composio_connections",
            "what tools and integrations do I have access to?"))
        results.append(run_test("composio_search",
            "search for Gmail tools available through Composio"))

    elif phase == 5:
        print("\n" + "="*60)
        print("PHASE 5: Research Tasks")
        print("="*60)
        results.append(run_test("research_competitors",
            "research and compare Cursor vs Windsurf vs Zed for AI-assisted coding. "
            "Give me a brief comparison with pros and cons.",
            timeout=180))

    elif phase == 6:
        print("\n" + "="*60)
        print("PHASE 6: Cron Jobs & Proactivity")
        print("="*60)
        results.append(run_test("cron_list",
            "what cron jobs do I have set up?"))
        results.append(run_test("cron_create",
            "set up a cron job that reminds me to check my emails every day at 9am"))

    elif phase == 7:
        print("\n" + "="*60)
        print("PHASE 7: Multi-step Workflows")
        print("="*60)
        results.append(run_test("multi_step",
            "I want you to: 1) check what Clerk users exist, "
            "2) tell me how many there are, and "
            "3) summarize their roles",
            timeout=180))

    elif phase == 8:
        print("\n" + "="*60)
        print("PHASE 8: Edge Cases & Error Recovery")
        print("="*60)
        results.append(run_test("empty_ish", "..."))
        results.append(run_test("very_long",
            "I need you to help me with something very important. " * 20))
        results.append(run_test("ambiguous",
            "can you do the thing we discussed yesterday?"))

    # Print summary
    print("\n\n" + "="*60)
    print(f"PHASE {phase} RESULTS SUMMARY")
    print("="*60)
    for r in results:
        status = "✓" if r.get("status") == "ok" else "✗"
        elapsed = r.get("elapsed_s") or r.get("initial_s", "?")
        resp = (r.get("response") or r.get("initial_response") or "")[:80]
        print(f"  {status} {r['label']}: {elapsed}s — {resp}")

    with open(f"test_phase{phase}_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to test_phase{phase}_results.json")
