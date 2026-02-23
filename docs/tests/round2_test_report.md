# Lucy Round 2 Test Report

**Generated:** 2026-02-23 14:09:04 UTC / 2026-02-23 19:39:04 IST
**Tests run:** 5
**Passed:** 5/5

---

| Test | Name | Status | Notes |
|------|------|--------|-------|
| R2 | Memory classification | PASS | - |
| R4 | Rich formatting (offline) | PASS | - |
| R5 | UX micro-interactions (offline) | PASS | - |
| R6 | Tone pipeline (offline) | PASS | - |
| R9 | Composio session isolation (offline) | PASS | - |

---

## R2: Memory classification
**Status:** PASS | **Time:** 2026-02-23 14:09:04 UTC
- cases: [{'msg': 'Remember this: our MRR target is $500K', 'persist_ok': True, 'target_ok': True, 'ok': True}, {'msg': 'Our company uses React and Python', 'persist_ok': True, 'target_ok': True, 'ok': True}, {'msg': "I'm the head of marketing", 'persist_ok': True, 'target_ok': True, 'ok': True}, {'msg': 'We switched to Vercel last month', 'persist_ok': True, 'target_ok': True, 'ok': True}, {'msg': 'My timezone is IST', 'persist_ok': True, 'target_ok': True, 'ok': True}, {'msg': 'Note that our budget is $50K for Q1', 'persist_ok': True, 'target_ok': True, 'ok': True}, {'msg': 'Going forward, always CC jake on emails', 'persist_ok': True, 'target_ok': True, 'ok': True}, {'msg': 'Our revenue is $2M ARR', 'persist_ok': True, 'target_ok': True, 'ok': True}, {'msg': 'What time is it?', 'persist_ok': True, 'target_ok': True, 'ok': True}, {'msg': 'Check my calendar', 'persist_ok': True, 'target_ok': True, 'ok': True}, {'msg': 'Send an email to Jake', 'persist_ok': True, 'target_ok': True, 'ok': True}, {'msg': 'Hi Lucy', 'persist_ok': True, 'target_ok': True, 'ok': True}]
- pass_count: 12
- total: 12

## R4: Rich formatting (offline)
**Status:** PASS | **Time:** 2026-02-23 14:09:04 UTC
- pass_count: 8
- total: 8

## R5: UX micro-interactions (offline)
**Status:** PASS | **Time:** 2026-02-23 14:09:04 UTC
- results: [{'turn': 1, 'ok': True}, {'turn': 4, 'ok': True}, {'turn': 7, 'ok': True}, {'check': 'tool_labels', 'ok': True}]

## R6: Tone pipeline (offline)
**Status:** PASS | **Time:** 2026-02-23 14:09:04 UTC
- pass_count: 6
- total: 6

## R9: Composio session isolation (offline)
**Status:** PASS | **Time:** 2026-02-23 14:09:04 UTC
- sessions_keyed_by_workspace: True
- has_cache_lock: True
- has_session_lock: True
- double_checked_locking: True
- lru_eviction: True
- stale_recovery: True
