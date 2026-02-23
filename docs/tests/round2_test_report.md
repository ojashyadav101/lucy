# Lucy Round 2 Test Report

**Generated:** 2026-02-23 10:15:55 UTC / 2026-02-23 15:45:55 IST
**Tests run:** 11
**Passed:** 11/11

---

| Test | Name | Status | Notes |
|------|------|--------|-------|
| R2 | Memory classification | PASS | - |
| R3 | Emoji reactions (offline) | PASS | - |
| R4 | Rich formatting (offline) | PASS | - |
| R5 | UX micro-interactions (offline) | PASS | - |
| R6 | Tone pipeline (offline) | PASS | - |
| R9 | Composio session isolation (offline) | PASS | - |
| R1 | Memory persistence | PASS | 21.9 |
| R3L | Emoji reactions (live) | PASS | - |
| R7 | Concurrent memory isolation | PASS | - |
| R8 | Thread context isolation | PASS | - |
| R10 | Load test | PASS | 36.3 |

---

## R2: Memory classification
**Status:** PASS | **Time:** 2026-02-23 10:12:13 UTC
- cases: [{'msg': 'Remember this: our MRR target is $500K', 'persist_ok': True, 'target_ok': True, 'ok': True}, {'msg': 'Our company uses React and Python', 'persist_ok': True, 'target_ok': True, 'ok': True}, {'msg': "I'm the head of marketing", 'persist_ok': True, 'target_ok': True, 'ok': True}, {'msg': 'We switched to Vercel last month', 'persist_ok': True, 'target_ok': True, 'ok': True}, {'msg': 'My timezone is IST', 'persist_ok': True, 'target_ok': True, 'ok': True}, {'msg': 'Note that our budget is $50K for Q1', 'persist_ok': True, 'target_ok': True, 'ok': True}, {'msg': 'Going forward, always CC jake on emails', 'persist_ok': True, 'target_ok': True, 'ok': True}, {'msg': 'Our revenue is $2M ARR', 'persist_ok': True, 'target_ok': True, 'ok': True}, {'msg': 'What time is it?', 'persist_ok': True, 'target_ok': True, 'ok': True}, {'msg': 'Check my calendar', 'persist_ok': True, 'target_ok': True, 'ok': True}, {'msg': 'Send an email to Jake', 'persist_ok': True, 'target_ok': True, 'ok': True}, {'msg': 'Hi Lucy', 'persist_ok': True, 'target_ok': True, 'ok': True}]
- pass_count: 12
- total: 12

## R3: Emoji reactions (offline)
**Status:** PASS | **Time:** 2026-02-23 10:12:14 UTC
- pass_count: 17
- total: 17

## R4: Rich formatting (offline)
**Status:** PASS | **Time:** 2026-02-23 10:12:14 UTC
- pass_count: 8
- total: 8

## R5: UX micro-interactions (offline)
**Status:** PASS | **Time:** 2026-02-23 10:12:14 UTC
- results: [{'turn': 1, 'ok': True}, {'turn': 4, 'ok': True}, {'turn': 7, 'ok': True}, {'check': 'tool_labels', 'ok': True}]

## R6: Tone pipeline (offline)
**Status:** PASS | **Time:** 2026-02-23 10:12:14 UTC
- pass_count: 6
- total: 6

## R9: Composio session isolation (offline)
**Status:** PASS | **Time:** 2026-02-23 10:12:14 UTC
- sessions_keyed_by_workspace: True
- has_cache_lock: True
- has_session_lock: True
- double_checked_locking: True
- lru_eviction: True
- stale_recovery: True

## R1: Memory persistence
**Status:** PASS | **Time:** 2026-02-23 10:12:59 UTC
- store_elapsed_s: 21.9
- recall_elapsed_s: 18.4
- stored: True
- mem_on_disk: True
- recalled: True
- store_response: Got it — I've noted your Q1 target of $800K with current revenue at $620K. That's $180K to go in Q1. I'll keep this in mind for future reference.
- recall_response: From what you told me earlier:

* *Q1 revenue target* — $800K
* *Current revenue* — $620K

That's about 77.5% of target with time remaining in Q1. Want me to dig into the details — maybe break down by...

## R3L: Emoji reactions (live)
**Status:** PASS | **Time:** 2026-02-23 10:13:20 UTC
- results: [{'msg': 'thanks!', 'has_emoji': True, 'no_reply': True, 'ok': True}, {'msg': 'got it', 'has_emoji': True, 'no_reply': True, 'ok': True}, {'msg': 'ship it', 'has_emoji': True, 'no_reply': True, 'ok': True}]

## R7: Concurrent memory isolation
**Status:** PASS | **Time:** 2026-02-23 10:13:41 UTC
- both_replied: True
- both_persisted: True

## R8: Thread context isolation
**Status:** PASS | **Time:** 2026-02-23 10:14:38 UTC
- threads: [{'thread': 1, 'msg': 'What time is it for each team member rig', 'elapsed': 18.8, 'got': True, 'reply': "Here's what time it is for each team member right now:\n\n• *Ojash* — 3:43 PM (Asia/Kolkata)\n• *Somya Sharma* — 3:43 PM (Asia/Kolkata)\n• *Pawan Singh* —"}, {'thread': 2, 'msg': 'What integrations do I have connected?', 'elapsed': 18.7, 'got': True, 'reply': 'I have the following integrations connected:\n\n• *Gmail* — Active (<mailto:hello@ojash.com|hello@ojash.com>)\n\nI can also connect to Google Calendar, Go'}, {'thread': 3, 'msg': 'What events do I have on my calendar tod', 'elapsed': 57.0, 'got': True, 'reply': "Here's what's on your calendar for today (Sunday, February 23rd):\n\n* *Project Alpha Weekly Sync* — 9:00 AM - 10:00 AM IST (you're in this now)\n - <htt"}]
- contaminated: False

## R10: Load test
**Status:** PASS | **Time:** 2026-02-23 10:15:55 UTC
- details: [{'msg': 'Hi Lucy!', 'type': 'greeting', 'elapsed_s': 18.1, 'got_reply': True, 'reply_len': 32}, {'msg': 'What time is it?', 'type': 'lookup', 'elapsed_s': 14.7, 'got_reply': True, 'reply_len': 18}, {'msg': 'What integrations are connected?', 'type': 'tool_use', 'elapsed_s': 14.7, 'got_reply': True, 'reply_len': 213}, {'msg': 'Check my calendar for today and list all', 'type': 'multi_step', 'elapsed_s': 77.6, 'got_reply': True, 'reply_len': 300}, {'msg': 'Give me a detailed breakdown of everythi', 'type': 'complex', 'elapsed_s': 56.6, 'got_reply': True, 'reply_len': 304}]
- avg_s: 36.3
- p50_s: 18.1
- p95_s: 77.6
- response_count: 5
