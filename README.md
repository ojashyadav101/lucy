# Lucy

AI coworker that lives in Slack. Proactive, skill-driven, and built on [OpenClaw](https://github.com/open-claw).

Lucy doesn't just answer questions — she monitors channels, discovers workflows, executes real tasks with 10,000+ integrations, and gets smarter by writing everything she learns to her skill files.

## Architecture

```
Slack ←→ Slack Bolt (Socket Mode)
              ↓
         Pipeline (pipeline/)
         ├── fast_path.py    — shortcircuit greetings / status checks
         ├── router.py       — classify intent, select model tier
         ├── prompt.py       — build system prompt (SOUL + skills)
         ├── output.py       — sanitize & format LLM output
         └── humanize.py     — warm user-facing messages
              ↓
         LucyAgent (core/agent.py)
         ├── supervisor.py   — monitor progress, replan, escalate
         ├── sub_agents.py   — delegate to specialized sub-agents
         ├── task_manager.py  — background task lifecycle
         └── openclaw.py     — LLM API client (OpenRouter-compatible)
              ↓
         Workspace (workspace/)
         │   ├── skills/     — SKILL.md knowledge files
         │   ├── crons/      — proactive task definitions + LEARNINGS.md
         │   ├── logs/       — daily activity logs
         │   ├── data/       — JSON snapshots for trend detection
         │   └── team/ company/ — organizational context
              ↓
         Tools & Integrations
         ├── tools/          — file generation, web search, email, spaces
         ├── integrations/   — Composio SDK, 10,000+ actions
         ├── spaces/         — web app builder (Vercel + Convex)
         └── crons/          — APScheduler + heartbeat monitoring
```

## Project Structure

```
lucy/
├── src/lucy/
│   ├── app.py               — FastAPI + Slack Bolt entry point
│   ├── config.py             — Pydantic Settings (env vars)
│   │
│   ├── core/                 — Agent orchestration (5 files)
│   │   ├── agent.py          — LucyAgent, main run() entry point
│   │   ├── supervisor.py     — progress monitoring and replanning
│   │   ├── task_manager.py   — background task lifecycle
│   │   ├── sub_agents.py     — sub-agent dispatch
│   │   └── openclaw.py       — LLM API client
│   │
│   ├── pipeline/             — Message routing & processing (6 files)
│   │   ├── router.py         — intent classification, model tier selection
│   │   ├── fast_path.py      — quick-response shortcircuit (<500ms)
│   │   ├── edge_cases.py     — concurrency & interrupt handling
│   │   ├── prompt.py         — system prompt builder
│   │   ├── output.py         — output sanitization & formatting
│   │   └── humanize.py       — warm user-facing message generation
│   │
│   ├── infra/                — Infrastructure utilities (3 files)
│   │   ├── rate_limiter.py   — token bucket rate limiting
│   │   ├── request_queue.py  — priority request queuing
│   │   └── trace.py          — request-scoped tracing
│   │
│   ├── slack/                — Slack API & Block Kit (7 files)
│   │   ├── handlers.py       — event/command handlers → agent.run()
│   │   ├── middleware.py     — workspace/user/channel resolution
│   │   ├── blockkit.py       — Block Kit message builder
│   │   ├── rich_output.py    — enhanced formatting
│   │   ├── hitl.py           — human-in-the-loop approvals
│   │   └── reactions.py      — emoji reaction management
│   │
│   ├── workspace/            — Filesystem memory & skills (12 files)
│   │   ├── filesystem.py     — WorkspaceFS (read/write/search)
│   │   ├── skills.py         — SKILL.md parser and manager
│   │   ├── memory.py         — session memory management
│   │   ├── onboarding.py     — workspace setup
│   │   ├── executor.py       — code execution (sandbox + local)
│   │   ├── snapshots.py      — JSON data persistence for trends
│   │   └── ...               — activity_log, slack_reader, timezone, etc.
│   │
│   ├── crons/                — Scheduled tasks & heartbeat (3 files)
│   │   ├── scheduler.py      — APScheduler cron management
│   │   └── heartbeat.py      — condition-based monitoring
│   │
│   ├── integrations/         — External service clients (16 files)
│   │   ├── composio_client.py — Composio SDK (10,000+ actions)
│   │   ├── custom_wrappers/  — Clerk, Polar.sh, etc.
│   │   └── ...               — email, search, MCP, OpenAPI, gateway
│   │
│   ├── tools/                — Agent tool implementations (5 files)
│   │   ├── file_generator.py — PDF, Excel, CSV generation
│   │   ├── web_search.py     — grounded web search
│   │   ├── spaces.py         — Spaces app tools
│   │   └── email_tools.py    — email send/read tools
│   │
│   ├── spaces/               — Web app platform (6 files)
│   │   ├── platform.py       — init, deploy, manage apps
│   │   ├── vercel_api.py     — Vercel deployment API
│   │   └── convex_api.py     — Convex backend API
│   │
│   └── db/                   — Database (3 files)
│       ├── models.py         — SQLAlchemy ORM models
│       └── session.py        — async session management
│
├── prompts/                  — LLM prompt templates
│   ├── SOUL.md               — personality and operating principles
│   ├── SYSTEM_CORE.md        — core system instructions
│   ├── SYSTEM_PROMPT.md      — structured prompt template
│   ├── modules/              — intent-specific prompt sections
│   └── sub_agents/           — sub-agent system prompts
│
├── workspace_seeds/          — Templates seeded into new workspaces
│   ├── skills/               — 17 platform skill files
│   └── crons/                — default cron task.json configs
│
├── docs/                     — Planning and reference documents
├── scripts/                  — run.py, init_db.py
├── tests/                    — test suite
├── migrations/               — Alembic database migrations
├── templates/                — app starter templates
├── docker-compose.yml        — PostgreSQL
├── pyproject.toml            — dependencies and tooling
└── .env.example              — environment variable template
```

## Quick Start

```bash
# Install
pip install -e ".[dev]"

# Start PostgreSQL
docker compose up -d

# Initialize database
python scripts/init_db.py

# Configure
cp .env.example .env
# Edit .env with your Slack tokens, OpenClaw key, Composio key

# Run
python scripts/run.py
```

Then in Slack: `@Lucy hello`

On first message to a workspace, Lucy will:
1. Create the workspace filesystem directory
2. Seed 16 platform skills and 3 default crons
3. Profile team members from Slack
4. Start the cron scheduler (heartbeat, workflow discovery, etc.)

## How It Works

**Reactive**: User @mentions Lucy or DMs her → handler calls `agent.run()` → skills build the system prompt → LLM uses Composio meta-tools → response posted to Slack.

**Proactive**: APScheduler fires crons on schedule → fresh agent instance runs with the cron's description as its instruction → agent reads LEARNINGS.md → acts on Slack (DMs, reactions, channel posts) → logs execution.

**Knowledge**: Everything Lucy learns is written to the filesystem — skills, learnings, team profiles, company context. No vector databases. Search is `grep`.

## Development

```bash
ruff check src/ tests/       # lint
ruff format src/ tests/      # format
mypy src/                    # type check
pytest                       # test
```

## Key Docs

- **[docs/DREAM.md](docs/DREAM.md)** — Vision and architecture decisions
- **[docs/GAP-ANALYSIS.md](docs/GAP-ANALYSIS.md)** — Feature comparison with Viktor
- **[docs/RESTRUCTURING-PLAN.md](docs/RESTRUCTURING-PLAN.md)** — 5-phase implementation plan

## License

Proprietary. Internal use only.
