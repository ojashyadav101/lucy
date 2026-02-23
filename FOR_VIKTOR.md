# Lucy → Viktor: Round 3 — Concurrency Fixes, Full Test Suite, Architecture Questions

**Date:** February 23, 2026, 3:45 PM IST / 10:15 AM UTC  
**Branch:** `lucy-openrouter-v2`  
**Context:** We've applied your Round 2 patches (Three-Tier Memory, Contextual Emojis, Rich Formatting, UX Micro-Interactions), fixed 4 critical concurrency bugs we discovered, and run a comprehensive test suite covering memory, reactions, formatting, concurrency isolation, and load testing. **All 10 tests pass.** This document contains our results and specific questions about multi-tenant architecture.

---

## What Changed Since Last Review

### Your Round 2 Patches (all applied)
1. **Three-Tier Memory** (`workspace/memory.py`) — Session memory bridge between thread-ephemeral and permanent knowledge. Regex-based extraction → classify → persist. Cron-based consolidation every 6h.
2. **Contextual Emoji Reactions** (`slack/reactions.py`) — Lightweight regex classifier: "thanks" → 🫡 react-only, "research X" → 🔍 react+reply. Working emoji changes per intent.
3. **Rich Formatting Pipeline** (`slack/rich_output.py`) — Anchor-text links (GitHub PR URLs → "GitHub PR #42"), section emojis, response splitting at natural breaks.
4. **UX Micro-Interactions** — Edit-in-place progress updates, turn-aware language ("Working on it" → "Still on it — this is a deep one"), specific error messages (timeout/rate-limit/connection).

### Critical Concurrency Bugs We Found and Fixed
5. **`_progress_ts` race** — Was stored on singleton `LucyAgent` instance. Two concurrent requests would overwrite each other's progress message timestamp. **Fixed:** moved to local variable in `_agent_loop`.
6. **Memory read-modify-write race** — `add_session_fact()` reads JSON, appends, writes. Two concurrent calls could lose data (last write wins). **Fixed:** per-workspace `asyncio.Lock` in `memory.py`.
7. **Event dedup race** — `_processed_events` dict read/written without locking. Under high concurrency, duplicate events could slip through. **Fixed:** `asyncio.Lock` wrapping the check-and-set.
8. **No concurrency limit** — Unlimited `agent.run()` calls could exhaust the LLM connection pool (max 20). **Fixed:** `asyncio.Semaphore(10)` gates concurrent agent runs.

### Memory Regex Improvements
9. **`_REMEMBER_SIGNALS`** — Added `our company/team/product uses/is/has/runs` to catch phrases like "Our company uses React and Python".
10. **`_COMPANY_SIGNALS`** — Added `we switched to/moved to/migrated to` to catch tech stack changes.

---

## Round 2 Test Results

**Test suite:** `round2_tests.py` — 10 tests covering memory, classification, reactions, formatting, UX, tone, concurrency isolation, and load.

**Result: 10/10 PASSED**

| Test | Name | Type | Status | Details |
|------|------|------|--------|---------|
| R1 | Memory persistence (store + recall) | Live | PASS | Stored $800K target, recalled from session memory in new thread. 21.8s store, 18.3s recall. |
| R2 | Memory classification (12 cases) | Offline | PASS | 12/12 — company/team/session routing correct for all test messages |
| R3 | Contextual emoji reactions (17 cases) | Offline | PASS | 17/17 — react-only, react+reply, working emoji all correct |
| R3L | Live emoji reactions (3 cases) | Live | PASS | saluting_face for "thanks!", check_mark for "got it", thumbsup for "ship it". All react-only (no text reply). |
| R4 | Rich formatting (8 cases) | Offline | PASS | GitHub PR links, Google Docs links, Linear issue links, section emojis, response splitting |
| R5 | UX micro-interactions (4 cases) | Offline | PASS | Turn-aware progress language verified for turns 1, 4, 7+ |
| R6 | Tone pipeline (6 cases) | Offline | PASS | "great question", "happy to help", "worth noting", "delve into" all stripped |
| R7 | **Concurrent memory isolation** | Live | PASS | Two threads stored different facts simultaneously — both persisted. No data loss. |
| R8 | **Thread context isolation** | Live | PASS | Three concurrent threads with different topics. No cross-contamination detected. |
| R9 | Composio session isolation | Offline | PASS | Workspace-keyed sessions, double-checked locking, LRU cache, stale recovery all verified |
| R10 | **Load test (5 concurrent)** | Live | PASS | 5/5 replies. P50: 25.7s, P95: 57.1s, Avg: 31.2s |

### Key Observations

**Memory works end-to-end.** R1 proved the full loop: user says "Remember our Q1 target is $800K" → Lucy acknowledges → fact persisted to `session_memory.json` → new thread asks "What's our revenue target?" → Lucy recalls from injected session context.

**Concurrency is safe after our fixes.** R7 sent two simultaneous "remember this" messages — both facts persisted correctly (no last-write-wins). The per-workspace `asyncio.Lock` is working. R8 confirmed no cross-thread contamination.

**Load performance is acceptable but not great.** 5 concurrent messages all got replies, but a simple "Hi Lucy!" took 25.7s. The bottleneck is LLM latency — simple greetings go through the full agent loop instead of a fast path. P95 is 57.1s for the most complex query (calendar lookup with tool calls).

---

## Concurrency Architecture Questions — Please Create PRs

Our current architecture is a single asyncio event loop with a singleton `LucyAgent` and shared `httpx` connection pool (max 20). We've added guards (semaphore, per-workspace locks, dedup locks) but the fundamental model is cooperative multitasking on one loop.

### 1. Multi-Tenant Request Handling (CRITICAL)

**Our architecture:**
```
SlackEvent → AsyncApp (single loop) → classify_reaction() → _handle_message()
→ asyncio.Semaphore(10) → agent.run() → LLM pool (max 20 connections)
```

**Questions:**
- How does Viktor handle 10+ simultaneous users? Do you use a request queue? Worker pool? Multiple event loops?
- Is there a priority system? (e.g., simple greetings fast-tracked ahead of multi-step research tasks?)
- Do you run agent loops in separate threads/processes, or all in one event loop like us?
- What happens when the LLM provider rate-limits you under load? Do you have per-model queues?

### 2. Tool Call Contention

When two users simultaneously need the same external API (e.g., Google Calendar):
- How do you handle this? Shared connection pool? Per-user API sessions?
- Do you rate-limit per external API to avoid hitting their rate limits?
- Does Composio handle this for you, or do you manage it at the application layer?

Our Composio client uses per-workspace session keying with LRU cache, but there's no rate limiting on the external API calls themselves.

### 3. Memory Isolation Under Concurrency

We added per-workspace `asyncio.Lock` for memory writes. This prevents the read-modify-write race condition. But:
- Is this sufficient? Or should we use file-level locking (fcntl)?
- What if two different workspaces write to different files simultaneously — is `asyncio.Lock` enough since writes are already to different paths?
- Do you use a database instead of filesystem for memory? If so, what database?

### 4. Request Prioritization / Fast Path

Our R10 load test showed a simple "Hi Lucy!" takes 25.7s because it goes through the full agent loop (workspace setup → tool fetch → LLM call). This is unacceptable for greetings.

**Questions:**
- Do you have a "fast path" that bypasses the full agent loop for simple queries?
- If so, what's the classification logic? (We already have `classify_reaction` for react-only messages — should we extend this to a "no-agent" classification for simple lookups?)
- How do you decide between "answer directly from cache/context" vs. "need to run the full loop"?

### 5. Long-Session Architecture (Follow-up)

Your Round 2 doc mentioned long-session management but we still need the implementation:
- How does a 30-minute background research task co-exist with immediate responses?
- Is the long task a separate `asyncio.Task` that posts updates via Slack? Or a separate process?
- What's the state machine? (pending → acknowledged → working → progress_update → complete?)

### 6. Backpressure Signaling

When overloaded:
- Does Viktor tell Slack to slow down? (Socket Mode doesn't support this)
- Do you queue internally and if so, what's the queue limit before dropping messages?
- Do you send a "busy" reaction/message to the user?

### 7. Updated Parity Assessment

After these changes, please score us again. Where does Lucy stand now? What's the delta to 95% parity?

**Please submit PRs for the concurrency architecture specifically.** Even skeleton implementations with the right patterns would help enormously.

---

## Full Test Reports

- **Round 2 test report:** `docs/tests/round2_test_report.md`
- **Round 1 test report:** `docs/tests/comprehensive_test_report.md`
- **Test suites:** `round2_tests.py`, `comprehensive_tests.py`

## Files Changed Since Your Last Review

| File | Change |
|------|--------|
| `src/lucy/workspace/memory.py` | NEW — Three-tier memory + per-workspace locks |
| `src/lucy/slack/reactions.py` | NEW — Contextual emoji reaction classifier |
| `src/lucy/slack/rich_output.py` | NEW — Rich formatting pipeline (links, emojis, splitting) |
| `src/lucy/slack/typing_indicator.py` | NEW — Typing indicator placeholder |
| `src/lucy/core/agent.py` | Bug fix: `_progress_ts` → local var; memory persistence post-response |
| `src/lucy/core/prompt.py` | Session memory injection into system prompt |
| `src/lucy/slack/handlers.py` | Contextual reactions, rich formatting, dedup lock, agent semaphore |
| `src/lucy/crons/scheduler.py` | Memory consolidation cron (every 6h) |
| `assets/SYSTEM_PROMPT.md` | Memory discipline + Slack formatting sections |
| `round2_tests.py` | NEW — 10-test Round 2 suite |

---

## What We Need From You

1. **PRs for concurrency architecture** — request queuing, priority system, fast path for greetings
2. **Long-session skeleton** — async task manager that allows background work + side conversations
3. **Fast path implementation** — skip full agent loop for simple greetings and acknowledgments
4. **Rate limiting layer** — per-model and per-external-API rate limiting
5. **Updated parity score** — where are we now after 10/10 tests pass and 4 concurrency fixes?
6. **Top 3 remaining gaps** to reach 95%+ parity

We're ready to implement immediately.
