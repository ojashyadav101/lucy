# Lucy Quick Reference

> **Read this first in every new session.** Fast lookup for how things
> work, where to find them, and how to change them.

---

## 10 Facts You Must Know

1. **LLM calls go through OpenRouter**, not OpenClaw. `OpenClawClient`
   in `core/openclaw.py` is a misnomer — it's an `httpx` client to
   `openrouter.ai`. See [OPENCLAW_BOUNDARY.md](./OPENCLAW_BOUNDARY.md).

2. **6 model tiers** route requests: fast, default, code, research,
   document, frontier. Defined in `config.py:42-47`, selected by
   `pipeline/router.py:classify_and_route()`. Escalation order:
   `fast → default → code → research → frontier`.

3. **No hard timeouts** except `ABSOLUTE_MAX_SECONDS = 14400` (4h) in
   `core/agent.py:44`. The **supervisor** (`core/supervisor.py`) checks
   every 3 turns or 60s and decides: CONTINUE / INTERVENE / REPLAN /
   ESCALATE / ASK_USER / ABORT.

4. **Tool routing:** `lucy_*` tools → internal handlers. `delegate_to_*`
   → sub-agents. Everything else → Composio `execute_tool_call()`.
   Defined in `core/agent.py:_execute_tool()` (line ~2002).

5. **Output pipeline** (4 layers, `pipeline/output.py`): sanitize →
   markdown-to-slack → validate tone → de-AI regex. The LLM rewrite
   layer is **disabled** (`_LLM_REWRITE_THRESHOLD = 999`).

6. **Composio** provides 5 meta-tools. Lucy's agent sees them as
   `COMPOSIO_SEARCH_TOOLS`, `COMPOSIO_MANAGE_CONNECTIONS`, etc.
   Client: `integrations/composio_client.py`.

7. **Workspace memory** is filesystem-based (`workspace/filesystem.py`).
   Each Slack team gets a directory under `settings.workspace_root`.
   Skills are `SKILL.md` files with YAML frontmatter.

8. **Heartbeats** are Lucy's **native** real-time monitors (not from
   OpenClaw). Defined in `crons/heartbeat.py`. Crons use APScheduler
   in `crons/scheduler.py`.

9. **Slack messages** flow: event → middleware (resolve workspace/user/
   channel) → `handlers.py:_handle_message()` → router → agent loop →
   output pipeline → Block Kit → Slack API.

10. **OpenClaw Gateway** is only used for VPS operations: exec commands,
    background processes, MCP server installs. Client:
    `integrations/openclaw_gateway.py`.

---

## "How Does X Work?"

| Question | Answer Location |
|----------|----------------|
| How does a message flow through Lucy? | [ARCHITECTURE.md](./ARCHITECTURE.md) > "How a Message Flows" |
| How does the agent loop work? | [AGENT_LOOP.md](./AGENT_LOOP.md) > entire doc |
| How does the supervisor monitor progress? | [AGENT_LOOP.md](./AGENT_LOOP.md) > "Supervisor System" |
| How does model escalation work? | [AGENT_LOOP.md](./AGENT_LOOP.md) > "Escalation Triggers" |
| How does intent classification work? | [MESSAGE_PIPELINE.md](./MESSAGE_PIPELINE.md) > "Router" |
| How does the output pipeline process text? | [MESSAGE_PIPELINE.md](./MESSAGE_PIPELINE.md) > "Output Pipeline" |
| How does the humanize pool system work? | [MESSAGE_PIPELINE.md](./MESSAGE_PIPELINE.md) > "Humanize Module" |
| How do fast-path responses work? | [MESSAGE_PIPELINE.md](./MESSAGE_PIPELINE.md) > "Fast Path" |
| How do Slack reactions/emojis work? | [SLACK_LAYER.md](./SLACK_LAYER.md) > "Reaction System" |
| How does Block Kit conversion work? | [SLACK_LAYER.md](./SLACK_LAYER.md) > "Block Kit Conversion" |
| How does HITL approval work? | [SLACK_LAYER.md](./SLACK_LAYER.md) > "Human-in-the-Loop" |
| How does workspace onboarding work? | [WORKSPACE_MEMORY.md](./WORKSPACE_MEMORY.md) > "Onboarding Flow" |
| How does three-tier memory work? | [WORKSPACE_MEMORY.md](./WORKSPACE_MEMORY.md) > "Three-Tier Memory" |
| How do skills get loaded into prompts? | [WORKSPACE_MEMORY.md](./WORKSPACE_MEMORY.md) > "Skills System" |
| How do cron jobs execute? | [CRONS_HEARTBEAT.md](./CRONS_HEARTBEAT.md) > cron section |
| How do heartbeat monitors work? | [CRONS_HEARTBEAT.md](./CRONS_HEARTBEAT.md) > heartbeat section |
| How does Composio tool execution work? | [TOOLS_INTEGRATIONS.md](./TOOLS_INTEGRATIONS.md) > "Composio Client" |
| How does the integration resolver work? | [TOOLS_INTEGRATIONS.md](./TOOLS_INTEGRATIONS.md) > "Resolver Pipeline" |
| How does rate limiting work? | [INFRASTRUCTURE.md](./INFRASTRUCTURE.md) > "Rate Limiting" |
| How does the request queue work? | [INFRASTRUCTURE.md](./INFRASTRUCTURE.md) > "Request Queue" |
| How does request tracing work? | [INFRASTRUCTURE.md](./INFRASTRUCTURE.md) > "Request Tracing" |
| What does SOUL.md control? | [PROMPTS_REFERENCE.md](./PROMPTS_REFERENCE.md) > SOUL.md section |
| How do sub-agents work? | [AGENT_LOOP.md](./AGENT_LOOP.md) > "Sub-Agent System" |
| What's OpenClaw vs what's native? | [OPENCLAW_BOUNDARY.md](./OPENCLAW_BOUNDARY.md) |
| How does error handling work? | [AGENT_LOOP.md](./AGENT_LOOP.md) > "Context-Aware Error Messages" |
| How does tool call dedup work? | [MESSAGE_PIPELINE.md](./MESSAGE_PIPELINE.md) > "Edge Cases Module" |
| How does thread interrupt handling work? | [MESSAGE_PIPELINE.md](./MESSAGE_PIPELINE.md) > "Edge Cases Module" |

---

## "I Want to Change X"

| Change | Files to Edit |
|--------|--------------|
| Change which model a tier uses | `src/lucy/config.py:42-47` (or set `LUCY_MODEL_TIER_*` env var) |
| Change intent classification | `src/lucy/pipeline/router.py` — regex patterns + `classify_and_route()` |
| Change output post-processing | `src/lucy/pipeline/output.py` — `_REGEX_DEAI_PATTERNS`, `_TONE_REPLACEMENTS` |
| Change Lucy's personality/voice | `prompts/SOUL.md` |
| Change system prompt structure | `src/lucy/pipeline/prompt.py` — `build_system_prompt()` |
| Add a new internal tool | See "How To" section below |
| Add a prompt module | Create file in `prompts/modules/`, add to `INTENT_MODULES` in `pipeline/router.py` |
| Change progress messages | `src/lucy/pipeline/humanize.py` — `POOL_CATEGORIES`, `_FALLBACKS` |
| Change error messages | `src/lucy/core/agent.py` — `_TOOL_HUMAN_NAMES`, `_collect_partial_results()` |
| Change supervisor behavior | `src/lucy/core/supervisor.py` — `evaluate_progress()`, `_needs_plan()` |
| Change supervisor check frequency | `src/lucy/core/supervisor.py:30-31` — `SUPERVISOR_CHECK_INTERVAL_*` |
| Change rate limits | `src/lucy/infra/rate_limiter.py` — `_MODEL_LIMITS`, `_API_LIMITS` |
| Change request queue workers | `src/lucy/infra/request_queue.py:86` — `NUM_WORKERS` |
| Add a new cron template | Create YAML in `workspace_seeds/crons/`, it auto-loads on onboarding |
| Add a new heartbeat evaluator | `src/lucy/crons/heartbeat.py` — add `_eval_*` function + type |
| Add a new workspace skill | Create `SKILL.md` in `workspace_seeds/skills/` |
| Change Slack reaction rules | `src/lucy/slack/reactions.py` — `_REACTION_RULES` |
| Change destructive action list | `src/lucy/slack/hitl.py` — `DESTRUCTIVE_ACTION_PATTERNS` |
| Change Block Kit thresholds | `src/lucy/slack/blockkit.py:14` — `MIN_BLOCKS_THRESHOLD` |
| Change message split length | `src/lucy/slack/rich_output.py` — `MAX_SINGLE_MESSAGE_CHARS` |

---

## "Where Is X Defined?"

| Constant / Config | File : Line | Value |
|-------------------|------------|-------|
| `ABSOLUTE_MAX_SECONDS` | `core/agent.py:44` | `14400` (4 hours) |
| `MAX_TOOL_TURNS` | `core/agent.py:39` | `50` |
| `MAX_CONTEXT_MESSAGES` | `core/agent.py:41` | `40` |
| `TOOL_RESULT_MAX_CHARS` | `core/agent.py:42` | `16000` |
| `MAX_PAYLOAD_CHARS` | `core/agent.py:45` | `120000` |
| `SUPERVISOR_CHECK_INTERVAL_TURNS` | `core/supervisor.py:30` | `3` |
| `SUPERVISOR_CHECK_INTERVAL_SECONDS` | `core/supervisor.py:31` | `60.0` |
| `_LLM_REWRITE_THRESHOLD` | `pipeline/output.py:423` | `999` (disabled) |
| `MIN_BLOCKS_THRESHOLD` | `slack/blockkit.py:14` | `80` chars |
| `MAX_SINGLE_MESSAGE_CHARS` | `slack/rich_output.py` | `3000` |
| `PENDING_TTL_SECONDS` | `slack/hitl.py:21` | `300` (5 min) |
| `_MAX_INJECTED_SKILLS` | `workspace/skills.py:82` | `3` |
| `MAX_QUEUE_DEPTH_PER_WORKSPACE` | `infra/request_queue.py:84` | `50` |
| `MAX_TOTAL_QUEUE_DEPTH` | `infra/request_queue.py:85` | `200` |
| `NUM_WORKERS` | `infra/request_queue.py:86` | `10` |
| `SYNC_LIMIT_PER_CHANNEL` | `workspace/slack_sync.py` | `100` |
| `MAX_RESULTS` (history search) | `workspace/history_search.py` | `30` |

---

## "Something Broke" — Debugging

| Symptom | Root Cause | Fix |
|---------|-----------|-----|
| "Sorry, this took longer than expected" | Old hardcoded timeout message in `handlers.py` or supervisor ABORT | Check `handlers.py:_handle_message()` exception handler; check supervisor logs |
| Agent returns empty/generic response | LLM returned no tool calls and no text | Check `_collect_partial_results()` in `agent.py`; check model escalation fired |
| Agent repeats same tool call | Stuck state detection not triggering | Check `_detect_stuck_state()` in `agent.py`; check loop detection thresholds |
| Progress messages feel generic | `humanize.py` pool messages lack context | Check if `task_hint` is being passed to `_describe_progress()` |
| Cron didn't fire | Scheduler not running or cron not loaded | Check `app.py:lifespan()` starts scheduler; check workspace `crons/` directory |
| Heartbeat not alerting | Wrong `alert_channel_id` or evaluator error | Check `heartbeat.py:evaluate_due_heartbeats()`; check channel ID format |
| Tool result truncated | Result exceeded `TOOL_RESULT_MAX_CHARS` | Increase threshold or check `TOOL_RESULT_SUMMARY_THRESHOLD` |
| Composio tool "not found" | Toolkit name mismatch | Check auto-repair in `composio_client.py:execute_tool_call()` |
| Rate limited | Token bucket depleted | Check `rate_limiter.py:_MODEL_LIMITS`; increase rate or burst |
| Formatting broken in Slack | Output pipeline mangled the text | Check each layer in `output.py:process_output()`; check `text_to_blocks()` |
| Personality feels flat | De-AI regex removing too much | Check `_REGEX_DEAI_PATTERNS` in `output.py`; verify `_LLM_REWRITE_THRESHOLD = 999` |
| Agent not using right model | Router classified intent wrong | Check `router.py:classify_and_route()`; add keywords to intent patterns |
| Background task stuck | No supervisor governance | Check `task_manager.py:MAX_TASK_DURATION`; check supervisor checkpoints |

---

## How To: Add a New Internal Tool

1. **Create the tool function** in the appropriate `src/lucy/tools/` module
   (or create a new module).

2. **Create tool definitions** — return OpenAI function-calling schema:
   ```python
   def get_my_tool_definitions() -> list[dict[str, Any]]:
       return [{"type": "function", "function": {"name": "lucy_my_tool", ...}}]
   ```

3. **Export** from `src/lucy/tools/__init__.py` — add to `__all__`.

4. **Register in agent** — in `src/lucy/core/agent.py`, the `run()` method
   builds the tool list. Add your `get_my_tool_definitions()` call.

5. **Add execution handler** — in `agent.py:_execute_internal_tool()`,
   add a branch for your tool name.

6. **Add human name** — in `_TOOL_HUMAN_NAMES` (agent.py:1660) for
   context-aware error messages.

7. **Check HITL** — if the tool is destructive, add keywords to
   `DESTRUCTIVE_ACTION_PATTERNS` in `slack/hitl.py`.

8. **Add to output sanitizer** — if the tool name should be hidden from
   users, add to `_REDACT_PATTERNS` in `pipeline/output.py`.

## How To: Add a New Model Tier

1. **Add setting** in `src/lucy/config.py:Settings` class.
2. **Add to MODEL_TIERS** in `src/lucy/pipeline/router.py:23-30`.
3. **Add to escalation order** in `src/lucy/core/supervisor.py` and
   `src/lucy/core/agent.py` (search for the escalation list
   `["fast", "default", "code", "research", "frontier"]`).
4. **Update router** — add intent pattern mapping in `classify_and_route()`.

## How To: Debug a Failed Agent Run

1. **Check thread trace log**: `workspaces/{id}/logs/threads/{thread_ts}.jsonl`
   — contains per-turn trace data with model, tools, timing.
2. **Check activity log**: `workspaces/{id}/logs/activity/{YYYY-MM-DD}.md`
   — daily activity entries.
3. **Check structlog output** — Lucy logs every tool call, model selection,
   supervisor decision, and error with structured JSON.
4. **Reproduce** — look at the intent classification (`router.py`) and
   model tier selected. Was it the right model? Was the plan reasonable?
5. **Check supervisor decisions** — search logs for `supervisor_decision`
   events. Did it ESCALATE when it should have? Did it ABORT too early?

## How To: Add a New Cron Template

1. **Create YAML** in `workspace_seeds/crons/`:
   ```yaml
   name: my_report
   schedule: "0 9 * * *"
   instruction: "Generate a daily report of..."
   channel: "general"
   enabled: true
   ```
2. **It auto-loads** during workspace onboarding (`workspace/onboarding.py`).
3. For existing workspaces, manually add to `workspaces/{id}/crons/`.

## How To: Add a New Heartbeat Evaluator Type

1. **Add evaluator function** in `src/lucy/crons/heartbeat.py`:
   ```python
   async def _eval_my_type(heartbeat: Heartbeat) -> tuple[bool, str]:
       # Return (is_ok, description)
   ```
2. **Register type** in the evaluator dispatch (search for `_eval_api_health`,
   `_eval_page_content`, etc. and add your type to the match).
3. **Update prompt** — add the new type to `prompts/modules/tool_use.md`
   so the agent knows it can create heartbeats of this type.

---

## Key File Map

```
src/lucy/
├── app.py                          # FastAPI + Slack Bolt entry point
├── config.py                       # All settings (LUCY_* env vars)
├── core/
│   ├── agent.py                    # Main agent loop, tool dispatch, error handling
│   ├── openclaw.py                 # LLM client (actually OpenRouter, NOT OpenClaw)
│   ├── supervisor.py               # Planning + progress monitoring
│   ├── sub_agents.py               # Sub-agent delegation (research, code, etc.)
│   └── task_manager.py             # Background task lifecycle
├── pipeline/
│   ├── router.py                   # Intent classification + model selection
│   ├── fast_path.py                # Instant responses for greetings/status
│   ├── prompt.py                   # System prompt builder
│   ├── output.py                   # 4-layer output post-processing
│   ├── humanize.py                 # Message pool system
│   └── edge_cases.py               # Interrupts, dedup, degradation
├── slack/
│   ├── handlers.py                 # Slack event handling
│   ├── middleware.py               # Workspace/user/channel resolution
│   ├── blockkit.py                 # Text → Block Kit conversion
│   ├── rich_output.py              # Emoji enhancement, link formatting, splitting
│   ├── reactions.py                # Reaction emoji selection
│   └── hitl.py                     # Human-in-the-loop approvals
├── workspace/
│   ├── filesystem.py               # WorkspaceFS (per-tenant file operations)
│   ├── memory.py                   # Three-tier memory (thread/session/knowledge)
│   ├── skills.py                   # SKILL.md parsing and injection
│   ├── onboarding.py               # New workspace setup
│   ├── slack_sync.py               # Slack message syncing
│   ├── history_search.py           # Grep-based Slack history search
│   └── timezone.py                 # User timezone resolution
├── crons/
│   ├── scheduler.py                # APScheduler cron engine
│   └── heartbeat.py                # Real-time condition monitors
├── tools/
│   ├── email_tools.py              # lucy_send_email
│   ├── file_generator.py           # lucy_generate_pdf/excel/csv
│   ├── spaces.py                   # lucy_spaces_init/deploy
│   └── web_search.py               # lucy_web_search
├── integrations/
│   ├── composio_client.py          # Composio SDK wrapper (5 meta-tools)
│   ├── openclaw_gateway.py         # OpenClaw Gateway client (VPS ops only)
│   ├── resolver.py                 # 3-stage integration resolver
│   ├── camofox.py                  # Anti-detection browser
│   ├── agentmail_client.py         # AgentMail SDK wrapper
│   └── email_listener.py           # WebSocket email listener
├── infra/
│   ├── rate_limiter.py             # Token bucket rate limiting
│   ├── request_queue.py            # Priority request queue
│   └── trace.py                    # Per-request tracing
├── db/
│   ├── models.py                   # 18 SQLAlchemy models
│   └── session.py                  # Async DB session management
└── spaces/
    ├── platform.py                 # Spaces web app deployment
    ├── convex_api.py               # Convex backend client
    └── vercel_api.py               # Vercel deployment client
```
