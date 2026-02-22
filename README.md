# Lucy

AI coworker that lives in Slack. Proactive, skill-driven, and built on [OpenClaw](https://github.com/open-claw).

Lucy doesn't just answer questions — she monitors channels, discovers workflows, executes real tasks with 10,000+ integrations, and gets smarter by writing everything she learns to her skill files.

## Architecture

```
Slack ←→ Slack Bolt (Socket Mode)
              ↓
         LucyAgent (core/agent.py)
         ├── System Prompt (SOUL.md + skills + meta-tools)
         ├── OpenClaw LLM Gateway (core/openclaw.py)
         ├── Workspace Filesystem (workspace/)
         │   ├── skills/  — SKILL.md knowledge files
         │   ├── crons/   — proactive task definitions
         │   ├── logs/    — daily activity logs
         │   └── team/ company/ — organizational context
         ├── Composio Meta-Tools (integrations/)
         │   └── 5 tools → 10,000+ actions (Gmail, Calendar, GitHub, ...)
         └── Cron Scheduler (crons/)
             ├── Heartbeat (4x/day)
             ├── Issue Monitor (every 2 min)
             └── Workflow Discovery (Mon/Thu)
```

## Project Structure

```
lucy/
├── src/lucy/                # Main package
│   ├── app.py               # FastAPI + Slack Bolt startup
│   ├── config.py            # Pydantic Settings (all env vars)
│   │
│   ├── core/                # Agent + LLM
│   │   ├── agent.py         # Main orchestrator
│   │   ├── openclaw.py      # OpenClaw HTTP client
│   │   └── types.py         # Shared dataclasses
│   │
│   ├── slack/               # Slack interface
│   │   ├── handlers.py      # Event/command handlers
│   │   ├── middleware.py     # Workspace/user resolution
│   │   ├── blocks.py        # Block Kit composers
│   │   └── thread_manager.py
│   │
│   ├── workspace/           # Filesystem knowledge layer
│   │   └── (to build)       # skills, logs, snapshots, onboarding
│   │
│   ├── crons/               # Proactivity engine
│   │   └── (to build)       # APScheduler-based scheduling
│   │
│   ├── integrations/        # Composio SDK
│   │   ├── composio_client.py
│   │   └── registry.py
│   │
│   └── db/                  # Database
│       ├── models.py        # SQLAlchemy ORM models
│       └── session.py       # Async session management
│
├── assets/                  # Static assets
│   └── SOUL.md              # Lucy's personality definition
│
├── workspace_seeds/         # Templates for new workspaces
│   ├── skills/              # Platform skill definitions
│   └── crons/               # Default cron task definitions
│
├── reference/               # Viktor workspace export (read-only reference)
│   └── viktor/              # Skills, crons, docs from Viktor
│
├── docs/                    # Planning documents
│   ├── DREAM.md             # North star vision
│   ├── GAP-ANALYSIS.md      # Feature gaps vs Viktor
│   └── RESTRUCTURING-PLAN.md # 5-phase build plan
│
├── scripts/                 # Utilities
│   ├── run.py               # Start Slack bot
│   └── init_db.py           # Initialize database
│
├── tests/                   # Test suite
│   ├── conftest.py          # Shared fixtures
│   └── (to build)
│
├── migrations/              # Alembic database migrations
├── .cursor/rules/           # IDE rules for consistent development
├── docker-compose.yml       # PostgreSQL
├── pyproject.toml           # Dependencies and tooling config
├── slack-manifest.json      # Slack app manifest
└── .env.example             # Environment variable template
```

## Quick Start

```bash
# Install dependencies
pip install -e ".[dev]"

# Start PostgreSQL
docker compose up -d

# Initialize database
python scripts/init_db.py

# Configure environment
cp .env.example .env
# Edit .env with your Slack tokens and OpenClaw credentials

# Run Lucy
python scripts/run.py
```

Then in Slack: `@Lucy hello`

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
- **[docs/RESTRUCTURING-PLAN.md](docs/RESTRUCTURING-PLAN.md)** — Phased implementation plan

## License

Proprietary. Internal use only.
