# Lucy

AI coworker that lives in Slack. Proactive, skill-driven, and built on [OpenClaw](https://github.com/open-claw).

Lucy doesn't just answer questions — she monitors channels, discovers workflows, executes real tasks with 10,000+ integrations, and gets smarter by writing everything she learns to her skill files.

## Architecture

```
Slack ←→ Slack Bolt (Socket Mode)
              ↓
         LucyAgent (core/agent.py)
         ├── System Prompt (SOUL.md + SYSTEM_PROMPT.md + live skills)
         ├── OpenClaw LLM Gateway (core/openclaw.py)
         ├── Workspace Filesystem (workspace/)
         │   ├── skills/    — SKILL.md knowledge files
         │   ├── crons/     — proactive task definitions + LEARNINGS.md
         │   ├── logs/      — daily activity logs
         │   ├── data/      — JSON snapshots for trend detection
         │   ├── scripts/   — Python scripts for execution
         │   └── team/ company/ — organizational context
         ├── Composio Meta-Tools (integrations/)
         │   └── 5 tools → 10,000+ actions (Gmail, Calendar, GitHub, ...)
         ├── Code Executor (workspace/executor.py)
         │   └── Composio sandbox → local fallback
         └── Cron Scheduler (crons/scheduler.py)
             ├── Heartbeat (4x/day weekdays)
             ├── Workflow Discovery (Mon & Thu)
             └── Channel Introductions (first 3 days)
```

## Project Structure

```
lucy/
├── src/lucy/
│   ├── app.py               # FastAPI + Slack Bolt + cron startup
│   ├── config.py             # Pydantic Settings (env vars + keys.json)
│   │
│   ├── core/                 # Agent + LLM
│   │   ├── agent.py          # LucyAgent — single run() entry point
│   │   ├── openclaw.py       # OpenClaw HTTP client (OpenAI-compatible)
│   │   ├── prompt.py         # System prompt builder (SOUL + skills)
│   │   └── types.py          # Shared dataclasses
│   │
│   ├── slack/                # Slack interface
│   │   ├── handlers.py       # Event/command handlers → agent.run()
│   │   └── middleware.py     # Workspace/user/channel resolution
│   │
│   ├── workspace/            # Filesystem knowledge layer
│   │   ├── filesystem.py     # WorkspaceFS (read/write/search/copy)
│   │   ├── skills.py         # SKILL.md parser and manager
│   │   ├── onboarding.py     # Day 1 workspace setup
│   │   ├── activity_log.py   # Daily activity logs for crons
│   │   ├── slack_reader.py   # Slack message fetcher for crons
│   │   ├── executor.py       # Code execution (Composio sandbox + local)
│   │   └── snapshots.py      # JSON data persistence for trends
│   │
│   ├── crons/                # Proactivity engine
│   │   └── scheduler.py      # APScheduler: load task.json, fire agent
│   │
│   ├── integrations/         # Composio SDK
│   │   └── composio_client.py # 5 meta-tools + OAuth + execution
│   │
│   └── db/                   # Database
│       ├── models.py         # SQLAlchemy ORM models
│       └── session.py        # Async session management
│
├── assets/
│   ├── SOUL.md               # Personality and tone
│   └── SYSTEM_PROMPT.md      # Structured system prompt template
│
├── workspace_seeds/          # Templates seeded into new workspaces
│   ├── skills/               # 16 platform skill SKILL.md files
│   └── crons/                # Default cron task.json (heartbeat, etc.)
│
├── reference/viktor/         # Viktor workspace export (read-only)
├── docs/                     # Planning documents
├── scripts/                  # Utilities (run.py, init_db.py)
├── tests/                    # Test suite
├── migrations/               # Alembic database migrations
├── .cursor/rules/            # IDE rules for consistent development
├── docker-compose.yml        # PostgreSQL
├── pyproject.toml            # Dependencies and tooling
└── .env.example              # Environment variable template
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
