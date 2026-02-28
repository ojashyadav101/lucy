# Message Pipeline — Deep Dive

> How Lucy classifies intent, selects a model, builds the system prompt,
> processes LLM output, and generates natural-sounding messages.

---

## Pipeline Overview

```
User message arrives
    │
    ▼
┌──────────────────────────────────────────────────┐
│ 1. FAST PATH (fast_path.py)                      │
│    Simple greetings/acks → instant reply          │
│    No LLM call, <500ms                           │
└───────────────┬──────────────────────────────────┘
                │ not fast
                ▼
┌──────────────────────────────────────────────────┐
│ 2. EDGE CASES (edge_cases.py)                    │
│    Status queries, cancellations, deduplication   │
└───────────────┬──────────────────────────────────┘
                │ normal message
                ▼
┌──────────────────────────────────────────────────┐
│ 3. ROUTER (router.py)                            │
│    classify_and_route() → ModelChoice             │
│    Intent + model tier + prompt modules           │
└───────────────┬──────────────────────────────────┘
                │
                ▼
┌──────────────────────────────────────────────────┐
│ 4. PROMPT BUILDER (prompt.py)                    │
│    build_system_prompt()                          │
│    Static prefix (cacheable) + dynamic suffix     │
└───────────────┬──────────────────────────────────┘
                │
                ▼
         [Agent Loop runs — see AGENT_LOOP.md]
                │
                ▼
┌──────────────────────────────────────────────────┐
│ 5. OUTPUT PIPELINE (output.py)                   │
│    4 layers: sanitize → markdown → tone → de-AI  │
└───────────────┬──────────────────────────────────┘
                │
                ▼
        Response delivered to Slack
```

---

## 1. Fast Path

**File:** `src/lucy/pipeline/fast_path.py`

The fast path intercepts simple messages that don't need the full agent loop.
No LLM call is made — responses come from pre-generated pools.

### Qualification Rules

A message qualifies for the fast path if **all** of these are true:
- Not in a thread (no thread context, depth = 0)
- Message length ≤ 80 characters

AND one of these patterns matches:

| Pattern | Pool | Example |
|---------|------|---------|
| Greeting | `pick("greeting")` | "hi", "hey", "hello lucy", "good morning" |
| Conversational greeting | `pick("greeting")` | "how's it going", "what's up" |
| Near-empty (< 3 chars after removing dots) | Hardcoded | ".", ".." |
| Status check | `pick("status")` | "are you there", "ping", "alive?" |
| Help request | `pick("help")` | "help", "what can you do", "who are you" |

### What Doesn't Qualify

- Messages in threads (user expects context-aware response)
- Messages > 80 characters (likely substantive)
- Messages with tool keywords (need agent processing)
- Greetings that also contain a question (e.g., "hey can you check X")

### FastPathResult

```python
@dataclass
class FastPathResult:
    is_fast: bool
    response: str | None
    reason: str = ""    # "greeting", "status", "help", "near_empty"
```

---

## 2. Edge Cases

**File:** `src/lucy/pipeline/edge_cases.py`

Handles special situations before the agent loop runs.

### Status Queries

Detected via regex patterns:
- "what are you working on"
- "any update/progress/status"
- "still working on"
- "is that done/ready"

When detected → `format_task_status()` returns a human-friendly status of
the current background task (description, state, elapsed time).

### Task Cancellation

Detected via regex:
- "cancel/stop/abort that"
- "nevermind" / "never mind"
- "don't bother"
- "scratch that" / "forget it"

When detected → `handle_task_cancellation()` cancels the most recent
background task and returns a confirmation message.

### Thread Interrupts

When a user sends a message in a thread where a background task is running:

```python
@dataclass
class InterruptDecision:
    action: str    # "status_reply", "cancel_task", "respond_independently"
    reason: str
```

| Message Type | Decision |
|-------------|----------|
| Status query | `status_reply` |
| Cancellation | `cancel_task` |
| Short/simple | `respond_independently` |
| Complex new request | `respond_independently` |

### Tool Deduplication

Prevents accidentally executing the same mutating action twice:

```python
should_deduplicate_tool_call(
    tool_name, parameters, recent_calls, window_seconds=5.0
) -> bool
```

- Idempotent tools (get, list, search, fetch): always allowed
- Mutating tools (create, delete, send, deploy): blocked if exact-same
  call was made within 5 seconds

### Graceful Degradation

When an error occurs, it's classified and routed to a user-friendly message:

| Error Type | Example | Pool |
|-----------|---------|------|
| `rate_limited` | 429, "rate limit" | `error_rate_limit` |
| `tool_timeout` | "timeout", "timed out" | `error_connection` |
| `service_unavailable` | 502, 503, 504 | `error_connection` |
| `context_overflow` | "context length exceeded" | `error_generic` |

---

## 3. Router

**File:** `src/lucy/pipeline/router.py`

The router is a pure regex-based classifier — no LLM call, runs in <1ms.

### Classification Logic (Priority Order)

```
Message arrives
  │
  ├── Pure greeting (hi/hey/hello/thanks/ok/yes/no)?
  │     ├── After tool calls? → "confirmation" (default tier)
  │     └── Otherwise → "chat" (fast tier)
  │
  ├── Short message in deep thread (>5 depth)?
  │     ├── Action verbs (do/send/run/execute)? → "command" (default)
  │     └── Otherwise → "followup" (fast or default)
  │
  ├── Monitoring keywords (inform me when, keep monitoring)?
  │     → "monitoring" (default tier)
  │
  ├── Data task keywords (all users, full report, conversion rate)?
  │     → "data" (code tier)
  │
  ├── Document keywords (pdf, report, spreadsheet)?
  │     → "document" (document tier)
  │
  ├── Heavy research (deep dive, comprehensive, benchmark)?
  │     OR 3+ light research matches?
  │     OR 2+ light matches with >50 chars?
  │     → "reasoning" (research tier)
  │
  ├── Light research (research, analyze, compare)?
  │     1+ match, >40 chars?
  │     → "tool_use" (default tier)
  │
  ├── Code keywords (code, deploy, script, refactor)?
  │     ├── Short + check pattern? → "tool_use" (default)
  │     └── Otherwise → "code" (code tier)
  │
  ├── Data source keywords (calendar, email, github, linear)?
  │     → "tool_use" (default tier)
  │
  ├── Short check (<60 chars)?
  │     → "tool_use" (default tier)
  │
  ├── Simple question (<40 chars, starts with what/when/where)?
  │     → "lookup" (fast tier)
  │
  └── Default → "tool_use" (default tier)
```

### ModelChoice Output

```python
@dataclass
class ModelChoice:
    intent: str               # "chat", "code", "data", "reasoning", etc.
    model: str                # Actual model name from settings
    tier: str                 # "fast", "default", "code", etc.
    prompt_modules: list[str] # ["coding"], ["data_tasks"], etc.
```

### Intent → Prompt Module Mapping

| Intent | Modules Loaded |
|--------|---------------|
| `code` | `["coding"]` |
| `reasoning` | `["research"]` |
| `document` | `["data_tasks"]` |
| `data` | `["data_tasks"]` |
| `command` | `["integrations"]` |
| `chat`, `lookup`, `confirmation`, `followup`, `tool_use`, `monitoring` | `[]` |

In addition, `_COMMON_MODULES = ["tool_use", "memory"]` are always loaded
for any non-`chat` intent.

---

## 4. Prompt Builder

**File:** `src/lucy/pipeline/prompt.py`

Assembles the system prompt from multiple sources. Uses a **static prefix +
dynamic suffix** architecture so the static portion can be cached by the LLM
provider.

### System Prompt Structure

```
┌─────────────────────────────────────────────────┐
│  STATIC PREFIX (cacheable across requests)      │
│                                                 │
│  1. SOUL.md (prompts/SOUL.md)                   │
│     Lucy's personality, voice, anti-patterns    │
│                                                 │
│  2. SYSTEM_CORE.md (prompts/SYSTEM_CORE.md)     │
│     Core instructions, planning rules,          │
│     verification checklist, tool restraint      │
│     Skills descriptions injected here           │
│                                                 │
│  3. Common modules (tool_use.md + memory.md)    │
│     Loaded for all non-chat intents             │
│                                                 │
│  4. Connected services environment block        │
│     "Connected: Google Calendar, GitHub, ..."   │
│                                                 │
│  5. Email identity (if AgentMail enabled)       │
│     "Your email: lucy@zeeyamail.com"            │
│                                                 │
│  6. Spaces capability (if Spaces enabled)       │
│     "You can build web apps with Lucy Spaces"   │
├─────────────────────────────────────────────────┤
│  DYNAMIC SUFFIX (varies per request)            │
│                                                 │
│  7. Intent-specific modules                     │
│     e.g., coding.md, research.md, data_tasks.md │
│                                                 │
│  8. Custom integrations block                   │
│     Discovered wrapper tools                    │
│                                                 │
│  9. Relevant skill content                      │
│     Skills matching user message (max 8000 ch)  │
│                                                 │
│  10. Knowledge blocks                           │
│      company/SKILL.md + team/SKILL.md content   │
└─────────────────────────────────────────────────┘
```

### How Skills Get Into the Prompt

1. `get_skill_descriptions_for_prompt(ws)` — lists all skill names +
   one-line descriptions (injected into SYSTEM_CORE.md)
2. `detect_relevant_skills(message)` — regex-based matching, returns
   up to 3 skill names sorted by match count
3. `load_relevant_skill_content(ws, message)` — loads full content of
   matched skills (max 8000 chars total)
4. `get_key_skill_content(ws)` — loads `company/SKILL.md` and
   `team/SKILL.md` for direct injection as knowledge

### Prompt Module Files

Located in `prompts/modules/`:

| Module | When Loaded |
|--------|-------------|
| `tool_use.md` | All non-chat intents (common module) |
| `memory.md` | All non-chat intents (common module) |
| `coding.md` | Intent = `code` |
| `research.md` | Intent = `reasoning` |
| `data_tasks.md` | Intent = `data` or `document` |
| `integrations.md` | Intent = `command` |

---

## 5. Output Pipeline

**File:** `src/lucy/pipeline/output.py`

Every LLM response passes through 4 processing layers before reaching the
user.

### Layer 1: Sanitize (`_sanitize`)

Strips internal references that should never reach the user:

| Category | Examples Stripped |
|----------|-----------------|
| File paths | `/home/user/...`, `/workspace/...`, `/Users/...` |
| Tool names | `COMPOSIO_SEARCH_TOOLS`, `lucy_write_file`, `COMPOSIO_REMOTE_WORKBENCH` |
| Service names | `composio`, `openrouter`, `openclaw` |
| Internal files | `SKILL.md`, `LEARNINGS.md`, `state.json`, `task.json` |
| API keys | `sk_live_*`, `Bearer *`, `pol_*` |
| XML tags | `<invoke>...</invoke>` |
| UUIDs | `[0-9a-f]{8}-[0-9a-f]{4}-...` |

Tool names are humanized: `COMPOSIO_SEARCH_TOOLS` → "search for tools"

### Layer 2: Markdown → Slack (`_convert_markdown_to_slack`)

| Markdown | Slack mrkdwn |
|----------|-------------|
| `**bold**` | `*bold*` |
| `# Header` | `*Header*` |
| `[text](url)` | `<url\|text>` |
| Tables | Bullet lists |
| Triple newlines | Double newlines |

Also runs `_fix_broken_urls()` to remove truncated/broken URLs.

### Layer 3: Tone Validation (`_validate_tone`)

Catches robotic patterns and replaces them:

| Pattern Rejected | Why |
|-----------------|-----|
| "I wasn't able to" | Defeatist |
| "Could you try rephrasing" | Deflecting |
| "running into a loop" | Exposes internals |
| "tool call(s) failed" | Exposes internals |
| "Something went wrong" | Vague |
| "several tool calls" | Exposes internals |
| "great/excellent/wonderful question" | Sycophantic |
| "happy to help" | Generic AI |
| "let me delve into" | AI tell |

### Layer 4: De-AI (`_deai`)

Removes patterns that make text sound AI-generated.

**AI Tell Categories and Weights:**

| Category | Examples | Weight |
|----------|---------|--------|
| Punctuation | Em dashes (—), en dashes (–) | 1 |
| Power words | delve, tapestry, landscape, beacon, pivotal | 2 |
| Formal transitions | Moreover, Furthermore, Additionally | 2 |
| Hedging | generally speaking, it's worth noting | 2 |
| Sycophancy | Absolutely!, Certainly!, Of course! | 2 |
| Chatbot closers | Hope this helps!, Let me know if you need anything | 3 |
| Structural patterns | "It's not just about X, it's about Y" | 2 |
| Lucy-specific | "Proactive Insight" | 2 |

**Two-tier approach:**
1. **Regex pass (always runs):** Strips em dashes, power words, transitions,
   closers, sycophantic openers, Lucy-specific labels
2. **LLM rewrite (disabled):** `_LLM_REWRITE_THRESHOLD = 999` effectively
   disables the secondary LLM rewrite. When enabled, it would use
   `minimax/minimax-m2.5` to contextually rewrite heavily AI-sounding text.

### Public API

```python
process_output(text: str | None) -> str        # async, full pipeline
process_output_sync(text: str) -> str           # sync fallback, regex-only de-AI
```

---

## 6. Humanize System

**File:** `src/lucy/pipeline/humanize.py`

Generates natural, varied messages for common situations (progress updates,
errors, greetings). Uses pre-generated pools so responses are instant
(no LLM call at runtime).

### How Pools Work

1. **At startup:** `initialize_pools()` makes a single LLM call with all
   pool categories, generating 6 variations per category
2. **At runtime:** `pick(category)` selects a random variation from the pool
3. **Fallback:** if pools aren't ready, hardcoded `_FALLBACKS` are used
4. **Refresh:** `refresh_pools()` regenerates every 6 hours

### Pool Categories

| Category | When Used | Example |
|----------|-----------|---------|
| `greeting` | Fast path greeting response | "Hey! What's on your plate today?" |
| `status` | "Are you there?" check | "Yep, I'm here and ready to go" |
| `help` | "What can you do?" | Brief introduction |
| `progress_early` | Turn 3 of agent loop | "On it, pulling that together now" |
| `progress_mid` | Mid-loop update | "Making progress, found some good stuff" |
| `progress_late` | Taking longer than usual | "This is a thorough one, almost there" |
| `progress_final` | Near completion | "Finishing touches, should have this soon" |
| `task_cancelled` | User cancelled task | "All good, I've stopped working on that" |
| `task_background_ack` | Background task started | "I'll work on this in the background" |
| `error_rate_limit` | 429 from API | "Getting a lot of requests, give me a sec" |
| `error_connection` | Service timeout | "Having trouble reaching that service" |
| `error_generic` | Unknown error | "Hit a snag, trying a different approach" |
| `error_task_failed` | Background task failed | "Ran into an issue with that task" |
| `supervisor_replan` | Supervisor replans | "Adjusting my approach on this" |
| `supervisor_ask_user` | Need clarification | "Quick question before I continue..." |
| `hitl_approved` | User approved action | "{user} approved, executing now" |
| `hitl_expired` | Approval expired | "That approval has expired" |
| `hitl_cancelled` | User cancelled action | "{user} cancelled that action" |

### `humanize()` vs `pick()`

| | `pick()` | `humanize()` |
|---|---------|-------------|
| **Speed** | Instant (0ms) | ~500ms (LLM call) |
| **Source** | Pre-generated pool | Real-time LLM generation |
| **Use case** | Repeated messages (progress, errors) | One-off context-specific messages |
| **Params** | `category`, `**format_kwargs` | `intent`, `context`, `task_hint`, `user_name` |
| **Model** | None (pool lookup) | `minimax/minimax-m2.5` |

### Prompt Templates

**Rephraser (for `humanize()`):**
> You are Lucy, an AI coworker who's sharp, warm, and direct. Rephrase the
> following message in Lucy's voice. Lucy's style: conversational but
> competent, like the best coworker you've had. She leads with the answer,
> uses contractions naturally, and mixes short punchy sentences with longer
> ones. She uses 1-2 emojis for warmth (not decoration). She never uses em
> dashes, 'delve', or corporate filler. Keep it to 1-2 sentences.

**Pool generator (for `initialize_pools()`):**
> Generate exactly 6 variations per category. Each variation must have a
> DIFFERENT structure. Vary sentence length, opening word, emoji placement,
> tone.

---

## Cross-System Effects

When modifying the pipeline, be aware of these connections:

| If You Change... | Also Check... |
|-----------------|---------------|
| Router intent categories | `INTENT_MODULES` mapping, `_COMMON_MODULES` |
| Model tier assignments | `config.py` settings, supervisor model selection |
| Fast path patterns | Router greeting detection (they should agree) |
| Output sanitization patterns | `_HUMANIZE_MAP` (tool name humanization) |
| Tone rejection patterns | `_TONE_REPLACEMENTS` (must have matching fix) |
| Humanize pool categories | `_FALLBACKS` dict (needs matching fallback) |
| Prompt module files | Router `INTENT_MODULES` (must load them) |
| SOUL.md personality | `_REPHRASER_PROMPT` (should match voice) |

---

## Router — Intent Classification Detail

**File:** `src/lucy/pipeline/router.py`

`classify_and_route()` runs in <1ms with zero LLM calls. It uses regex
patterns against the user message to determine intent and model tier.

### Intent Detection Priority

The router evaluates patterns in this order (first match wins):

```
1. Greeting patterns → intent="greeting", tier="fast"
2. Code keywords → intent="code", tier="code"
3. Heavy research keywords → intent="reasoning", tier="research"
4. Document keywords → intent="document", tier="document"
5. Data task keywords → intent="data", tier="default"
6. Monitoring keywords → intent="monitoring", tier="default"
7. Action verbs → intent="command", tier="default"
8. Data source keywords → intent="tool_use", tier="default"
9. Check/verify patterns → intent="lookup", tier="fast"
10. Simple questions → intent="chat", tier="fast"
11. Default → intent="chat", tier="default"
```

### Thread Depth Adjustments

- `thread_depth > 0` with `prev_had_tool_calls`: intent="followup",
  keeps same tier (continuing a workflow)
- `thread_depth > 3`: Bumps simple intents from `fast` to `default`
  (deeper threads usually need more context)

### Key Regex Patterns

| Pattern | Matches |
|---------|---------|
| `_CODE_KEYWORDS` | code, deploy, script, function, debug, refactor, implement, create app, dockerfile, ci/cd |
| `_RESEARCH_HEAVY` | deep dive, comprehensive, thorough, investigate, audit, benchmark, detailed analysis |
| `_RESEARCH_LIGHT` | research, analyze, compare, strategy, competitor, market, tell me about |
| `_DOCUMENT_KEYWORDS` | pdf, report, document, spreadsheet, excel, csv |
| `_DATA_TASK_KEYWORDS` | all users, export, all data, every record, complete list |
| `_MONITORING_KEYWORDS` | monitor, alert, notify when, track, watch for |
| `_ACTION_VERBS` | do, send, run, execute, delete, cancel, merge, deploy, schedule, create |
| `_GREETING_PATTERNS` | hi, hey, hello, thanks, ok, got it, sure, yes, no |
| `_SIMPLE_QUESTION` | What/when/where/who/how questions under 60 chars |

### Intent → Prompt Module Mapping

| Intent | Extra Modules Loaded |
|--------|---------------------|
| `chat`, `greeting`, `confirmation`, `followup` | None |
| `lookup`, `tool_use`, `monitoring` | None |
| `command` | `integrations.md` |
| `code` | `coding.md` |
| `reasoning` | `research.md` |
| `data`, `document` | `data_tasks.md` |

All non-chat intents also load the common modules: `tool_use.md` + `memory.md`.

---

## Fast Path — Instant Responses

**File:** `src/lucy/pipeline/fast_path.py`

For pure greetings, status checks, and help requests, skip the entire
agent loop and respond instantly from the humanize message pools.

### Evaluation

`evaluate_fast_path(message, thread_depth, has_thread_context)`
returns `FastPathResult(is_fast, response, reason)`.

### Disqualifiers

- Message length > 80 characters
- `has_thread_context = True` (user is in an active thread)
- `thread_depth > 0` (not a top-level message)

### Pattern Matching

| Pattern | Example | Pool Used |
|---------|---------|-----------|
| `_GREETING_RE` | "Hi Lucy!", "Hey there" | `pick("greeting")` |
| `_CONVERSATIONAL_GREETING_RE` | "How's it going?" | `pick("greeting")` |
| `_STATUS_RE` | "Are you online?", "Ping" | `pick("status")` |
| `_HELP_RE` | "What can you do?", "Who are you?" | `pick("help")` |

If no pattern matches, `FastPathResult(is_fast=False)` is returned and
the message proceeds to the full agent loop.

---

## Edge Cases Module

**File:** `src/lucy/pipeline/edge_cases.py`

Handles concurrency issues, interrupts, and graceful degradation.

### Thread Interrupt Handling

`decide_thread_interrupt(message, has_active_bg_task, thread_depth)`
determines what to do when a new message arrives during an active task:

| Condition | Decision |
|-----------|----------|
| Message is a status query | `status_reply` — respond with task status |
| Message is a cancellation | `cancel_task` — cancel the background task |
| `thread_depth == 0` (new thread) | `respond_independently` — handle separately |
| Background task active + same thread | `queue` — wait for task to finish |
| No background task | `respond_independently` |

### Task Status Queries

`is_status_query(message)` detects messages like:
- "What are you working on?"
- "Any update?"
- "Are you busy?"
- "How's it going with that?"

### Task Cancellation

`is_task_cancellation(message)` detects:
- "Cancel that", "Nevermind", "Don't bother"
- "Stop what you're doing", "Forget it"

### Tool Call Deduplication

`should_deduplicate_tool_call(tool_name, parameters, recent_calls, window_seconds=5.0)`
prevents duplicate mutating actions within a 5-second window.

| Tool Category | Examples | Dedup? |
|---------------|----------|--------|
| Idempotent | get, list, search, fetch, read | Never (safe to repeat) |
| Mutating | create, update, delete, send, deploy | Yes (within 5s window) |
| Unknown | Custom tools | Never (default safe) |

### Error Degradation

When errors occur, they're classified and mapped to user-friendly messages:

| Error Type | Source | User Message Pool |
|------------|--------|-------------------|
| `rate_limited` | 429 status, "rate limit" in error | `error_rate_limit` |
| `tool_timeout` | Timeout errors | `error_connection` |
| `service_unavailable` | 502/503/504, connection errors | `error_connection` |
| `context_overflow` | Token limit exceeded | `error_generic` |
| `unknown` | Everything else | `error_generic` |

---

## Output Pipeline — Four-Layer Detail

**File:** `src/lucy/pipeline/output.py`

### Layer 1: `_sanitize(text)` — Redact Internals

Removes any trace of internal tool names, file paths, and IDs:

| Pattern Type | Examples Redacted |
|-------------|-------------------|
| Tool names | `COMPOSIO_SEARCH_TOOLS`, `lucy_web_search`, `delegate_to_research_agent` |
| File paths | `/home/user/workspaces/`, `/workspace/skills/` |
| API keys | `sk-...`, `xoxb-...` (any 20+ char alphanumeric strings after "key"/"token") |
| UUIDs | `550e8400-e29b-41d4-a716-446655440000` |
| JSON fragments | `{"tool_name": ...}`, `{"status": ...}` |

**Humanize map** (`_HUMANIZE_MAP`): Some tool names are replaced with
human-readable phrases instead of being deleted:
- `COMPOSIO_SEARCH_TOOLS` → "search for tools"
- `COMPOSIO_MANAGE_CONNECTIONS` → "manage connections"

### Layer 2: `_convert_markdown_to_slack(text)` — Format Conversion

| Markdown | Slack mrkdwn |
|----------|-------------|
| `**bold**` | `*bold*` |
| `# Heading` | `*Heading*` |
| `## Heading` | `*Heading*` |
| `[text](url)` | `<url\|text>` |
| `| col1 | col2 |` | Bullet list (via `_table_to_bullets`) |

Tables are converted because Slack doesn't render markdown tables.
Each row becomes a bullet point with column headers as labels.

### Layer 3: `_validate_tone(text)` — Catch Robotic Patterns

Regex replacements for common chatbot phrases:

| Pattern | Replacement |
|---------|-------------|
| "I wasn't able to..." | "Let me try a different approach." |
| "Something went wrong..." | "Working on getting that sorted." |
| "I encountered an error..." | "Let me take another look at this." |
| "I apologize for..." | "Let me fix that." |

### Layer 4: `_deai(text)` — Remove AI Writing Tells

**Tier 1 (regex, always runs):** `_regex_deai(text)` applies instant fixes:

| Pattern | Action |
|---------|--------|
| Em dashes (—, –) | Replace with comma or remove |
| Power words | Remove: delve, tapestry, landscape, beacon, pivotal, leverage, etc. |
| Formal transitions | Remove: Moreover, Furthermore, Additionally, In conclusion |
| Chatbot closers | Remove: "Feel free to...", "Don't hesitate to...", "Happy to help!" |
| Sycophantic openers | Remove: "Great question!", "That's a wonderful idea!" |

**Tier 2 (LLM rewrite, DISABLED):** `_llm_deai_rewrite(text, tells)`
is disabled by setting `_LLM_REWRITE_THRESHOLD = 999`. When active,
it would send text through minimax-m2.5 to rewrite passages with high
AI-tell scores. Disabled because it destroyed Lucy's personality and
formatting (see Viktor UX audit).

**AI Tell Scoring:** `_ai_tell_score(text)` sums weighted scores:

| Category | Weight | Example |
|----------|--------|---------|
| `em_dash` | 1 | "The project — a major initiative — succeeded" |
| `power_word` | 1 | "delve into the landscape" |
| `formal_transition` | 2 | "Furthermore, it is worth noting" |
| `hedging` | 1 | "It's important to note that" |
| `sycophancy` | 2 | "Great question! I'd love to help" |
| `chatbot_closer` | 2 | "Feel free to reach out anytime" |
| `exclamation` | 1 | Multiple exclamation marks |

---

## Humanize Module — Message Pool System

**File:** `src/lucy/pipeline/humanize.py`

### Pool Architecture

At startup, `initialize_pools()` generates 6 variations per category
(18 categories x 6 = 108 messages) via a single batched LLM call.

**Cost:** ~$0.002 for all 108 messages (minimax-m2.5 at temp 0.9).

### Pool Categories

| Category | When Used |
|----------|-----------|
| `greeting` | Fast path greeting responses |
| `status` | Fast path status check responses |
| `help` | Fast path help request responses |
| `progress_early` | First progress message (turn 1-2) |
| `progress_mid` | Middle progress message (turn 4-6) |
| `progress_late` | Late progress message (turn 8+) |
| `progress_final` | Final "wrapping up" message |
| `task_cancelled` | When user cancels a task |
| `task_background_ack` | Acknowledging background task start |
| `error_rate_limit` | Rate limit errors |
| `error_connection` | Connection/timeout errors |
| `error_generic` | Unclassified errors |
| `error_task_failed` | Background task failure |
| `supervisor_replan` | Supervisor re-planning message |
| `supervisor_ask_user` | Supervisor asking user for input |
| `hitl_approved` | User approved a destructive action |
| `hitl_expired` | Approval expired (300s TTL) |
| `hitl_cancelled` | User cancelled a destructive action |

### `pick(category)` — Pool Selection

Zero-cost, instant message retrieval:

```
pick("progress_early")
    │
    ├── Pools ready?
    │     Yes → Random selection from 6 variations
    │     No  → Use hardcoded _FALLBACKS[category]
    │
    └── Supports format kwargs: pick("greeting", name="Ojash")
```

### `humanize(intent, context, task_hint, user_name)` — Dynamic Generation

For messages that need more context than pre-generated pools:

```
humanize("Working on competitor analysis", task_hint="competitor analysis")
    │
    ├── Build context block with task_hint + user_name
    ├── Call minimax-m2.5 (temp 0.9, max 120 tokens, timeout 2s)
    ├── If success → return LLM output
    └── If failure → return _humanize_fallback(intent)
```

### Fallback Messages

Every category has hardcoded fallbacks for when pools aren't ready
or the humanize LLM call fails. These are the absolute last resort.
