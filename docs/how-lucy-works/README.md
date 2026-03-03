# How Lucy Works — Overview

This folder is the **definitive reference** for understanding, operating, and developing Lucy. Start here.

---

## What Is Lucy?

Lucy is an async Python Slack bot (AI coworker) that:
- Listens to messages in Slack via **Socket Mode** (persistent WebSocket — no inbound port needed)
- Processes messages through an **LLM agent loop** (OpenClaw gateway → OpenRouter → Gemini/Kimi models)
- Uses **tools** (Composio integrations, MCP servers, web search, file gen, email, Spaces)
- Maintains **per-workspace memory** (skills, activity logs, session context)
- Runs **proactive crons** (APScheduler-based scheduled tasks per workspace)

---

## Where Lucy Runs

**Production: Contabo VPS — `167.86.82.46`**

Lucy is a `systemd` service. It starts on boot and restarts automatically on crash. It is **not** running on any developer laptop — if your laptop closes, Lucy is unaffected.

See [DEPLOYMENT.md](./DEPLOYMENT.md) for the full hosting setup.

---

## How to Deploy a Change

```bash
# 1. Make your code changes locally
# 2. Commit
git add . && git commit -m "your message"
# 3. Push — goes to GitHub AND triggers auto-deploy on Contabo
git push origin main
# 4. Lucy restarts on Contabo ~10 seconds later
# 5. Watch logs
ssh contabo-lucy "journalctl -u lucy -f"
```

That's it. One push, everything updates.

---

## How to Check If Lucy Is Running

```bash
ssh contabo-lucy "systemctl is-active lucy && journalctl -u lucy -n 10 --no-pager"
```

---

## Repository Structure

```
lucy/
├── src/lucy/               — main Python package
│   ├── app.py              — FastAPI + Slack Bolt app (HTTP mode entry point)
│   ├── config.py           — All settings (Pydantic Settings, LUCY_* env vars)
│   ├── core/               — Agent orchestrator, OpenClaw client, supervisor
│   ├── pipeline/           — Message router, system prompt builder, output sanitizer
│   ├── slack/              — Bolt event handlers, middleware, Block Kit
│   ├── workspace/          — Filesystem memory: skills, activity logs, onboarding
│   ├── crons/              — APScheduler proactivity engine
│   ├── integrations/       — Composio, MCP, AgentMail, custom wrappers
│   ├── tools/              — File gen, web search, email, Spaces tools
│   ├── infra/              — Rate limiter, circuit breaker, tracing
│   ├── db/                 — SQLAlchemy models + async session
│   └── spaces/             — Vercel + Convex web app builder
├── scripts/
│   ├── run.py              — Primary startup script (Socket Mode / HTTP)
│   └── init_db.py          — Database init / Alembic migrations
├── prompts/                — SOUL.md (system prompt), modules (tool_use, etc.)
├── deploy/                 — VPS deployment: systemd service, git hook, setup script
├── docs/                   — Architecture docs (this folder + others)
├── workspaces/             — Live workspace data (per Slack team, NOT in git)
├── workspace_seeds/        — Default skill seeds for new workspaces
├── migrations/             — Alembic DB migration files
├── tests/                  — pytest test suites
└── pyproject.toml          — Project metadata, deps, ruff/mypy config
```

---

## Key Concepts

### Socket Mode
Lucy connects outbound to Slack's WebSocket API. No firewall rules or inbound ports needed. The connection is maintained as long as Lucy is running.

### OpenClaw Gateway
A local proxy on the VPS (`127.0.0.1:18789`) that Lucy uses for tool execution (bash sandbox, memory). The actual LLM calls go through OpenRouter using the key in `.env`.

### Workspaces
Each Slack team (workspace) gets a directory under `workspaces/` containing its own skills YAML, cron definitions, and activity logs. These are persisted on the VPS filesystem.

### Multi-tenant
Lucy serves multiple Slack workspaces from a single process. The `resolve_workspace_middleware` identifies the workspace on every message.

---

## Documents in This Folder

| File | What It Covers |
|---|---|
| [DEPLOYMENT.md](./DEPLOYMENT.md) | VPS setup, systemd, git auto-deploy, log commands |
| [README.md](./README.md) | This file — overview and orientation |

Other architecture docs in `/docs/`:
- `ARCHITECTURE.md` — System architecture overview
- `AGENT_LOOP.md` — How the agent processes messages
- `MESSAGE_PIPELINE.md` — Full message flow from Slack to response
- `OPENCLAW_BOUNDARY.md` — What OpenClaw does vs what Lucy does
- `WORKSPACE_MEMORY.md` — Skills, memory, workspace filesystem
- `TOOLS_INTEGRATIONS.md` — All tools and integrations
- `DATABASE.md` — DB schema and models
- `CRONS_HEARTBEAT.md` — Proactivity engine
- `DEPLOYMENT.md` (in `/docs/`) — Original deployment notes
- `QUICK_REFERENCE.md` — Command cheatsheet

---

## Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --tb=short

# Run specific test file
pytest tests/test_action_safety.py -v
```

Tests use `pytest-asyncio` with `asyncio_mode = "auto"`.

---

## Configuration

All config is via `LUCY_*` environment variables, loaded from `.env` via Pydantic Settings.

```bash
# Local dev
cp .env.example .env
# fill in tokens

# Production (on VPS)
nano /opt/lucy/app/.env
```

Never hardcode tokens. See `src/lucy/config.py` for all available settings.
