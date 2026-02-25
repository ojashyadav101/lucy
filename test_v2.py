"""Lucy v2 Test Suite — fresh prompts, same themes, different questions."""
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
        reply_text = reply.get("text", "")[:500]
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
    print(f"  ✓ Initial response ({e1}s): {reply1['text'][:300]}")

    time.sleep(2)
    print(f"  → Follow-up: {followup[:80]}")
    t1 = time.time()
    fu_ts = send_thread_reply(msg_ts, followup)
    reply2 = wait_for_reply(msg_ts, fu_ts, timeout=timeout)
    e2 = round(time.time() - t1, 1)
    if reply2:
        print(f"  ✓ Follow-up response ({e2}s): {reply2['text'][:300]}")
    else:
        print(f"  ✗ No follow-up response")
    return {
        "label": label, "initial_s": e1, "followup_s": e2,
        "initial_response": reply1["text"][:500],
        "followup_response": (reply2 or {}).get("text", "")[:500],
        "thread_ts": msg_ts,
        "status": "ok" if reply2 else "timeout_followup",
    }


if __name__ == "__main__":
    results = []

    print("\n" + "="*60)
    print("SECTION A: Greetings & Conversational")
    print("="*60)
    results.append(run_test("greet_morning", "good morning!"))
    results.append(run_test("greet_howdy", "howdy! how's your day going?"))
    results.append(run_test("greet_bare", "hey"))

    print("\n" + "="*60)
    print("SECTION B: Simple Q&A (no tools needed)")
    print("="*60)
    results.append(run_test("day_of_week", "what day of the week is it?"))
    results.append(run_test("math_simple", "what is 156 divided by 12?"))
    results.append(run_test("self_intro", "who are you?"))

    print("\n" + "="*60)
    print("SECTION C: Knowledge Questions (from training data)")
    print("="*60)
    results.append(run_test("knowledge_k8s", "explain Kubernetes to me like I'm a marketer"))
    results.append(run_test(
        "knowledge_compare",
        "compare React, Vue, and Svelte — give me the key differences",
        timeout=180,
    ))

    print("\n" + "="*60)
    print("SECTION D: Thread Context Retention")
    print("="*60)
    results.append(run_thread_test(
        "thread_project",
        "I'm working on a new onboarding flow for our product",
        "what was I just telling you about?",
    ))

    print("\n" + "="*60)
    print("SECTION E: Tool Use")
    print("="*60)
    results.append(run_test("clerk_count", "how many users do we have in Clerk?", timeout=90))
    results.append(run_test("cron_status", "what recurring tasks do I have set up?"))

    print("\n" + "="*60)
    print("SECTION F: Edge Cases")
    print("="*60)
    results.append(run_test("empty_dots", "..."))
    results.append(run_test(
        "urgent_repeat",
        "please help me this is really urgent " * 5,
    ))
    results.append(run_test(
        "research_no_tool",
        "what are the pros and cons of Next.js vs Remix for a new project?",
        timeout=180,
    ))

    print("\n\n" + "="*60)
    print("FULL RESULTS SUMMARY")
    print("="*60)
    for r in results:
        status = "✓" if r.get("status") == "ok" else "✗"
        elapsed = r.get("elapsed_s") or r.get("initial_s", "?")
        resp = (r.get("response") or r.get("initial_response") or "")[:100]
        print(f"  {status} {r['label']}: {elapsed}s — {resp}")

    with open("test_v2_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to test_v2_results.json")
