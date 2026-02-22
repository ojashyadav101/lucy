# Gap Analysis v2: Viktor vs Lucy (Post-Cleanup)

> Updated: Feb 22, 2026 — After deleting 12 source files, 15 old docs, 15 test files.

## Current State After Cleanup

**Remaining Lucy source**: 23 Python files, 6,452 lines total.

```
src/lucy/
├── core/          agent.py (1,099), openclaw.py (268), circuit_breaker.py (290), types.py (23)
├── slack/         handlers.py (721), middleware.py (239), blocks.py (332), thread_manager.py (509)
├── db/            models.py (1,284), session.py (110)
├── integrations/  composio_client.py (349), registry.py (168)
├── routing/       router.py (210)
├── observability/ metrics.py (328)
├── app.py (357), config.py (100)
```

**What works**: Slack connection (Socket Mode), OpenClaw client (HTTP API), Composio client (tool execution), PostgreSQL (models + sessions), structured logging.

**What's broken**: `agent.py` still imports deleted modules (memory, retrieval, classifier, tiers, safety, timeout). This file needs a full rewrite.

---

## Feature Gaps (Post-Cleanup)

### CRITICAL — Must have for Viktor parity

| # | Feature | Viktor | Lucy Now | Effort |
|---|---------|--------|----------|--------|
| 1 | **Workspace filesystem** | `/work/` with company/, team/, skills/, crons/, data/, logs/ | None | Medium |
| 2 | **Skill system** | 27 SKILL.md files with YAML frontmatter, auto-injected into prompt | None (only SOUL.md) | Medium |
| 3 | **System prompt** (~3K words) | 6 sections: philosophy, skills, work approach, communication, rules, available skills | ~500 words personality only | Medium |
| 4 | **Heartbeat cron** (4x/day) | Reads messages, finds opportunities, takes action, updates LEARNINGS.md | None | Medium |
| 5 | **Issue monitor cron** (every 2 min) | Classifies messages, proposes tickets, maintains state | None | Medium |
| 6 | **Workflow discovery cron** (Mon/Thu) | Investigates team members, proposes automations | None | Medium |
| 7 | **Cron scheduler** | Reads task.json, runs agent crons + script crons | None | Medium |
| 8 | **Day 1 onboarding** | Profile team, explore integrations, create skill files, set up crons | None | Medium |
| 9 | **Code execution** | Full Python sandbox, scripts saved for reuse | None (OpenClaw sandbox exists but unused) | Small |
| 10 | **Tool calling** (Composio meta-tools) | SDK wrappers + skill routing | BM25 retrieval (deleted) — needs new approach | Medium |
| 11 | **LEARNINGS.md** | Accumulated knowledge per cron, read by all instances | None | Small |
| 12 | **Team profiling** | Observational from Slack messages, updated in team/SKILL.md | None | Small |
| 13 | **Company knowledge** | company/SKILL.md with industry, products, revenue | None | Small |
| 14 | **Agent rewrite** | N/A (agent.py needs full rewrite after deletions) | Broken — imports deleted modules | Medium |

### IMPORTANT — Should have

| # | Feature | Viktor | Lucy Now | Effort |
|---|---------|--------|----------|--------|
| 15 | **Data snapshots** | JSON files for trend detection (revenue, metrics) | None | Small |
| 16 | **Daily activity logs** | logs/YYYY-MM-DD/global.log readable by crons | structlog only (not cron-readable) | Small |
| 17 | **Draft/approval flow** | Presents draft, asks confirmation, then executes | Hard-blocks write actions | Small |
| 18 | **Emoji reactions** | Uses reactions as acknowledgments | None | Tiny |
| 19 | **Custom cron creation** | Lucy can create her own crons (self-improving) | None | Small |
| 20 | **Slack message streaming** | N/A (Viktor uses Slack differently) | `chat_stream()` available in Bolt SDK | Small |

### NICE TO HAVE — Can do later

| # | Feature | Note |
|---|---------|------|
| 21 | Channel introductions cron (self-deleting) | Low priority |
| 22 | Slack Assistant side panel integration | Requires paid Slack plan |
| 23 | Proactive emoji reactions | Tiny effort but low impact |

---

## What Lucy Already Has That Viktor Doesn't

| Feature | Lucy | Viktor | Keep? |
|---------|------|--------|-------|
| OpenClaw self-hosted LLM | Yes — any model via OpenRouter | No — Coworker platform controls model | **Yes** |
| Composio 10K+ tools | Yes — via Composio SDK | 3,141 via Pipedream + MCP | **Yes** |
| Multi-tenant DB isolation | Yes — workspace_id scoping | No — single workspace per install | **Yes** |
| Structured JSON logging | Yes — structlog | Plain text logs only | **Yes** |
| PostgreSQL task tracking | Yes — Task model with full lifecycle | No — filesystem only | **Yes** (simplify) |
| Async-first Python | Yes — all I/O awaited | Yes — Python scripts in sandbox | **Yes** |

---

## What Needs to Happen to agent.py

The current `agent.py` (1,099 lines) is the heart of Lucy but is now broken — it imports 8 deleted modules. It needs a **full rewrite** with:

**Remove** (references to deleted modules):
- Vector memory search/sync (`from lucy.memory.vector import ...`, `from lucy.memory.sync import ...`)
- Task classifier (`from lucy.routing.classifier import ...`)
- Model tiers (`from lucy.routing.tiers import ...`)
- BM25 retrieval (`from lucy.retrieval.tool_retriever import ...`)
- Safety modules (`from lucy.core.safety import ...`)
- Timeout module (`from lucy.core.timeout import ...`)
- SLO metrics accumulation

**Keep** (working functionality):
- Multi-turn tool execution loop (but simplify)
- Thread history building from Slack
- Composio tool execution
- Tool loop detection
- Write-action guardrails (convert to draft/approval)
- Slack response sending

**Add** (new functionality):
- Read workspace skill files before every task
- Build system prompt dynamically (with skill descriptions)
- Use Composio meta-tools (5 tools only)
- Write skill file updates after tasks
- Log activity to daily log files
- Code execution via OpenClaw sandbox

---

## Composio Upgrade Path

**Current**: `composio-core` + `composio-openai` (deprecated packages)
**Target**: `composio` v0.11+ (new unified package)

Key changes:
1. Replace `composio-core` with `composio` in pyproject.toml
2. Use session-based pattern: `composio.create(user_id=workspace_id)`
3. Get 5 meta-tools via `session.tools()` instead of fetching all schemas
4. Handle tool execution via `composio.provider.handle_tool_calls()`
5. Use workspace_id (not "default") as entity_id

---

## Database Simplification

**Current**: 17 models (Workspace, User, Channel, Task, TaskStep, Approval, Schedule, Heartbeat, Pattern, CostLog, AuditLog, WebhookDelivery, Agent + more)

**Target**: 5-6 models:
- `Workspace` — tenant isolation
- `User` — workspace members
- `Channel` — tracked channels
- `Task` — task records (simplified)
- `Integration` — connected tool accounts
- `CronJob` — scheduled job records (new)

Knowledge data (company, team, skills, learnings) lives in the filesystem, not the database.
