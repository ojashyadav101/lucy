# Behavior Guide — How Lucy Decides

> A comprehensive reference for understanding Lucy's decision-making at every
> level: when to respond, how to react, what personality to use, how errors
> are handled, and — critically — how changes in one system affect others.

---

## Decision Map: What Happens When a Message Arrives

```
Message arrives in Slack
    │
    ├─── Is it a bot message? → IGNORE
    ├─── Is it a duplicate (30s TTL)? → IGNORE
    │
    ├─── REACTION DECISION
    │    classify_reaction(message)
    │    ├── Thanks/ack/confirmation → react only (🫡/✅/👍), NO reply
    │    ├── FYI message → react only (📝), NO reply
    │    ├── Short react-only + >8 words → react AND reply
    │    └── Everything else → react AND reply
    │
    ├─── FAST PATH DECISION
    │    evaluate_fast_path(message, thread_depth)
    │    ├── Simple greeting, no thread → instant reply from pool
    │    ├── Status check → instant reply from pool
    │    ├── Help request → instant reply from pool
    │    └── Everything else → full agent pipeline
    │
    ├─── EDGE CASE DECISION
    │    ├── "What are you working on?" → task status reply
    │    ├── "Cancel that" → cancel background task
    │    └── Thread interrupt → decide: status/cancel/independent
    │
    ├─── ROUTING DECISION
    │    classify_and_route(message)
    │    ├── Intent: what KIND of request is this?
    │    ├── Model tier: which LLM to use?
    │    └── Prompt modules: which instructions to load?
    │
    ├─── BACKGROUND TASK DECISION
    │    should_run_as_background_task(message, tier)
    │    ├── Frontier tier + heavy signals → background
    │    └── Everything else → synchronous
    │
    ├─── PLANNING DECISION
    │    supervisor._needs_plan(intent, message)
    │    ├── Simple intents (chat, greeting, confirmation) → no plan
    │    └── Complex intents (code, data, reasoning, monitoring) → plan
    │
    └─── EXECUTION + DELIVERY
         Agent loop → output pipeline → Block Kit → Slack
```

---

## Emoji Behavior

Lucy uses emojis in three distinct ways. Each is controlled by a different
system.

### 1. Reaction Emojis (on the user's message)

**Controlled by:** `slack/reactions.py` → `classify_reaction()`

These appear as reactions on the user's message bubble:

| User Says | Reaction | Responds? |
|-----------|----------|-----------|
| "thanks!" | 🫡 saluting_face | No |
| "got it" / "perfect" | ✅ white_check_mark | No |
| "lgtm" / "ship it" | 👍 thumbsup | No |
| FYI context | 📝 memo | No |
| "urgent!" / "asap" | ⚡ zap | Yes |
| "bug" / "error" / "broken" | 🔍 mag | Yes |
| "create" / "build" | 🔨 hammer_and_wrench | Yes |
| "analyze" / "research" | 📊 bar_chart | Yes |
| "deploy" / "ship" | 🚀 rocket | Yes |
| General question | 👀 eyes | Yes |

**Edge case:** If a react-only message has >8 words, Lucy also replies
(the message is substantial enough to warrant a response).

### 2. Working Emojis (while processing)

**Controlled by:** `slack/reactions.py` → `get_working_emoji()`

Added when Lucy starts processing, removed in `finally` block:

| Content | Working Emoji |
|---------|--------------|
| Research/analyze/find | 🔍 mag |
| Create/build/make | 🔨 hammer_and_wrench |
| Deploy/ship/release | 🚀 rocket |
| Default | ⏳ hourglass_flowing_sand |

Additionally, if the queue is busy and the request is LOW priority,
an ⌛ hourglass reaction is added as a backpressure signal.

### 3. Response Emojis (in Lucy's text)

**Controlled by:** `prompts/SOUL.md` + `pipeline/output.py`

Lucy uses 1-2 emojis per message for warmth. This is governed by:
- `SOUL.md`: "1-2 emojis per message. For warmth, not decoration."
- `output.py`: De-AI layer doesn't strip emojis (they're human-like)
- `rich_output.py`: `add_section_emoji()` adds emoji to Block Kit headers

**To change emoji behavior in responses:** Edit `SOUL.md` personality
instructions. The LLM follows these guidelines.

**To change header emojis:** Edit `add_section_emoji()` keyword mapping
in `slack/rich_output.py`.

---

## Progress Message Behavior

When Lucy is working on a multi-turn task, she posts progress updates
to keep the user informed.

### Timing

| Turn | Message Pool |
|------|-------------|
| 3 | `progress_early` — "On it, pulling that together" |
| 8 | `progress_mid` — "Found some good stuff, still working" |
| 13 | `progress_mid` — another variation |
| 18 | `progress_late` — "This is thorough, almost there" |
| 23+ | `progress_late` — continues every 5 turns |

### What Controls This

1. **Timing:** `_agent_loop()` in `core/agent.py` — checks `turn == 3`
   or `turn % 5 == 3`
2. **Message text:** `_describe_progress()` uses `pick()` from
   `pipeline/humanize.py`
3. **Task context:** `_extract_task_hint()` grabs first 60 chars of
   user's message for context
4. **Delivery:** `chat_postMessage` (new message, not edit)

### How to Modify

| Want to... | Change... |
|------------|-----------|
| Change timing | `_agent_loop()` turn checks |
| Change messages | `POOL_CATEGORIES` in `humanize.py` |
| Change fallbacks | `_FALLBACKS` dict in `humanize.py` |
| Add task context | `_describe_progress()` + `_extract_task_hint()` |

---

## Error Handling Behavior

Lucy has a multi-layered error handling system designed to never show
raw errors to users.

### Layer 1: Agent-Level Recovery

```
Tool call fails
    │
    ├── Error appended to messages as tool result
    ├── LLM sees the error and adjusts approach
    ├── Stuck detection: 3+ consecutive errors?
    │     ├── Inject intervention guidance
    │     └── Escalate model
    └── Supervisor checkpoint may trigger REPLAN
```

### Layer 2: Retry with Recovery

```
_run_with_recovery() in handlers.py
    │
    ├── Attempt 1: normal agent.run()
    ├── On failure: wait 2s
    ├── Attempt 2: agent.run(failure_context="what went wrong")
    │     Agent gets context about previous failure
    └── On second failure: graceful degradation
```

### Layer 3: Graceful Degradation

```
Both attempts failed
    │
    ├── error_strategy.classify_error(exception)
    │     → rate_limited / tool_timeout / service_unavailable / ...
    │
    ├── error_strategy.get_recovery_action(error_type)
    │     → Friendly message from humanize pool
    │
    └── Post to user: "I ran into an issue while working on
        {task_hint}. {friendly_message}"
```

### Layer 4: Output Sanitization

Even in successful responses, errors might leak through:

```
_sanitize() strips:
    - File paths (/home/user/..., /workspace/...)
    - Tool names (COMPOSIO_*, lucy_*)
    - API keys (sk_live_*, Bearer *)
    - Internal references (state.json, SKILL.md)

_validate_tone() catches:
    - "tool call(s) failed"
    - "Something went wrong"
    - "running into a loop"
    - "I wasn't able to"
```

### What the User Never Sees

- Raw exception traces
- Tool names (e.g., `COMPOSIO_SEARCH_TOOLS`)
- Internal file paths
- API keys or tokens
- JSON payloads
- Supervisor decisions
- Model names

---

## Personality Pipeline

Lucy's personality is shaped by multiple systems working together.
Understanding this is critical when making changes — personality leaks
from one layer can override another.

### Personality Sources (in priority order)

```
1. SOUL.md (highest priority)
   "Answer first, always. Specificity is warmth. Match the energy."
   Sets: voice, tone, emoji usage, anti-patterns

2. SYSTEM_CORE.md
   "Act, don't narrate. Ask smart questions. Be proactive."
   Sets: behavioral rules, planning approach, verification checklist

3. Humanize pools (for canned messages)
   Pre-generated variations with Lucy's voice traits
   Sets: greeting style, progress messages, error messages

4. _REPHRASER_PROMPT (for dynamic messages)
   "Sharp, warm, and direct. Like the best coworker you've had."
   Sets: on-the-fly message rephrasing

5. Output pipeline (post-processing)
   De-AI removes: em dashes, "delve", "moreover", sycophancy
   Sets: final text cleanup
```

### How to Change Lucy's Personality

| Aspect | Where to Change | Affects |
|--------|----------------|---------|
| Core personality | `prompts/SOUL.md` | All responses |
| Behavioral rules | `prompts/SYSTEM_CORE.md` | How tasks are approached |
| Greeting style | `POOL_CATEGORIES["greeting"]` in `humanize.py` | Fast path responses |
| Progress messages | `POOL_CATEGORIES["progress_*"]` in `humanize.py` | Mid-task updates |
| Error messages | `POOL_CATEGORIES["error_*"]` in `humanize.py` | Failure communications |
| Words to avoid | `_REGEX_DEAI_PATTERNS` in `output.py` | Words stripped from all output |
| Tone rejection | `_TONE_REJECT_PATTERNS` in `output.py` | Phrases blocked from output |
| Emoji in headers | `add_section_emoji()` in `rich_output.py` | Block Kit headers |
| Link formatting | `format_links()` in `rich_output.py` | URL display in responses |

### Personality Anti-Patterns

Lucy explicitly avoids these (enforced by `output.py`):

| Category | Blocked |
|----------|---------|
| Punctuation | Em dashes (—), en dashes (–) |
| Power words | delve, tapestry, landscape, beacon, pivotal |
| Transitions | Moreover, Furthermore, Additionally |
| Hedging | generally speaking, it's worth noting |
| Sycophancy | Absolutely!, Certainly!, Of course! |
| Closers | Hope this helps!, Let me know if you need anything |
| Labels | "Proactive Insight:", summary openers |

---

## How Lucy Approaches Problems

The problem-solving flow is defined in `SYSTEM_CORE.md`:

```
User request arrives
    │
    ├── 1. UNDERSTAND DEEPLY
    │     What exactly does the user want?
    │     What does a successful outcome look like?
    │     Is there ambiguity? If yes → ask before acting
    │
    ├── 2. PLAN (for multi-step tasks)
    │     Supervisor creates execution plan:
    │       - Goal
    │       - Numbered steps with expected tools
    │       - Success criteria
    │       - Anticipated failure modes
    │
    ├── 3. INVESTIGATE THOROUGHLY
    │     At least 2-3 tool calls for data, 3+ sources for research
    │     Multiple tool calls to gather complete data
    │     Cross-reference findings
    │
    ├── 4. WORK BY DOING
    │     Call tools, don't describe what you would do
    │     Execute in parallel when possible
    │     Self-correct on errors
    │
    ├── 5. QUALITY CHECK
    │     Does the response match the request?
    │     Is real data used (not samples/placeholders)?
    │     Are all parts of the question addressed?
    │
    ├── 6. LEARN AND UPDATE
    │     Persist memorable facts to memory
    │     Update LEARNINGS.md for crons
    │     Improve skills for future requests
    │
    └── 7. DELIVER
          Process through output pipeline
          Format for Slack (Block Kit when appropriate)
          Include specifics, not generic advice
```

### Self-Verification Checklist

Before sending any response, Lucy checks (per `SYSTEM_CORE.md`):

- [ ] Did I answer the actual question?
- [ ] Did I use real data, not samples?
- [ ] Did I address ALL parts of the request?
- [ ] Is my response specific, not generic?
- [ ] Did I lead with the answer?
- [ ] Did I avoid internal tool names and paths?

---

## Cross-System Effect Map

This is the most important section of this document. When you change one
thing, these other things may be affected.

### Personality Changes

| Change | Side Effects |
|--------|-------------|
| Edit `SOUL.md` | All LLM responses change tone/style |
| Edit `SYSTEM_CORE.md` | Agent behavior changes (planning, investigation depth) |
| Edit `_REPHRASER_PROMPT` | `humanize()` output changes |
| Edit `_POOL_GENERATOR_PROMPT` | All pool-generated messages change on next refresh |
| Add word to `_REGEX_DEAI_PATTERNS` | Word stripped from ALL responses, including cron outputs |
| Add pattern to `_TONE_REJECT_PATTERNS` | Must also add replacement in `_TONE_REPLACEMENTS` |

### Routing Changes

| Change | Side Effects |
|--------|-------------|
| Add new intent category | Must add to `INTENT_MODULES`, fast path won't catch it |
| Change model tier mapping | All requests of that intent use different model |
| Change `_GREETING_PATTERNS` | Must sync with `_GREETING_RE` in fast_path.py |
| Change `_MONITORING_KEYWORDS` | Affects which requests get supervisor monitoring guidance |

### Tool Changes

| Change | Side Effects |
|--------|-------------|
| Add new tool | Must add to tool registration in `agent.run()` |
| Change tool name | Must update `_REDACT_PATTERNS` in output.py |
| Add destructive tool | Must update `is_destructive_tool_call()` in hitl.py |
| Change Composio meta-tool | Must update `_execute_composio_tool()` in agent.py |

### Memory Changes

| Change | Side Effects |
|--------|-------------|
| Change session memory format | Must update `get_session_context_for_prompt()` |
| Change skill frontmatter | Must update `parse_frontmatter()` + `detect_relevant_skills()` |
| Add memory category | Must update `classify_memory_target()` |
| Change consolidation logic | Affects what gets promoted to permanent knowledge |

### Cron/Heartbeat Changes

| Change | Side Effects |
|--------|-------------|
| Change heartbeat condition types | Must add evaluator function |
| Change HEARTBEAT_OK suppression | Must update `_build_cron_instruction()` |
| Change cron delivery | Must update `_deliver_to_slack()` |
| Change LEARNINGS.md format | Must update `_build_cron_instruction()` reader |

### Slack Layer Changes

| Change | Side Effects |
|--------|-------------|
| Change reaction patterns | May conflict with fast path patterns |
| Change Block Kit limits | Must check `split_response()` thresholds |
| Change HITL destructive list | Must inform agent via tool definitions |
| Change middleware resolution | Must update DB models if schema changes |

---

## Configuration Quick Reference

All configuration lives in `src/lucy/config.py`. Every setting is
controlled via environment variables prefixed with `LUCY_`.

For model tiers, escalation order, and defaults, see
[ARCHITECTURE.md > Model Tier Strategy](./ARCHITECTURE.md#model-tier-strategy).

For all constants, thresholds, and timeout values, see
[QUICK_REFERENCE.md > "Where Is X Defined?"](./QUICK_REFERENCE.md#where-is-x-defined).

---

## Debugging Checklist

When something goes wrong, check these in order:

1. **Response is robotic/AI-sounding**
   - Check `output.py` de-AI patterns — is the word/phrase covered?
   - Check `SOUL.md` — does it address the pattern?
   - Check `_REPHRASER_PROMPT` — is it up to date with voice traits?

2. **Wrong model being used**
   - Check `router.py` — does the message match expected intent?
   - Check model tier settings in `config.py`
   - Check if escalation is triggering unexpectedly

3. **Response too short/incomplete**
   - Check `SYSTEM_CORE.md` — investigation depth rules
   - Check `MAX_TOOL_TURNS` — is the agent running out of turns?
   - Check supervisor — is it aborting too early?

4. **Tool calls failing**
   - Check rate limiter — is the API throttled?
   - Check Composio connection — is the service connected?
   - Check `_run_with_recovery()` — is the retry working?

5. **Cron not firing**
   - Check `validate_cron_expression()` — is the expression valid?
   - Check timezone — is the cron in the right timezone?
   - Check scheduler startup — `await scheduler.start()` called?
   - Check `task.json` — is the file properly formatted?

6. **Heartbeat not alerting**
   - Check `evaluate_due_heartbeats()` — is it being called?
   - Check cooldown — is the alert in cooldown period?
   - Check `consecutive_failures` — is the heartbeat in error state?
   - Check `_slack_alert_channel` in config — is it set correctly?

7. **Memory not persisting**
   - Check `should_persist_memory()` — does the message qualify?
   - Check `classify_memory_target()` — correct category?
   - Check `MAX_SESSION_ITEMS` — rolling window may have evicted it
   - Check workspace locks — concurrent write conflict?

---

## Cursor Rules Reference

Five `.mdc` files in `.cursor/rules/` guide AI-assisted development on the
Lucy codebase. These are NOT runtime rules — they're instructions for
Cursor/Codex when editing code.

### `global.mdc` — Project-Wide Standards

Applies to: `src/**/*.py`, `tests/**/*.py`

| Rule | Requirement |
|------|-------------|
| Python version | 3.12+ with modern syntax (`type` statements, `X \| Y` unions, `match/case`) |
| Type hints | Strict on all public functions, methods, class attributes |
| Future annotations | `from __future__ import annotations` at top of every module |
| Async-first | All I/O uses `async`/`await`. Never `time.sleep()`, never sync `requests`. |
| Module structure | Public API through `__init__.py`, internals prefixed with `_`, no circular imports |
| Package layout | `core/` → `slack/` → `workspace/` → `crons/` → `integrations/` → `db/` |
| Config | All via `config.py` (Pydantic Settings), env vars prefixed `LUCY_` |
| Errors | Custom hierarchy from `LucyError`, never catch bare `Exception` unless re-raising |
| Logging | `structlog` JSON, include `workspace_id`, never `print()` |
| Style | Max 100 chars/line, ruff-sorted imports, no commented-out code |

### `openclaw.mdc` — LLM Integration Protocol

Applies to: `src/lucy/core/**/*.py`

| Rule | Requirement |
|------|-------------|
| Routing | All LLM calls through `OpenClawClient` in `core/openclaw.py` |
| Endpoint | Requests go to OpenRouter (`openrouter.ai/api/v1`) |
| Tool calling | Composio meta-tools as `tools` param, `tool_choice="auto"` |
| OpenClaw Gateway | Secondary (VPS exec/files), NOT in critical LLM path |
| System prompt | Built in `pipeline/prompt.py`, combining SOUL + SYSTEM_CORE + modules |
| Error handling | 5xx retry once after 2s, 4xx log without retry, never expose raw errors |

### `testing.mdc` — Testing Standards

Applies to: `tests/**/*.py`

| Rule | Requirement |
|------|-------------|
| Structure | Tests in `tests/`, mirroring `src/lucy/` structure |
| Framework | `pytest` + `pytest-asyncio` for async tests |
| Naming | Descriptive function names |
| Mocking | Mock ALL external services (Slack, OpenClaw, Composio) |
| Async | Use `unittest.mock.AsyncMock` for async interfaces |
| No real HTTP | Never make real HTTP calls in unit tests |
| Fixtures | Shared in `conftest.py`: `workspace_id`, `db_session`, `mock_slack_client`, `tmp_workspace` |

### `slack.mdc` — Slack Interface Standards

Applies to: `src/lucy/slack/**/*.py`

| Rule | Requirement |
|------|-------------|
| Core principle | Slack is Lucy's only voice, be proactive |
| Workspace resolution | Every handler MUST resolve `workspace_id` from event |
| Message composition | All messages through `blockkit.py`, never construct Block Kit inline |
| Action naming | Block Kit actions prefixed with `lucy_action_` |
| Thread awareness | Reply in thread if triggered from thread; new thread if top-level |
| Rate limiting | 1 message/second/channel, queue for batch operations |
| Error responses | Brief, no stack traces, no model names, no internal details |

### `state.mdc` — State Management

Applies to: `src/lucy/db/**/*.py`, `src/lucy/workspace/**/*.py`

| Rule | Requirement |
|------|-------------|
| Database | SQLAlchemy async is source of truth for relational state |
| Scoping | Every query MUST be scoped by `workspace_id` |
| Migrations | Use Alembic for schema changes |
| Filesystem | Knowledge, skills, learnings, logs as plain files |
| No vector DB | No embeddings/RAG — use grep/file search |
| Multi-tenant | Workspace data NEVER mixed, each has own directory |
| Atomic writes | File writes via `WorkspaceFS` (temp → rename pattern) |

---

## Exception Hierarchy

All domain exceptions inherit from `LucyError`. Each exception type is
raised in specific modules and caught at specific layers.

```
LucyError (src/lucy/core/__init__.py)
│   Root exception for all Lucy domain errors.
│   Raised: anywhere custom domain errors are needed
│   Caught: handlers.py (top-level catch)
│
├── OpenClawError (src/lucy/core/openclaw.py)
│     Attributes: status_code (int | None)
│     Raised when: LLM API returns error (429, 500, 502, 503, 504)
│     Caught in:
│       - agent.py (retries with backoff, escalates model)
│       - handlers.py (classifies for degradation message)
│       - sub_agents.py (retries via _llm_call_with_retry)
│
├── OpenClawGatewayError (src/lucy/integrations/openclaw_gateway.py)
│     Attributes: tool (str) — which Gateway tool failed
│     Raised when: VPS exec/file/web_fetch operations fail
│     Caught in:
│       - mcp_manager.py (aborts MCP installation)
│       - executor.py (falls back to local execution)
│
└── _RetryableComposioError (src/lucy/integrations/composio_client.py)
      Internal only — not exported
      Raised when: Composio SDK returns transient error
      Caught in:
        - composio_client.py (triggers retry with backoff)
```

### Error Propagation

```
Tool execution fails
    │
    ├── Specific exception (OpenClawError, etc.)
    │     Agent loop catches → appends error as tool result
    │     LLM sees error → adjusts approach
    │     Stuck detection may trigger escalation
    │
    └── Unhandled exception
          _run_with_recovery() catches
            ├── Attempt 1: agent.run() fails
            ├── Wait 2s
            ├── Attempt 2: agent.run(failure_context=...)
            └── Both fail → error_strategy.classify_error()
                  → error_strategy.get_recovery_action()
                  → Post friendly error to user
```

---

## Testing Guide

### Test Infrastructure

**File:** `tests/conftest.py`

| Fixture | Scope | Purpose |
|---------|-------|---------|
| `engine` | Session | Async SQLAlchemy engine, creates all tables once |
| `db_session` | Function | Per-test session with auto-rollback |
| `workspace_id` | Function | Random UUID for test isolation |
| `mock_slack_client` | Function | `AsyncMock` with common Slack methods |
| `tmp_workspace` | Function | Temporary workspace directory |

### Test Suites

**`tests/test_quality_fixes.py`** — 12+ tests covering quality improvements:

| Test | What It Verifies |
|------|-----------------|
| Event deduplication | Same (channel, ts) ignored within 30s |
| Retry logic | OpenClaw client retries on 429/500/502/503/504 |
| LLM failure recovery | Agent redirects on LLM error |
| Silent recovery | Error cascade handled without user notification |
| 400 recovery mid-loop | Model escalation on 400 errors |
| Dynamic environment | Connected services injected into prompt |
| Output pipeline | Sanitize → markdown → tone layers work correctly |
| Tool search filtering | Composio tool search returns relevant results |
| System prompt sections | Intelligence sections present in built prompt |
| Context trimming | Messages trimmed when exceeding limit |
| Agent model override | `model_override` parameter respected |

**`tests/test_round4.py`** — 20 tests across 4 feature PRs:

| PR | Tests | Coverage |
|----|-------|----------|
| PR 14: Slack History | A-E | Search, channel listing, result formatting, tool definitions, execution |
| PR 15: System Prompt | F-J | SOUL.md loading, module inclusion, skill injection, connected services, prompt structure |
| PR 16: File Tools | K-O | PDF generation, Excel generation, CSV generation, file upload, tool definitions |
| PR 17: Edge Cases | P-T | Status queries, cancellation, deduplication, degradation, thread interrupts |

### Running Tests

```bash
# All tests
pytest tests/ -v

# Specific suite
pytest tests/test_quality_fixes.py -v

# Single test
pytest tests/test_quality_fixes.py::test_event_deduplication -v

# Stop on first failure
pytest -x

# With coverage
pytest --cov=lucy tests/
```

### Writing New Tests

1. Create test file in `tests/` mirroring the source structure
2. Import fixtures from `conftest.py`
3. Mock all external services using `AsyncMock`
4. Use `@pytest.mark.asyncio` for async tests
5. Assert specific behavior, not implementation details
6. Keep tests independent — no shared mutable state

### What Should Be Tested

- Every new function with business logic
- All error paths (not just happy paths)
- Edge cases identified in `pipeline/edge_cases.py`
- Output pipeline changes (patterns added to sanitize/tone/de-AI)
- Router classification changes (new intents or pattern changes)
- Memory persistence logic changes
