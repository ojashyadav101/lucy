# Lucy

AI coworker that lives inside your Slack workspace. Built on [OpenClaw](https://github.com/open-claw) with a custom execution intelligence layer.

Lucy executes real work — she doesn't just answer questions. She writes code, manages integrations, monitors infrastructure, orchestrates multi-step workflows, and gets smarter over time.

## Architecture

```
Slack → Slack Bolt + FastAPI → Orchestrator → OpenClaw Gateway
                                    ├── Model Router (LiteLLM + RouteLLM)
                                    ├── Memory (GPTCache → Mem0/Qdrant → Engram)
                                    ├── Integrations (Composio + self-building)
                                    ├── Tasks (registry, approvals, scheduler)
                                    └── Security (LlamaFirewall + PII filter)
```

See [Documentation/lucy-docs.md](Documentation/lucy-docs.md) for the full vision document and technical specification.

## Quick Start

### 1. Prerequisites

- Python 3.12+
- PostgreSQL 16 (via Docker)
- Qdrant (via Docker)
- Slack app credentials

### 2. Setup

```bash
# Install dependencies
pip install -e ".[dev]"

# Start PostgreSQL and Qdrant
docker compose up -d

# Create test database
docker exec lucy-postgres createdb -U lucy lucy_test

# Initialize database schema
python scripts/init_db.py

# Run tests
pytest tests/unit/test_models.py -v
```

### 3. Configure Environment

```bash
cp .env.example .env
# Edit .env with your Slack tokens and OpenClaw credentials
```

### 4. Run Lucy

**Socket Mode (recommended for development):**
```bash
python scripts/run.py
# or
python -m scripts.run
```

**HTTP Mode (for production):**
```bash
python scripts/run.py --http --port 3000
```

### 5. Test OpenClaw Connection

```bash
# Test connection to your VPS gateway (167.86.82.46:18791)
python scripts/test_openclaw.py
```

This verifies:
- Gateway is reachable
- Health check passes
- Session can be spawned
- Simple message can be sent

### 6. Test the Gate

In Slack, type:
```
@Lucy hello
```

Lucy should respond: "Hello! I'm Lucy, your AI coworker. How can I help today?"

Then try:
```
@Lucy generate a report
```

This will:
1. Create a task
2. Spawn OpenClaw session with Kimi K2.5
3. Execute task
4. Send result back to Slack

---

## Running the Worker (Background Task Processing)

For production, run the worker separately to process tasks:

```bash
# Terminal 1: Run Slack bot
python scripts/run.py

# Terminal 2: Run background worker
python scripts/worker.py

# Or run one batch and exit
python scripts/worker.py --once
```

---

## Development Commands

```bash
# Linting
ruff check src/ tests/
ruff format src/ tests/

# Type checking
mypy src/

# Tests
pytest tests/unit/ -v                    # Unit tests
pytest tests/integration/ -v             # Integration tests
pytest -v                                 # All tests

# Test Connections
python scripts/test_slack_connection.py              # Test Slack API
python scripts/test_slack_connection.py --send-test  # Send test message
python scripts/test_openclaw.py                      # Test OpenClaw gateway

# Database
python scripts/init_db.py                # Create tables (dev)
python scripts/init_db.py --migrate       # Run Alembic migrations
alembic revision --autogenerate -m "add_feature"  # Create migration

# Run Services
python scripts/run.py                     # Slack bot (Socket Mode)
python scripts/run.py --http --port 3000  # HTTP mode
python scripts/worker.py                  # Background task worker
python scripts/worker.py --once           # Process one batch
```

## Project Structure

### Current (Step 2 Complete)

```
src/lucy/
├── app.py              # Entry point (Slack Bolt + FastAPI)
├── config.py           # Pydantic Settings
├── core/               # OpenClaw integration (NEW)
│   ├── __init__.py
│   ├── openclaw.py     # HTTP client for VPS gateway
│   └── agent.py        # Task execution orchestrator
├── db/
│   ├── __init__.py
│   ├── models.py       # 17 production-grade models
│   └── session.py      # Async SQLAlchemy session management
└── slack/
    ├── __init__.py
    ├── middleware.py   # Workspace/user resolution (lazy onboarding)
    ├── handlers.py     # Event handlers (@Lucy, /lucy, Block Kit)
    └── blocks.py       # Block Kit message templates

migrations/             # Alembic migrations
scripts/
├── init_db.py          # Database initialization
├── run.py              # Run Slack bot (Socket Mode or HTTP)
├── worker.py           # Background task worker (NEW)
├── test_openclaw.py    # Test OpenClaw connection (NEW)
└── test_slack_connection.py  # Test Slack connection

tests/
├── unit/
│   └── test_models.py
├── integration/
│   ├── test_slack_handlers.py
│   └── test_openclaw.py  # OpenClaw tests (NEW)
└── conftest.py
```

### Planned (90-Day Roadmap)

```
src/lucy/
├── core/               # LucyAgent, OpenClaw integration (Day 7)
├── memory/             # Three-layer memory system (Day 14)
├── routing/            # LiteLLM + RouteLLM (Day 14)
├── integrations/       # Composio + self-builder (Day 30)
├── tasks/              # Task registry, scheduler (Day 21)
├── security/           # LlamaFirewall, PII filter (Day 21)
├── knowledge/          # RAG pipeline (Day 45)
├── monitors/           # Heartbeats, patterns (Day 60)
├── sandbox/            # E2B code execution (Day 45)
├── browser/            # CamoFox stealth browser (Day 60)
├── costs/              # Per-workspace cost tracking (Day 30)
└── observability/      # Langfuse tracing (Day 14)
```

## Development

```bash
ruff check src/ tests/        # lint
mypy src/                     # type check
pytest                        # test
```

## License

Proprietary. Internal use only.
