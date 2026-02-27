# Agent Orchestration — Deep Dive

> How Lucy processes a request from start to finish, including planning,
> tool execution, supervisor checkpoints, sub-agent delegation, and model
> escalation.

---

## Entry Point: `LucyAgent.run()`

**File:** `src/lucy/core/agent.py`

`run()` is the single entry point for all agent work — whether triggered by a
Slack message, a cron job, or a background task. It orchestrates the full
lifecycle:

### Parameters

| Parameter | Type | Purpose |
|-----------|------|---------|
| `message` | `str` | The user's message or cron instruction |
| `ctx` | `AgentContext` | Workspace, channel, thread, user info |
| `slack_client` | `Any \| None` | Slack Web API client (for posting updates) |
| `model_override` | `str \| None` | Force a specific model (bypasses router) |
| `failure_context` | `str \| None` | Injected on retry: what went wrong last time |
| `_retry_depth` | `int` | Internal retry counter (prevents infinite recursion) |

### AgentContext Dataclass

```python
@dataclass
class AgentContext:
    workspace_id: str
    channel_id: str | None = None
    thread_ts: str | None = None
    user_name: str | None = None
    user_slack_id: str | None = None
    team_id: str | None = None
    is_cron_execution: bool = False
```

### Execution Flow

```
run() called
  │
  ├── 1. classify_and_route(message) → ModelChoice
  │      Intent: chat|code|data|reasoning|monitoring|...
  │      Model tier: fast|default|code|research|frontier
  │      Prompt modules: [coding, data_tasks, ...]
  │
  ├── 2. ensure_workspace(workspace_id) → WorkspaceFS
  │      Creates directory structure if new workspace
  │
  ├── 3. Parallel fetch:
  │      ├── get_connected_app_names_reliable()
  │      └── composio_client.get_tools()
  │
  ├── 4. build_system_prompt(ws, connected_services, message, modules)
  │      Assembles: SOUL + SYSTEM_CORE + modules + skills + knowledge
  │
  ├── 5. Build conversation messages from Slack thread history
  │      Fetches via conversations.replies (max 100 messages)
  │      Keeps most recent MAX_CONTEXT_MESSAGES (40)
  │
  ├── 6. Pre-flight context injection:
  │      ├── Session memory → system message
  │      ├── History search (if references past conversations)
  │      └── Custom integration thread detection
  │
  ├── 7. Failure context injection (on retry):
  │      "Previous attempt failed: {failure_context}"
  │
  ├── 8. Supervisor: create_plan(message, tools, intent)
  │      Only for complex tasks (determined by _needs_plan())
  │      Generates step-by-step plan with success criteria
  │      Injected as <execution_plan> system message
  │
  ├── 9. _agent_loop() — Multi-turn LLM ↔ tool execution
  │      Wrapped in asyncio.wait_for(ABSOLUTE_MAX_SECONDS)
  │      Returns final response text
  │
  ├── 10. Quality gate: assess response completeness
  │       May escalate model and retry for better output
  │
  ├── 11. Verification gate: check data accuracy
  │       Retries with escalated model if verification fails
  │
  ├── 12. Post-response:
  │       ├── Persist memorable facts to memory
  │       ├── Log activity
  │       └── Write trace
  │
  └── Return response text
```

---

## The Agent Loop: `_agent_loop()`

This is where the real work happens. It's a `while True` loop that alternates
between calling the LLM and executing tool calls until the model returns a
text-only response (no tool calls).

### Parameters

| Parameter | Type | Purpose |
|-----------|------|---------|
| `system_prompt` | `str` | Complete system prompt |
| `messages` | `list[dict]` | Conversation history |
| `tools` | `list[dict] \| None` | Available tool schemas |
| `ctx` | `AgentContext` | Request context |
| `model` | `str` | Starting model |
| `trace` | `Trace` | Request trace |
| `route` | `ModelChoice` | Router classification |
| `slack_client` | `Any \| None` | For posting progress |
| `task_plan` | `TaskPlan \| None` | Supervisor plan |

### Loop Mechanics

```
Turn 0, 1, 2, ... (up to MAX_TOOL_TURNS = 50)
  │
  ├── Call LLM: client.chat_completion(messages, config)
  │     model = current model (may be escalated)
  │     tools = available tools (or None)
  │
  ├── Handle empty response:
  │     If content is empty and no tool calls:
  │       ├── First empty: nudge the model ("Please continue")
  │       └── Second empty: escalate to frontier model
  │
  ├── Detect narration:
  │     If model describes what it would do instead of calling tools:
  │       Inject: "Don't describe actions, call the tool directly"
  │
  ├── If text response (no tool calls) → break, return text
  │
  ├── Loop detection:
  │     Track tool call signatures (name + args hash)
  │     If same signature appears 3x → break with partial results
  │
  ├── Per-tool-name cap:
  │     If same tool called 4+ times → break
  │     Exception: search tools and Composio workbench tools
  │
  ├── Progress messages (posted to Slack):
  │     Turn 3: first progress update
  │     Every 5 turns after: subsequent updates
  │     Uses pick() from humanize pools for variety
  │     Includes task_hint (first 60 chars of user message)
  │
  ├── Execute tool calls in parallel:
  │     asyncio.gather(*[execute(call) for call in tool_calls])
  │     Each result capped at TOOL_RESULT_MAX_CHARS (16,000)
  │     Summarized if > TOOL_RESULT_SUMMARY_THRESHOLD (8,000)
  │
  ├── HITL check:
  │     If tool is destructive (delete, send, deploy, etc.):
  │       Post approval prompt → wait for user approve/cancel
  │
  ├── Append tool results to messages
  │
  ├── Payload trimming:
  │     If total chars > MAX_PAYLOAD_CHARS (120,000):
  │       Drop oldest tool results, keep system + recent turns
  │
  ├── Build TurnReport for supervisor
  │
  ├── Stuck detection:
  │     If 3+ consecutive tool errors:
  │       Inject intervention: "You seem stuck. Try a different approach."
  │       Escalate model if available
  │
  ├── Supervisor checkpoint:
  │     Runs every 3 turns OR every 60 seconds
  │     Calls evaluate_progress(plan, turn_reports, ...)
  │     Decision handling:
  │       CONTINUE  → no action, keep going
  │       INTERVENE → inject guidance message into conversation
  │       REPLAN    → generate new plan, inject it
  │       ESCALATE  → switch to stronger model tier
  │       ASK_USER  → post clarification question to Slack
  │       ABORT     → break loop, return partial results
  │
  ├── Mid-loop model escalation:
  │     If REMOTE_WORKBENCH or REMOTE_BASH called:
  │       Switch to code-tier model
  │     If 2+ lucy_edit_file calls:
  │       Switch to frontier model
  │     If 400 error from LLM:
  │       Switch to frontier model
  │
  └── Context window management:
        If messages > MAX_CONTEXT_MESSAGES (40):
          Trim oldest non-system messages
```

### Fallback: `_collect_partial_results()`

When the loop exits without a clean text response (empty final message,
timeout, or abort), this method scavenges useful information from the
conversation history:

- Counts total tool calls and errors
- Extracts the last tool that was called
- Identifies error hints from the most recent tool result
- Builds a human-readable status message

**Never exposes:** raw JSON, tool names, file paths, or internal data.

---

## The Supervisor System

**File:** `src/lucy/core/supervisor.py`

The supervisor is a lightweight, cheap LLM check that monitors the agent's
progress. It replaces rigid timeouts with intelligent evaluation.

### When It Runs

```python
def should_check(turn: int, last_check_time: float, elapsed_seconds: float) -> bool:
    if turn < 2: return False
    if time.monotonic() - last_check_time >= 60.0: return True
    if turn % 3 == 0: return True
    return False
```

### Planning: `create_plan()`

Called before the agent loop for complex tasks. Uses the cheapest model
(`MODEL_TIERS["fast"]`) to generate a plan.

**Input:**
- User message (first 300 chars)
- Available tool names (first 30)
- Classified intent

**Output:** `TaskPlan` with:
- `goal` — one-line summary of what needs to happen
- `steps` — numbered steps with descriptions and expected tools
- `success_criteria` — how to know the task is done

**Format in conversation:**
```
<execution_plan>
Goal: Pull SEO data from Google Search Console for January
1. Connect to GSC via COMPOSIO tools
2. Query performance data for date range
3. Format results as detailed report
Success: Complete report with all metrics delivered to user
</execution_plan>
```

**Skipped for simple tasks:** greetings, confirmations, lookups, followups.

### Progress Evaluation: `evaluate_progress()`

Called at supervisor checkpoints. Uses `MODEL_TIERS["fast"]`.

**Input provided to supervisor:**
- User request (first 150 chars)
- Intent classification
- Plan text (or "No plan")
- Turn count and elapsed seconds
- Recent actions (last 3 TurnReports)
- Total errors and consecutive error count
- Response text length so far
- Current model

**Decision output (single letter):**

| Letter | Decision | Effect |
|--------|----------|--------|
| `C` | CONTINUE | Keep going, agent is on track |
| `I` | INTERVENE | Inject guidance: "Try X instead" |
| `R` | REPLAN | Current plan is wrong, generate new one |
| `E` | ESCALATE | Switch to a stronger model |
| `A` | ASK_USER | Need user clarification, post question |
| `X` | ABORT | Task is impossible, stop gracefully |

### TurnReport Dataclass

```python
@dataclass
class TurnReport:
    turn: int
    tool_name: str
    tool_args_summary: str      # first 80 chars of args
    result_preview: str         # first 100 chars of result
    had_error: bool
    error_summary: str = ""
```

---

## Sub-Agent System

**File:** `src/lucy/core/sub_agents.py`

Sub-agents are isolated, specialized agent loops that run independently from
the main agent. They have their own tool sets, models, and turn limits.

### Registry

| Name | Prompt File | Model Tier | Max Turns | Tools |
|------|-------------|------------|-----------|-------|
| `research` | `sub_agents/research.md` | research | 12 | SEARCH_TOOLS, MULTI_EXECUTE, history_search |
| `code` | `sub_agents/code.md` | code | 10 | REMOTE_WORKBENCH, REMOTE_BASH, write_file, edit_file |
| `integrations` | `sub_agents/integrations.md` | default | 8 | SEARCH_TOOLS, MANAGE_CONNECTIONS, resolve_integration |
| `document` | `sub_agents/document.md` | document | 8 | REMOTE_WORKBENCH, REMOTE_BASH, write_file, generate_pdf/excel/csv |

### SubAgentSpec

```python
@dataclass
class SubAgentSpec:
    name: str
    system_prompt_file: str
    model: str
    tool_names: list[str] = field(default_factory=list)
    max_turns: int = 10
    max_tokens: int = 4096
    temperature: float = 0.4
```

### How Delegation Works

1. Main agent exposes tools like `delegate_to_research_agent`,
   `delegate_to_code_agent`, etc.
2. When the LLM calls one of these tools, the main agent:
   a. Extracts agent type and task description from arguments
   b. Looks up `SubAgentSpec` from `REGISTRY`
   c. Calls `run_subagent(task, spec, ...)`
3. Sub-agent runs its own isolated loop:
   - System prompt: `SOUL_LITE.md` + task-specific prompt
   - Separate tool list (subset of main agent's tools)
   - Max turns: as specified in spec
   - Payload limit: `SUB_MAX_PAYLOAD_CHARS` (80,000)
   - Timeout: `SUB_TIMEOUT_SECONDS` (120s)
4. Sub-agent returns final text result
5. Main agent receives result as a tool call result

### Progress Reporting

Sub-agents report progress every 3 turns via a callback that posts to
the original Slack thread.

### Safety Mechanisms

- **Loop detection:** 3x same tool call signature → break
- **Empty response recovery:** nudge to continue after turn 0
- **Payload trimming:** drop oldest tool results if >80k chars
- **Timeout:** hard 120-second safety net

---

## Background Tasks

**File:** `src/lucy/core/task_manager.py`

Some requests are too heavy for synchronous processing. These run as
background `asyncio.Task` objects.

### When a Task Goes Background

```python
def should_run_as_background_task(message: str, route_tier: str) -> bool:
    # Only frontier-tier tasks qualify
    # Must match compound heavy signals:
    # "comprehensive research", "deep dive", "competitive analysis", etc.
```

### Task Lifecycle

```
PENDING → ACKNOWLEDGED → WORKING → [progress updates] → COMPLETED/FAILED/CANCELLED
```

| State | Meaning |
|-------|---------|
| `PENDING` | Created, not yet started |
| `ACKNOWLEDGED` | Ack message posted to Slack |
| `WORKING` | Agent loop running |
| `COMPLETED` | Final result posted |
| `FAILED` | Error occurred, user notified |
| `CANCELLED` | User cancelled via "cancel that" |

### Limits

- **Per workspace:** max 5 concurrent background tasks
- **Duration:** 14,400 seconds (4 hours) safety net
- **Cleanup:** completed tasks auto-pruned (keeps last 20)

### User Interaction During Background Tasks

Users can:
- **Check status:** "What are you working on?" → `format_task_status()`
- **Cancel:** "Cancel that" / "nevermind" → `handle_task_cancellation()`
- **Send new messages:** processed independently (thread lock prevents conflicts)

---

## Model Escalation Logic

Model escalation is Lucy's automatic quality improvement mechanism. When
initial models fail or produce poor results, the system moves to stronger
models.

### Escalation Triggers

| Trigger | Source | Action |
|---------|--------|--------|
| Empty LLM response (2x) | `_agent_loop` | Switch to frontier |
| 3+ consecutive tool errors | `_agent_loop` stuck detection | Switch to stronger tier |
| Supervisor `ESCALATE` decision | `supervisor.evaluate_progress` | Switch to frontier |
| Code tools called mid-loop | `_agent_loop` model upgrade | Switch to code tier |
| 2+ `lucy_edit_file` calls | `_agent_loop` fail-up | Switch to frontier |
| 400 error from LLM API | `_agent_loop` error recovery | Switch to frontier |
| Quality gate failure | `run()` quality check | Retry with frontier |
| Verification gate failure | `run()` verification | Retry with escalated model |

### Escalation Never Downgrades

Once a model is escalated in a session, it stays at the higher tier for the
remainder of that request. This prevents oscillation.

---

## OpenClaw Client

**File:** `src/lucy/core/openclaw.py`

Despite the name, `OpenClawClient` is actually an **OpenRouter** client that
handles all LLM inference.

### Key Methods

| Method | Purpose |
|--------|---------|
| `chat_completion()` | Core LLM call with retry + rate limiting |
| `_parse_tool_calls()` | Converts OpenAI tool call format to internal format |
| `load_soul()` | Loads `prompts/SOUL.md` personality prompt |

### Response Caching

Short, deterministic calls (no tools, single user message <200 chars) are
cached with a 5-minute TTL. Cache key is `"{model}:{system_hash}:{content}"`.
Max 500 cache entries.

### Rate Limiting Integration

Before each LLM call, acquires a token from the rate limiter:
```python
await get_rate_limiter().acquire_model(model, timeout=30.0)
```

If rate limited, raises `OpenClawError` with `status_code=429`.

### Retry Logic

Retries on status codes: `429, 500, 502, 503, 504` with exponential backoff.

### ChatConfig

```python
@dataclass
class ChatConfig:
    model: str = ""
    temperature: float = 0.7
    max_tokens: int = 4096
    system_prompt: str | None = None
    tools: list[dict] | None = None
```

---

## Trace System

**File:** `src/lucy/infra/trace.py`

Every request gets a `Trace` object that records timing, model usage, and
tool calls.

### What's Recorded

| Field | Content |
|-------|---------|
| `trace_id` | 12-char hex identifier |
| `model_used` | Final model used |
| `intent` | Router classification |
| `tool_calls_made` | List of tool names called |
| `user_message` | First 500 chars |
| `response_text` | First 500 chars |
| `usage` | Token counts (prompt, completion, total) |
| `spans` | Timed sections with metadata |

### Span Example

```python
async with trace.span("llm_call", model="gemini-2.5-flash"):
    response = await client.chat_completion(...)
```

### Output

Traces are:
1. Logged as a structured `request_trace` event via `structlog`
2. Written as JSONL to `{workspace}/logs/threads/{thread_ts}.jsonl`

---

## Tool Dispatch

**File:** `src/lucy/core/agent.py` — `_execute_tool()`

When the LLM returns tool calls, each is routed through a three-way
dispatch:

```
_execute_tool(tool_name, parameters, workspace_id, ctx)
    │
    ├── tool_name starts with "lucy_"?
    │     → _execute_internal_tool()
    │       Routes to: file_generator, web_search, spaces, email_tools,
    │       history_search, cron CRUD, heartbeat CRUD,
    │       custom wrapper tools (lucy_custom_*),
    │       resolve_custom_integration, store_api_key
    │
    ├── tool_name starts with "delegate_to_" and ends with "_agent"?
    │     → _handle_delegation()
    │       Extracts agent type (research/code/integrations/document)
    │       Looks up SubAgentSpec from REGISTRY
    │       Runs isolated sub-agent loop with 120s timeout
    │
    └── Everything else?
          → composio_client.execute_tool_call()
            Routes to Composio SDK for external tool execution
            (Google Calendar, GitHub, Linear, Gmail, etc.)
```

**Parallel execution:** `_execute_tools_parallel()` runs all tool calls
from a single LLM turn concurrently via `asyncio.gather()`.

---

## Quality Gate

**File:** `src/lucy/core/agent.py` — `_assess_response_quality()`

A zero-cost heuristic check (no LLM call) that runs after the agent loop
completes. Assigns a confidence score from 1-10.

### What It Checks

| Check | Detected Pattern | Effect |
|-------|-----------------|--------|
| Service name confusion | Response mentions services the user didn't ask about | -3 confidence |
| "I can't find" responses | Agent claims inability when user expects action | -2 confidence |
| Very short response | Complex question, response < 100 chars | -2 confidence |
| Suggesting unrelated services | Recommending tools user didn't request | -2 confidence |

### Escalation

If `confidence <= 6` and issues exist, the response is escalated:

```python
_escalate_response(user_message, original_response, issues, ctx)
```

Uses `MODEL_TIERS["frontier"]` to re-check and correct the response.
Only runs for non-frontier models (frontier responses are trusted).

---

## Verification Gate

**File:** `src/lucy/core/agent.py` — `_verify_output()`

Another zero-cost heuristic check that catches completeness failures.

### What It Checks

| Check | Detected Pattern |
|-------|-----------------|
| Sample data | User asked for "all data" but response suggests only samples |
| Multi-part requests | User asked for multiple deliverables (e.g., "excel + email + summary") but not all are present |
| Short data responses | Data-intent tasks with response < 200 chars |
| Degradation phrases | Response contains failure/apology patterns |
| Complex request, short output | Message > 50 words but response < 150 chars |

### Retry

If verification fails, the agent retries with the next-higher model tier,
injecting the verification issues as failure context. Max retry depth: 1.

---

## Planning Decision: `_needs_plan()`

**File:** `src/lucy/core/supervisor.py`

Determines whether a task warrants a supervisor-generated plan.

```python
_SIMPLE_INTENTS = {"greeting", "fast", "follow_up", "status"}
_COMPLEX_INTENTS = {"data", "document", "code", "code_reasoning",
                    "tool_use", "research", "monitoring"}

def _needs_plan(intent, message):
    if intent in _SIMPLE_INTENTS:
        return False                  # Never plan for simple intents
    if intent in _COMPLEX_INTENTS:
        if len(message.split()) < 8:
            return False              # Short messages don't need plans
        return True                   # Complex intent + substantial message
    return len(message.split()) > 15  # Unknown intent: plan if verbose
```

---

## Custom Integration Detection

**File:** `src/lucy/core/agent.py` — `_detect_custom_integration_context()`

When a thread contains evidence of a custom integration workflow, the agent
injects a system nudge to continue the process.

### Detection Patterns

**Offer phrases** (Lucy offered to build a custom integration):
- "custom connection", "custom integration", "build a custom",
  "try to build", "i can try to build"

**User consent phrases:**
- "yes", "go ahead", "please build", "build it", "do it",
  "sure", "let's do it", "try it", "give it a shot", "go for it"

**API key patterns** (regex):
- `api[_ ]?key|token|secret|credential` followed by value
- Any 20+ char alphanumeric string (likely a key)

When both an offer phrase and consent/API key are detected in the thread,
a system message nudges the agent to proceed with the integration workflow.

---

## Thread History Fetching

The agent fetches thread history from Slack in two places:

### 1. Initial Classification (limit: 50)

```
conversations.replies(channel, ts, limit=50)
    → Extract thread_depth
    → Check if previous messages had tool calls
    → Determine if confirmation vs new request
```

### 2. Building LLM Messages (limit: 20)

```
conversations.replies(channel, ts, limit=20)
    → Convert each message to LLM format:
        Bot messages → role: "assistant"
        User messages → role: "user"
    → Keep most recent MAX_CONTEXT_MESSAGES (40)
    → Trim oldest non-system messages if exceeded
```

---

## Tool Registration

During `agent.run()`, tools are assembled from multiple sources. Some are
conditional based on configuration:

| Source | Tools | Condition |
|--------|-------|-----------|
| Composio meta-tools | 5 tools (SEARCH, MANAGE, MULTI_EXECUTE, WORKBENCH, BASH) | Always |
| History tools | `lucy_search_slack_history`, `lucy_get_channel_history`, `lucy_list_slack_channels` | Always |
| File tools | `lucy_write_file`, `lucy_edit_file`, `lucy_store_api_key`, `lucy_generate_pdf/excel/csv` | Always |
| Web search | `lucy_web_search` | Always |
| Email tools | `lucy_send_email`, `lucy_read_emails`, `lucy_reply_to_email`, `lucy_search_emails`, `lucy_get_email_thread` | `agentmail_enabled and agentmail_api_key` |
| Spaces tools | `lucy_spaces_init/deploy/list/status/delete` | `spaces_enabled` |
| Cron tools | `lucy_create_cron`, `lucy_delete_cron`, `lucy_modify_cron`, `lucy_list_crons`, `lucy_trigger_cron` | Always |
| Heartbeat tools | `lucy_create_heartbeat`, `lucy_delete_heartbeat`, `lucy_list_heartbeats` | Always |
| Custom wrappers | `lucy_custom_{slug}_{tool}` | Discovered from `custom_wrappers/` |
| Integration tools | `lucy_resolve_custom_integration`, `lucy_delete_custom_integration` | Always |
| Delegation tools | `delegate_to_research/code/integrations/document_agent` | Always |

**Total:** ~35+ tools available per request.

---

## Supervisor System — Deep Dive

**File:** `src/lucy/core/supervisor.py`

The supervisor is a lightweight checkpoint that monitors agent progress
without imposing hard timeouts. It runs every 3 turns or 60 seconds
(whichever comes first, minimum turn 2).

### Planning Phase

`_needs_plan(intent, message)` decides whether to create a plan:

| Intent | Condition | Plan? |
|--------|-----------|-------|
| `greeting`, `fast`, `follow_up`, `status` | Always | No |
| `data`, `document`, `code`, `research`, `monitoring` | Message >= 8 words | Yes |
| Other | Message > 15 words | Yes |

`create_plan(user_message, available_tools, intent)` uses the `fast`
tier model (cheapest) to generate a 2-6 step plan:

```
GOAL: Create a multi-sheet Excel with Clerk + Polar.sh user data
1. Fetch all users from Clerk API [tool: COMPOSIO_EXECUTE_TOOL]
2. Fetch subscriber data from Polar.sh [tool: lucy_custom_polarsh_list_products]
3. Process and merge the datasets [tool: lucy_run_script]
4. Generate formatted Excel with summary sheet [tool: lucy_generate_excel]
SUCCESS: Excel file with raw data sheets + summary sheet uploaded to thread
```

### Checkpoint Evaluation

`evaluate_progress(plan, turn_reports, ...)` analyzes:

1. **Recent turns:** Last 3 tool calls and their results
2. **Error patterns:** Total errors and consecutive errors
3. **Plan adherence:** Whether tools match expected tools in plan
4. **Time elapsed:** How long the agent has been running

### Supervisor Decisions

| Decision | Meaning | Agent Action |
|----------|---------|-------------|
| `CONTINUE` | On track, keep going | No change |
| `INTERVENE` | Slightly off track | Inject guidance as system message |
| `REPLAN` | Plan is wrong, need new approach | Generate new plan, reset |
| `ESCALATE` | Current model struggling | Upgrade to next model tier |
| `ASK_USER` | Ambiguous or needs confirmation | Post question in Slack, pause |
| `ABORT` | Unrecoverable situation | Stop gracefully with explanation |

### Model Escalation Order

When the supervisor or agent detects struggles:

```
fast → default → code → research → frontier
```

Each escalation moves to the next tier. The frontier model
(`google/gemini-3.1-pro-preview`) is the last resort.

### Escalation Triggers

Multiple paths can trigger model escalation:

| Trigger | Location | Condition |
|---------|----------|-----------|
| 400 error recovery | `_agent_loop` turn > 0 | API returns 400 status |
| Empty response | `_agent_loop` turn > 0 | No tool calls and no text (2 retries) |
| Stuck state | `_detect_stuck_state()` | Same tool called 3+ times with same args |
| Supervisor ESCALATE | Checkpoint evaluation | Supervisor decides model is struggling |
| Edit failure | `_agent_loop` | 2+ failed `lucy_edit_file` calls |
| Code execution | `_agent_loop` | Remote code execution detected, upgrade to code tier |

---

## Edge Cases in the Agent Loop

Edge cases (thread interrupts, tool call deduplication, error degradation)
are handled by `src/lucy/pipeline/edge_cases.py` and evaluated before the
agent loop starts. See
[MESSAGE_PIPELINE.md > Edge Cases Module](./MESSAGE_PIPELINE.md#edge-cases-module)
for the full reference with decision tables and classification rules.

---

## Context-Aware Error Messages

**File:** `src/lucy/core/agent.py` — `_collect_partial_results()`

When the agent fails to produce a response, this method constructs
a context-aware error message instead of generic fallbacks.

### Tool Name Humanization

`_TOOL_HUMAN_NAMES` maps internal names to user-friendly descriptions:

| Internal Name | User Sees |
|--------------|-----------|
| `lucy_spaces_init` | "setting up the project" |
| `lucy_spaces_deploy` | "deploying your app" |
| `lucy_generate_excel` | "creating a spreadsheet" |
| `lucy_send_email` | "sending an email" |
| `lucy_create_heartbeat` | "setting up a monitor" |
| `lucy_create_cron` | "creating a scheduled task" |
| `lucy_custom_{slug}_{action}` | "pulling data from {Slug}" (auto-extracted) |

### Error Classification

Scans tool result content for error hints:

| Keyword in Result | Error Type |
|-------------------|-----------|
| `timeout`, `timed out` | "a timeout" |
| `rate limit`, `429` | "a rate limit" |
| `connection`, `ECONNREFUSED` | "a connection issue" |
| `permission`, `forbidden`, `403` | "a permissions issue" |
| `not found`, `404` | "a not-found error" |

### Message Construction

```python
# With context:
"I was pulling data from Polar but hit a connection issue. Let me try a different approach."

# Without context:
"I ran into a hiccup while processing your request. Let me try a different approach."

# Partial success:
"Here's what I found so far: {summary}\n\nI'm still piecing together the full picture."
```

---

## Safety Nets

### ABSOLUTE_MAX_SECONDS = 14,400 (4 hours)

The catastrophic safety net. Wraps the entire agent loop in
`asyncio.wait_for()`. If the agent hasn't finished in 4 hours,
it's force-stopped with a user-friendly message.

This replaced the old 5-minute timeout that caused most of Lucy's
"Sorry, this took longer than expected" errors.

### Soft Limits

| Limit | Value | What Happens |
|-------|-------|-------------|
| `MAX_TOOL_TURNS` | 50 | Supervisor takes over governance |
| `MAX_CONTEXT_MESSAGES` | 40 | Older messages trimmed from context |
| `MAX_PAYLOAD_CHARS` | 120,000 | Tool results truncated |
| `TOOL_RESULT_MAX_CHARS` | 16,000 | Individual tool result capped |
| `TOOL_RESULT_SUMMARY_THRESHOLD` | 8,000 | Results > 8K get summarized |

### Loop Detection

| Pattern | Threshold | Action |
|---------|-----------|--------|
| Identical tool call signatures | 3 repeats | Force stop |
| Same tool called many times | 4 calls → warning, 6 → stop | Escalate model, then stop |
| Empty LLM responses | 2 retries | Escalate to frontier, then use partial results |
