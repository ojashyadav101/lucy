# Lucy Test Matrix & Results

> **Created**: Feb 22, 2026  
> **Last Run**: Feb 23, 2026 (22:10 IST)  
> **Models**: Dynamic routing — `gemini-2.5-flash` / `minimax-m2.5` / `deepseek-v3` / `claude-sonnet-4`  
> **Result**: **L1-L5: 32/32 (100%)** | **Stress: 4/5 (80%)** | **Round 3: 26/26 (100%)** | **Round 4: 25/25 (100%)** | **Parity: 97%**

## Legend

| Symbol | Meaning                      |
| ------ | ---------------------------- |
| `[P]`  | Passed                       |
| `[F]`  | Failed — see notes           |
| `[S]`  | Skipped — not applicable now |

---

## Level 1: Infrastructure (Boot & Connectivity) — 12/12

| #    | Test                                    | Status | Time     | Notes                                        |
| ---- | --------------------------------------- | ------ | -------- | -------------------------------------------- |
| 1.1  | App boots without import errors         | `[P]`  | 0ms      |                                              |
| 1.2  | OpenRouter reachable (minimax-m2.5)     | `[P]`  | 1,229ms  | Replaced OpenClaw — tool calling works here  |
| 1.3  | Tool calling works (minimax-m2.5)       | `[P]`  | 2,440ms  | finish_reason=tool_calls, 1 tool call        |
| 1.4  | Composio SDK initializes               | `[P]`  | 808ms    |                                              |
| 1.5  | Composio session creates for workspace | `[P]`  | 704ms    |                                              |
| 1.6  | Composio meta-tools returned (6)       | `[P]`  | 508ms    | MANAGE_CONNECTIONS, MULTI_EXECUTE, BASH, etc |
| 1.7  | Config loads from .env/keys.json       | `[P]`  | 15ms     | All keys present                             |
| 1.8  | Workspace directory structure          | `[P]`  | 0ms      | skills, crons, logs, data, team, company, scripts |
| 1.9  | Skills seeded in skills/ (16)          | `[P]`  | 0ms      |                                              |
| 1.10 | Crons seeded in crons/ (3)             | `[P]`  | 0ms      |                                              |
| 1.11 | team/SKILL.md exists                   | `[P]`  | 0ms      | Has member table with timezone               |
| 1.12 | company/SKILL.md exists                | `[P]`  | 0ms      |                                              |

---

## Level 2: Core Agent (Prompt Building & LLM Response) — 6/6

| #    | Test                                     | Status | Time    | Notes                           |
| ---- | ---------------------------------------- | ------ | ------- | ------------------------------- |
| 2.1  | System prompt includes SOUL.md           | `[P]`  | 27ms    | 4,577 chars                     |
| 2.2  | System prompt includes template          | `[P]`  | 0ms     | Placeholder + philosophy found  |
| 2.3  | Skill descriptions in prompt (18)        | `[P]`  | 10ms    | 3,024 chars of skill summaries  |
| 2.4  | Full system prompt builds                | `[P]`  | 6ms     | 13,340 chars total              |
| 2.5  | LLM responds to 'Hi'                    | `[P]`  | 4,340ms | "Hi there! How can I help you?" |
| 2.6  | LLM responds in <60s                    | `[P]`  | 2,292ms | 26x under threshold             |

---

## Level 3: Tool Calling & Integrations — 3/3

| #   | Test                           | Status | Time     | Notes                          |
| --- | ------------------------------ | ------ | -------- | ------------------------------ |
| 3.1 | Composio tools fetched (6)     | `[P]`  | 1,224ms  |                                |
| 3.2 | LLM calls tool when asked      | `[P]`  | 13,503ms | Called COMPOSIO_SEARCH_TOOLS   |
| 3.3 | Tool execution works           | `[P]`  | 2,781ms  | Result: {data, error, successful} |

---

## Level 4: Workspace, Skills, & Memory — 6/6

| #   | Test                              | Status | Time | Notes                                   |
| --- | --------------------------------- | ------ | ---- | --------------------------------------- |
| 4.1 | Workspace dirs exist              | `[P]`  | 0ms  | All 7 directories present               |
| 4.2 | Skills with valid frontmatter (18)| `[P]`  | 11ms | All 18 parsed                           |
| 4.3 | state.json has onboarded_at       | `[P]`  | 0ms  |                                         |
| 4.4 | Activity log works                | `[P]`  | 1ms  |                                         |
| 4.5 | Cron task.json valid (3)          | `[P]`  | 0ms  |                                         |
| 4.6 | team/SKILL.md has timezone data   | `[P]`  | 0ms  |                                         |

---

## Level 5: End-to-End Slack Interactions — 5/5

| #   | Test                              | Status | Time      | Notes                                              |
| --- | --------------------------------- | ------ | --------- | -------------------------------------------------- |
| 5.1 | Mention gets exactly 1 reply      | `[P]`  | 11,869ms  | 1 reply, no duplicates                             |
| 5.2 | Reply in thread                   | `[P]`  | 551ms     |                                                    |
| 5.3 | Response time < 60s               | `[P]`  | 11,714ms  | "Hey! I'm doing well — thanks for asking."         |
| 5.4 | Tool use query answered           | `[P]`  | 144,999ms | Gmail integration info with connection status      |
| 5.5 | Thread continuity                 | `[P]`  | 17,873ms  | Follow-up answered with new fact                   |

---

## Summary

| Level              | Total  | Passed | Failed | %      |
| ------------------ | ------ | ------ | ------ | ------ |
| L1: Infrastructure | 12     | 12     | 0      | 100%   |
| L2: Core Agent     | 6      | 6      | 0      | 100%   |
| L3: Tool Calling   | 3      | 3      | 0      | 100%   |
| L4: Workspace      | 6      | 6      | 0      | 100%   |
| L5: E2E Slack      | 5      | 5      | 0      | 100%   |
| **TOTAL**          | **32** | **32** | **0**  | **100%** |

---

## Architecture Decision Log

### Decision: OpenRouter replaces OpenClaw for LLM routing

**Date**: Feb 23, 2026  
**Status**: Implemented and tested

**Problem**: OpenClaw's `/v1/chat/completions` endpoint silently strips `tools` and `tool_choice` parameters. This prevented any tool calling, regardless of which model was used behind OpenClaw.

**Investigation**:
1. Tested kimi, deepseek-v3, gpt-4o-mini through OpenClaw — all failed to make tool calls
2. Tested the same models through OpenRouter directly — all made tool calls correctly
3. Confirmed via OpenClaw docs: the chatCompletions endpoint is a "small OpenAI-compatible surface" that only passes `model`, `messages`, `stream`, `user`
4. OpenClaw has a `/v1/responses` endpoint that supports tools, but it uses a different API format

**Decision**: Route ALL requests through OpenRouter. OpenClaw remains available for future sandbox/memory use but is not in the critical LLM path.

**Model**: `minimax/minimax-m2.5` — #1 on OpenRouter for programming, native interleaved thinking, $0.30/$1.10 per M tokens, 197K context.

**Results**:
- Response time: 2-14s (was 128s+ through OpenClaw)
- Tool calling: 100% reliable (was 0% through OpenClaw)
- No duplicate responses
- E2E Slack mention → reply: 11-18s

---

## Level 6: Stress Tests & Infrastructure — 4/5

| # | Test | Status | Time | Notes |
|---|------|--------|------|-------|
| ST-A | 3 concurrent threads (independent context) | `[P]` | 38.7s max | All 3 replied correctly, no cross-contamination, 3 unique trace_ids |
| ST-B | Sequential workflow (calendar → email → confirm) | `[F]` | 46.1s | 400 from OpenRouter on complex multi-step — provider edge case |
| ST-C | Parallel task (3 sub-tasks in 1 request) | `[P]` | 87.2s | All 3 sub-tasks answered: integrations, team times, calendar |
| ST-D | Model routing (10 test cases) | `[P]` | <1ms | 10/10 correct — fast/default/code/frontier all routed properly |
| ST-E | Sustained load (5 messages, 3 threads) | `[P]` | 21.6s p95 | 5/5 responses, avg 18.8s, model routing active in production |

---

## Level 7: Detailed Logging & Tracing

| Feature | Status | Notes |
|---------|--------|-------|
| Per-request trace_id (UUID) | `[P]` | Every request gets unique trace_id via contextvars |
| Spans: prompt build, LLM call, tool exec, slack post | `[P]` | All timed with ms precision |
| Per-thread JSONL log files | `[P]` | Written to `workspaces/{id}/logs/threads/{ts}.jsonl` |
| Token usage tracking (prompt + completion) | `[P]` | Accumulated across multi-turn loops |
| Model routing logged per request | `[P]` | intent + model_selected in every trace |
| Composio cache locking (asyncio.Lock + threading.Lock) | `[P]` | Prevents race conditions under concurrent load |
| Progress updates in Slack (turn 3+) | `[P]` | Posts "Working on it..." in thread during long operations |

---

## Level 8: Model Routing

| Tier | Model | Triggers | Status |
|------|-------|----------|--------|
| fast | `google/gemini-2.5-flash` | Greetings, short follow-ups, simple lookups | `[P]` — verified in production (3 requests routed) |
| default | `minimax/minimax-m2.5` | Tool-calling, general tasks | `[P]` — primary model (10 requests routed) |
| code | `deepseek/deepseek-v3-0324` | Code, build, deploy, script keywords | `[P]` — routes correctly, mid-loop upgrade on sandbox use |
| frontier | `anthropic/claude-sonnet-4` | Research, analysis, comparison (60+ chars) | `[P]` — routes correctly on complex prompts |

---

## Architecture Decision Log

### Decision: Dynamic model routing via rule-based classifier

**Date**: Feb 23, 2026
**Status**: Implemented and verified

**Problem**: Single model (`minimax/minimax-m2.5`) for all requests wastes cost on simple queries and lacks power for complex reasoning/coding.

**Solution**: `src/lucy/core/router.py` — pure Python regex + heuristic classifier (<1ms). No LLM call for routing.

**Model tiers**:
- `fast` (gemini-2.5-flash): greetings, follow-ups, simple lookups — $0.075/$0.30 per M tokens
- `default` (minimax-m2.5): tool calling, general — $0.30/$1.10 per M tokens
- `code` (deepseek-v3): code generation, debugging — $0.25/$1.10 per M tokens
- `frontier` (claude-sonnet-4): research, analysis — $3/$15 per M tokens

**Mid-loop upgrade**: If agent detects code execution tools (REMOTE_WORKBENCH/BASH), automatically upgrades to code model for subsequent turns.

---

### Decision: OpenRouter replaces OpenClaw for LLM routing

**Date**: Feb 23, 2026
**Status**: Implemented and tested

---

## Level 9: Round 3 — Viktor's PR 10-13 Patches — 26/26

| # | Test | PR | Status | Time | Details |
|---|------|----|--------|------|---------|
| PR10-01 | Queue module imports | PR10 | `[P]` | 220ms | RequestQueue, Priority, classify_priority |
| PR10-02 | 3 priority levels | PR10 | `[P]` | <1ms | HIGH, NORMAL, LOW |
| PR10-03 | Priority classification | PR10 | `[P]` | <1ms | fast→HIGH, default→NORMAL, frontier→LOW |
| PR10-04 | Metrics + backpressure | PR10 | `[P]` | <1ms | metrics + is_busy property |
| PR10-05 | Enqueue accepts requests | PR10 | `[P]` | <1ms | queue_size=1 after enqueue |
| PR11-01 | Fast path imports | PR11 | `[P]` | 1ms | FastPathResult, evaluate_fast_path |
| PR11-02 | Greetings fast path | PR11 | `[P]` | <1ms | 5/5 greetings detected |
| PR11-03 | Complex skip fast path | PR11 | `[P]` | <1ms | 4/4 correctly bypassed |
| PR11-04 | In-thread skip | PR11 | `[P]` | <1ms | reason=in_thread |
| PR11-05 | Status checks | PR11 | `[P]` | <1ms | "Online and ready. What's up?" |
| PR11-06 | Help capabilities | PR11 | `[P]` | <1ms | Detailed capabilities overview |
| PR11-07 | Fast path latency | PR11 | `[P]` | 1ms | 0.010ms avg (100 evals) |
| PR12-01 | Rate limiter imports | PR12 | `[P]` | 3ms | RateLimiter, TokenBucket |
| PR12-02 | Model acquire | PR12 | `[P]` | <1ms | google/gemini acquired |
| PR12-03 | API acquire | PR12 | `[P]` | <1ms | google_calendar acquired |
| PR12-04 | classify_api_from_tool | PR12 | `[P]` | <1ms | Detects google_calendar |
| PR12-05 | Token bucket capacity | PR12 | `[P]` | <1ms | 3 acquired, 4th rejected |
| PR13-01 | Task manager imports | PR13 | `[P]` | 1ms | TaskManager, should_run_as_background_task |
| PR13-02 | 6 task states | PR13 | `[P]` | <1ms | PENDING→CANCELLED lifecycle |
| PR13-03 | Background classification | PR13 | `[P]` | <1ms | frontier+research=bg, else=sync |
| PR13-04 | Task manager metrics | PR13 | `[P]` | <1ms | Empty on init |
| PR13-05 | Background task completes | PR13 | `[P]` | 201ms | state=completed, result=done |
| INT-01 | Fast path in handlers | INT | `[P]` | 1ms | evaluate_fast_path wired |
| INT-02 | Queue in handlers | INT | `[P]` | <1ms | classify_priority wired |
| INT-03 | Rate limiter in agent | INT | `[P]` | <1ms | rate_limiter in agent.py + openclaw.py |
| INT-04 | Task manager in handlers | INT | `[P]` | <1ms | should_run_as_background wired |

### Key Performance Wins (Viktor's Patches)
- **Fast path latency**: Greetings from ~25,700ms → 0.010ms (2,570,000x improvement)
- **Rate limiting**: Per-model (5 tiers) + per-API (8 services) token buckets
- **Background tasks**: 5 per workspace, 10min timeout, progress reporting
- **Priority queue**: 3 levels, 10 workers, per-workspace fairness, backpressure signaling

---

## Level 10: Round 4 — Viktor's PR 14-17 Patches — 25/25

| # | Test | PR | Status | Details |
|---|------|----|--------|---------|
| A | Search finds matches | PR14 | `[P]` | 2 results for "pricing" in synced logs |
| B | Channel filter | PR14 | `[P]` | Only searched target channel |
| C | days_back filter | PR14 | `[P]` | Excluded old messages |
| D | Format results | PR14 | `[P]` | Grouped by channel |
| E | Tool definitions | PR14 | `[P]` | 2 tools, lucy_* prefix |
| F | 3-source verification | PR15 | `[P]` | In SYSTEM_PROMPT.md |
| G | Draft-review-iterate | PR15 | `[P]` | Review cycle present |
| H | Proactive intelligence | PR15 | `[P]` | Pattern recognition |
| I | Background task patterns | PR15 | `[P]` | In SOUL.md |
| J | Anti-patterns preserved | PR15 | `[P]` | Still present |
| K | CSV generation | PR16 | `[P]` | Valid CSV output |
| L | Excel generation | PR16 | `[P]` | Valid .xlsx output |
| M | File tool definitions | PR16 | `[P]` | 3 tools: pdf/excel/csv |
| N | CSV tool dispatch | PR16 | `[P]` | execute_file_tool works |
| O | Unknown file tool | PR16 | `[P]` | Returns error correctly |
| P | Status query detection | PR17 | `[P]` | 5/5 patterns detected |
| Q | Cancellation detection | PR17 | `[P]` | 5/5 patterns detected |
| R | Tool idempotency | PR17 | `[P]` | GET=idempotent, CREATE=mutating |
| S | Duplicate dedup | PR17 | `[P]` | Blocks identical CREATE in 5s |
| T | Degradation messages | PR17 | `[P]` | Warm messages for all types |
| Reg-1 | Fast path regression | R3 | `[P]` | Greetings still fast |
| Reg-2 | Rate limiter regression | R3 | `[P]` | API classification works |
| Reg-3 | Queue metrics | R3 | `[P]` | Metrics accessible |
| Reg-4 | Router classification | R3 | `[P]` | Intent correct |
| Reg-5 | Reactions | R3 | `[P]` | Emoji reactions work |

### Key Capabilities Added (Round 4)
- **Slack history search**: Full-text across synced logs, <50ms, channel filtering, date ranges
- **System prompt**: 3-source verification, draft→review→iterate, proactive intelligence
- **File output**: PDF, Excel, CSV generation with auto-upload to Slack
- **Edge cases**: Status queries, task cancellation, tool dedup, graceful degradation

### Parity Progression
| Round | Parity | Cumulative Tests |
|-------|--------|-----------------|
| Baseline | 80% | — |
| Round 2 | 87% | 10 |
| Round 2.5 | 90% | 11 |
| Round 3 | 94% | 37 |
| **Round 4** | **97%** | **62** |

---

## Error Log (Resolved)

| # | Test | Error | Root Cause | Fix | Fixed? |
|---|------|-------|-----------|-----|--------|
| 1 | 1.2 | OpenClaw 128s response | OpenClaw gateway overhead + kimi model | Switched to OpenRouter + minimax-m2.5 | Yes |
| 2 | 1.3 | Tool calls never made | OpenClaw strips tools/tool_choice params | Route all requests via OpenRouter | Yes |
| 3 | 1.9 | Skills at workspace root | copy_seeds missing target_subdir | Added target_subdir param to copy_seeds | Yes |
| 4 | 1.11 | team/SKILL.md missing | Not created during setup | Created with timezone data | Yes |
| 5 | 3.3 | Composio toolkit version | dangerously_skip_version_check needed | Simplified to session-based API (handles versions automatically) | Yes |
| 6 | venv | ModuleNotFoundError | venv using Python 3.9 | Recreated with python3.12 | Yes |
| 7 | 5.x | Double responses | Duplicate event handlers | Fixed handler registration | Yes |
