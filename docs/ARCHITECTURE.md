# Lucy — System Architecture

> **Last updated:** 2026-02-26
>
> This is the master document. Each section links to a detailed deep-dive.

---

## What Is Lucy?

Lucy is an async-first AI agent that lives in Slack. She reads messages,
classifies intent, selects the right model tier, plans complex tasks through a
supervisor, executes tool calls in a multi-turn loop, and delivers polished
responses — all while maintaining workspace-level memory, running scheduled
jobs, and monitoring external services.

She is built on top of **OpenRouter** for LLM inference, **Composio** for
third-party tool orchestration, and **Slack Bolt** for real-time event handling.

---

## 10 Core Systems

| # | System | Package | Purpose |
|---|--------|---------|---------|
| 1 | **Agent Orchestrator** | `core/` | Multi-turn LLM loop, supervisor, sub-agents, model escalation |
| 2 | **Message Pipeline** | `pipeline/` | Intent classification, prompt building, output processing |
| 3 | **Slack Layer** | `slack/` | Event handling, Block Kit, reactions, HITL approvals |
| 4 | **Workspace & Memory** | `workspace/` | Per-workspace filesystem, three-tier memory, skills, onboarding |
| 5 | **Cron Engine** | `crons/` | APScheduler-based recurring jobs with Slack delivery |
| 6 | **Heartbeat Monitor** | `crons/heartbeat` | Real-time condition monitors (API health, page content, thresholds) |
| 7 | **Tools** | `tools/` | File generation, web search, Spaces, email |
| 8 | **Integrations** | `integrations/` | Composio meta-tools, custom wrappers, MCP, OpenAPI registration |
| 9 | **Infrastructure** | `infra/` | Rate limiting, priority queue, request tracing |
| 10 | **Prompts & Personality** | `prompts/` | SOUL.md, SYSTEM_CORE.md, intent modules, sub-agent prompts |

---

## How a Message Flows Through Lucy

```
User @mentions Lucy in Slack
        │
        ▼
┌─────────────────────────────────────────────────────────┐
│  1. SLACK LAYER (slack/handlers.py)                     │
│     ├─ Event deduplication (30s TTL)                    │
│     ├─ Middleware: resolve workspace → user → channel   │
│     ├─ Contextual emoji reaction                        │
│     └─ Thread lock (one agent per thread)               │
└───────────────────────┬─────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────┐
│  2. FAST PATH CHECK (pipeline/fast_path.py)             │
│     Simple greetings/acks → instant reply, no agent     │
│     If fast → post response, return early               │
└───────────────────────┬─────────────────────────────────┘
                        │ (not fast path)
                        ▼
┌─────────────────────────────────────────────────────────┐
│  3. EDGE CASE CHECK (pipeline/edge_cases.py)            │
│     ├─ Status query → format_task_status()              │
│     ├─ Task cancellation → handle_task_cancellation()   │
│     └─ Thread interrupt → decide_thread_interrupt()     │
└───────────────────────┬─────────────────────────────────┘
                        │ (normal message)
                        ▼
┌─────────────────────────────────────────────────────────┐
│  4. INTENT CLASSIFICATION (pipeline/router.py)          │
│     ├─ classify_and_route() → ModelChoice               │
│     │   intent: chat|lookup|code|data|reasoning|...     │
│     │   model tier: fast|default|code|research|frontier │
│     │   prompt_modules: [coding, data_tasks, ...]       │
│     └─ Priority classification → queue slot             │
└───────────────────────┬─────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────┐
│  5. AGENT ORCHESTRATOR (core/agent.py → run())          │
│     ├─ Ensure workspace exists (onboard if new)         │
│     ├─ Fetch connected services + Composio meta-tools   │
│     ├─ Build system prompt (pipeline/prompt.py)         │
│     ├─ Build conversation history from Slack thread     │
│     ├─ Inject context: session memory, history search   │
│     ├─ Supervisor: create_plan() for complex tasks      │
│     └─ Enter _agent_loop()                              │
└───────────────────────┬─────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────┐
│  6. MULTI-TURN LLM LOOP (core/agent.py → _agent_loop)  │
│     while True:                                         │
│       ├─ Call LLM via OpenClawClient                    │
│       ├─ If text response → break, return it            │
│       ├─ If tool calls → execute in parallel            │
│       ├─ Append tool results to messages                │
│       ├─ Post progress updates (turn 3, every 5 after)  │
│       ├─ Supervisor checkpoint (every 3 turns / 60s)    │
│       │   ├─ CONTINUE → keep going                      │
│       │   ├─ INTERVENE → inject guidance                │
│       │   ├─ REPLAN → new plan, reset                   │
│       │   ├─ ESCALATE → switch to stronger model        │
│       │   ├─ ASK_USER → post clarification question     │
│       │   └─ ABORT → stop gracefully                    │
│       ├─ Model escalation (code → frontier on errors)   │
│       └─ Loop detection (3x same tool call → break)     │
│     4-hour absolute safety net                          │
└───────────────────────┬─────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────┐
│  7. QUALITY + VERIFICATION GATES                        │
│     ├─ Quality gate: assess response completeness       │
│     ├─ Verification gate: retry with escalated model    │
│     └─ Memory persistence: save memorable facts         │
└───────────────────────┬─────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────┐
│  8. OUTPUT PIPELINE (pipeline/output.py)                │
│     Layer 1: _sanitize()     → strip paths, tool names  │
│     Layer 2: _convert_md()   → Markdown → Slack mrkdwn  │
│     Layer 3: _validate_tone()→ catch robotic patterns   │
│     Layer 4: _deai()         → remove AI tells (regex)  │
└───────────────────────┬─────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────┐
│  9. SLACK DELIVERY (slack/handlers.py + blockkit.py)    │
│     ├─ text_to_blocks() → Block Kit blocks              │
│     ├─ enhance_blocks() → add emojis, format links      │
│     ├─ split_response() → chunk if >3000 chars          │
│     └─ chat_postMessage() → deliver to user             │
└─────────────────────────────────────────────────────────┘
```

---

## System Interconnection Map

```
                     ┌─────────────┐
                     │   prompts/  │
                     │  SOUL.md    │
                     │  SYSTEM_    │
                     │  CORE.md    │
                     │  modules/   │
                     └──────┬──────┘
                            │ loaded by
                            ▼
┌──────────┐    ┌──────────────────┐    ┌────────────────┐
│  slack/  │───▶│    pipeline/     │───▶│     core/      │
│ handlers │    │ router           │    │ agent          │
│ blockkit │    │ prompt           │    │ supervisor     │
│ hitl     │    │ output           │    │ sub_agents     │
│ rich_out │    │ humanize         │    │ task_manager   │
│ react    │    │ fast_path        │    │ openclaw       │
│ middle   │    │ edge_cases       │    └───────┬────────┘
└────┬─────┘    └──────────────────┘            │
     │                                          │ calls tools via
     │                                          ▼
     │          ┌──────────────────┐    ┌────────────────┐
     │          │   workspace/     │◀──▶│ integrations/  │
     │          │ filesystem       │    │ composio_client│
     │          │ memory           │    │ resolver       │
     │          │ skills           │    │ mcp_manager    │
     │          │ executor         │    │ wrapper_gen    │
     │          │ onboarding       │    │ camofox        │
     │          │ snapshots        │    │ agentmail      │
     │          └──────────────────┘    └────────────────┘
     │                  ▲
     │                  │ persists to filesystem
     │          ┌───────┴──────────┐    ┌────────────────┐
     └─────────▶│    crons/        │    │    tools/      │
                │ scheduler        │    │ file_generator │
                │ heartbeat        │    │ web_search     │
                └──────────────────┘    │ spaces         │
                                        │ email_tools    │
                        ┌───────────┐   └────────────────┘
                        │  infra/   │
                        │ rate_limit│◀── used by core/ and slack/
                        │ req_queue │
                        │ trace     │
                        └───────────┘
```

---

## Model Tier Strategy

Lucy uses six model tiers, each mapped to a specific model in `config.py`:

| Tier | Default Model | Used For |
|------|---------------|----------|
| `fast` | `gemini-2.5-flash` | Greetings, simple lookups, supervisor checks, planning |
| `default` | `minimax-m2.5` | Tool-use tasks, integrations, general requests |
| `code` | `minimax-m2.5` | Code writing, data analysis, document generation |
| `research` | `gemini-3-flash-preview` | Research, competitive analysis, deep investigation |
| `document` | `minimax-m2.5` | Report/PDF/Excel generation |
| `frontier` | `gemini-3.1-pro-preview` | Complex multi-step tasks, escalation fallback |

**Escalation path:** `fast → default → code → research → frontier`

Model escalation happens automatically when:
- Empty responses are returned (switch to frontier)
- 3+ consecutive errors detected (supervisor escalation)
- Code tools are called mid-loop (switch to code tier)
- 400 errors occur (switch to frontier)
- Supervisor issues `ESCALATE` decision

---

## Key Constants

| Constant | Value | Location | Purpose |
|----------|-------|----------|---------|
| `MAX_TOOL_TURNS` | 50 | `core/agent.py` | Max tool-call loop iterations |
| `ABSOLUTE_MAX_SECONDS` | 14,400 (4h) | `core/agent.py` | Hard safety net for agent execution |
| `MAX_CONTEXT_MESSAGES` | 40 | `core/agent.py` | Conversation window before trimming |
| `TOOL_RESULT_MAX_CHARS` | 16,000 | `core/agent.py` | Max chars per tool result |
| `MAX_PAYLOAD_CHARS` | 120,000 | `core/agent.py` | Total payload before trimming |
| `SUB_MAX_TURNS` | 10 | `core/sub_agents.py` | Sub-agent max iterations |
| `SUB_TIMEOUT_SECONDS` | 120 | `core/sub_agents.py` | Sub-agent timeout |
| `MAX_BACKGROUND_TASKS` | 5 | `core/task_manager.py` | Per-workspace background task limit |
| `MAX_QUEUE_DEPTH_PER_WORKSPACE` | 50 | `infra/request_queue.py` | Queue backpressure limit |
| `SUPERVISOR_CHECK_INTERVAL_TURNS` | 3 | `core/supervisor.py` | Supervisor checkpoint frequency |
| `_LLM_REWRITE_THRESHOLD` | 999 | `pipeline/output.py` | Disabled (set to 999) |

---

## Package Layout

```
src/lucy/
├── app.py                  # FastAPI + Slack Bolt entry point
├── config.py               # Pydantic Settings (all LUCY_* env vars)
│
├── core/                   # Agent orchestration
│   ├── agent.py            # LucyAgent, run(), _agent_loop()
│   ├── supervisor.py       # Plan creation, progress evaluation
│   ├── sub_agents.py       # Isolated sub-agent execution
│   ├── task_manager.py     # Background task lifecycle
│   └── openclaw.py         # OpenRouter LLM client
│
├── pipeline/               # Message processing pipeline
│   ├── router.py           # Intent classification, model selection
│   ├── fast_path.py        # Instant replies for simple messages
│   ├── edge_cases.py       # Status queries, cancellations, deduplication
│   ├── prompt.py           # System prompt assembly
│   ├── output.py           # 4-layer output sanitization
│   └── humanize.py         # Pre-generated message pools
│
├── slack/                  # Slack interface
│   ├── handlers.py         # Event handlers, message flow
│   ├── blockkit.py         # Block Kit conversion
│   ├── rich_output.py      # Link formatting, emoji enhancement
│   ├── hitl.py             # Human-in-the-loop approvals
│   ├── middleware.py        # Workspace/user/channel resolution
│   └── reactions.py        # Contextual emoji reactions
│
├── workspace/              # Per-workspace data management
│   ├── filesystem.py       # WorkspaceFS class
│   ├── skills.py           # Skill loading and matching
│   ├── memory.py           # Three-tier memory system
│   ├── onboarding.py       # New workspace scaffolding
│   ├── executor.py         # Code execution (Composio + local)
│   ├── snapshots.py        # Snapshot persistence and deltas
│   ├── activity_log.py     # Daily activity logging
│   ├── slack_reader.py     # Channel listing
│   ├── slack_sync.py       # Message sync to filesystem
│   ├── timezone.py         # User timezone resolution
│   └── history_search.py   # Slack history search
│
├── crons/                  # Scheduled jobs
│   ├── scheduler.py        # CronScheduler, job lifecycle
│   └── heartbeat.py        # Real-time condition monitors
│
├── tools/                  # Agent tool implementations
│   ├── file_generator.py   # PDF, Excel, CSV generation
│   ├── web_search.py       # Gemini-powered web search
│   ├── spaces.py           # Lucy Spaces commands
│   └── email_tools.py      # AgentMail operations
│
├── integrations/           # External service connectors
│   ├── composio_client.py  # 5 meta-tools via Composio SDK
│   ├── resolver.py         # 3-stage integration resolution
│   ├── mcp_manager.py      # MCP server management
│   ├── openapi_registrar.py# OpenAPI spec registration
│   ├── wrapper_generator.py# LLM-generated API wrappers
│   ├── camofox.py          # Headless browser for scraping
│   ├── agentmail_client.py # Email send/receive
│   ├── email_listener.py   # Inbound email polling
│   ├── grounded_search.py  # Gemini grounded search
│   └── openclaw_gateway.py # OpenClaw gateway client
│
├── infra/                  # Infrastructure utilities
│   ├── rate_limiter.py     # Token bucket per model/API
│   ├── request_queue.py    # Priority queue with worker pool
│   └── trace.py            # Per-request tracing with spans
│
├── spaces/                 # Lucy Spaces web app platform
│   ├── platform.py         # Project init, deploy, delete
│   ├── project_config.py   # SpaceProject dataclass
│   ├── convex_api.py       # Convex backend API
│   └── vercel_api.py       # Vercel deployment API
│
└── db/                     # Database layer
    ├── models.py           # SQLAlchemy models (Heartbeat, etc.)
    └── session.py          # Async session management
```

---

## Deep-Dive Documents

### Start Here

| Document | What It Covers |
|----------|----------------|
| [QUICK_REFERENCE.md](./QUICK_REFERENCE.md) | **Read first.** Task-oriented lookups, "where is X?", debugging guide, how-to guides |
| [OPENCLAW_BOUNDARY.md](./OPENCLAW_BOUNDARY.md) | What's native Lucy vs what comes from OpenClaw Gateway, naming confusion |

### System Deep-Dives

| Document | What It Covers |
|----------|----------------|
| [AGENT_LOOP.md](./AGENT_LOOP.md) | Agent orchestrator, supervisor system, sub-agents, model escalation, safety nets |
| [MESSAGE_PIPELINE.md](./MESSAGE_PIPELINE.md) | Router, fast path, prompt builder, output pipeline (4 layers), humanize pool, edge cases |
| [SLACK_LAYER.md](./SLACK_LAYER.md) | Event handling, Block Kit conversion, rich output, HITL approvals, reactions, middleware |
| [WORKSPACE_MEMORY.md](./WORKSPACE_MEMORY.md) | Workspace filesystem, three-tier memory, skills, onboarding, Slack sync, timezone, snapshots |
| [CRONS_HEARTBEAT.md](./CRONS_HEARTBEAT.md) | Cron scheduler, heartbeat monitors, condition evaluators |
| [TOOLS_INTEGRATIONS.md](./TOOLS_INTEGRATIONS.md) | File generation, web search, email, Spaces, Composio internals, OpenClaw client, resolver chain |
| [INFRASTRUCTURE.md](./INFRASTRUCTURE.md) | Rate limiting (TokenBucket), request queue (priority, dedup), request tracing (spans, logs) |

### Reference & Operations

| Document | What It Covers |
|----------|----------------|
| [BEHAVIOR_GUIDE.md](./BEHAVIOR_GUIDE.md) | Decision logic: emojis, progress messages, personality, cross-system effects |
| [DATABASE.md](./DATABASE.md) | SQLAlchemy models, enums, Alembic migrations, async session management |
| [DEPLOYMENT.md](./DEPLOYMENT.md) | Entry points, environment variables, keys.json, Docker, health checks |
| [PROMPTS_REFERENCE.md](./PROMPTS_REFERENCE.md) | SOUL.md, SYSTEM_CORE.md, intent modules, sub-agent prompts |
| [SKILLS_SEEDS.md](./SKILLS_SEEDS.md) | Platform skills, workspace seeds, default cron configurations, Spaces templates |
| [ARCHIVED_CODE.md](./ARCHIVED_CODE.md) | Removed code reference for historical context |

### External Reference

| Directory | What It Covers |
|-----------|----------------|
| [openclaw/](./openclaw/) | Local cache of OpenClaw docs Lucy needs (tools invoke API, heartbeat, cron, exec, skills, web) |
