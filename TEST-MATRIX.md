# Lucy Test Matrix & Results

> **Created**: Feb 22, 2026  
> **Last Run**: Feb 23, 2026 (10:00 IST)  
> **Models**: Dynamic routing — `gemini-2.5-flash` / `minimax-m2.5` / `deepseek-v3` / `claude-sonnet-4`  
> **Result**: **L1-L5: 32/32 (100%)** | **Stress: 4/5 (80%)** | **Infra: All passing**

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
