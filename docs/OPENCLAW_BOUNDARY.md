# OpenClaw Boundary — What's Ours vs What's Theirs

> Definitive reference for what Lucy builds natively vs what comes
> from OpenClaw. Read this when confused about "do we use X from
> OpenClaw or did we build it?"

---

## The Short Answer

Lucy is a **standalone application** that happens to use OpenClaw's
Gateway for VPS operations. Everything in the critical path (LLM calls,
agent loop, Slack, workspace, crons, heartbeats, tools) is **native
Lucy code** talking to third-party APIs directly.

---

## Naming Confusion

**`OpenClawClient`** in `src/lucy/core/openclaw.py` is **NOT** an
OpenClaw integration. It is an HTTP client that calls **OpenRouter**
(`https://openrouter.ai/api/v1`). The name is historical.

From the file header:
> "Routes all requests through OpenRouter (openrouter.ai/api/v1).
> OpenClaw stripped tool parameters, so we bypass it entirely."

The `.cursor/rules/openclaw.mdc` rule confirms:
> "Requests go to OpenRouter, not directly to OpenClaw."

**When you see `OpenClawClient` in the code, read it as `LLMClient`.**

---

## What Lucy Uses FROM OpenClaw Gateway

**Client file:** `src/lucy/integrations/openclaw_gateway.py`
**Endpoint:** `POST /tools/invoke` at `settings.openclaw_base_url`

| Feature | Gateway Tool | Used By |
|---------|-------------|---------|
| Shell execution on VPS | `exec_command()` | MCP server installs |
| Background processes | `start_background()`, `poll_process()`, `kill_process()` | Long-running VPS tasks |
| File operations on VPS | `write_file()`, `read_file()`, `edit_file()` | Available but rarely used |
| Web page fetching | `web_fetch()` | Available for URL fetching |
| Health check | `session_status` tool | Gateway availability check |
| Capability probe | `check_coding_tools()` | Detecting available VPS tools |

**Primary consumer:** `src/lucy/integrations/mcp_manager.py` (installing
and running MCP servers on the VPS).

**Not in critical path:** The agent loop, LLM calls, tool execution,
and Slack handlers never depend on the Gateway being available.

---

## What Lucy Builds NATIVELY (Not From OpenClaw)

| Feature | Lucy's Implementation | Why Not OpenClaw? |
|---------|----------------------|-------------------|
| **LLM calls** | `core/openclaw.py` → OpenRouter API | OpenClaw strips tool parameters |
| **Agent loop** | `core/agent.py` — multi-turn with supervisor | Custom planning + escalation logic |
| **Heartbeat monitors** | `crons/heartbeat.py` — condition-based polling | More controllable, no LLM per check |
| **Cron system** | `crons/scheduler.py` — APScheduler | Direct Slack delivery, custom execution |
| **Workspace filesystem** | `workspace/filesystem.py` — local directory per workspace | Need custom structure + atomic writes |
| **Skills system** | `workspace/skills.py` — SKILL.md files with YAML frontmatter | Custom trigger matching + injection |
| **Memory** | `workspace/memory.py` — three-tier (thread/session/knowledge) | Custom classification + persistence |
| **Composio integration** | `integrations/composio_client.py` — direct SDK | Need meta-tool architecture + auto-repair |
| **Slack handlers** | `slack/handlers.py` — Slack Bolt | OpenClaw doesn't do Slack natively |
| **Database** | `db/models.py` — SQLAlchemy + PostgreSQL | Need multi-tenant workspace models |
| **Sub-agents** | `core/sub_agents.py` — shared LLM client | Need custom delegation + context trimming |
| **Output pipeline** | `pipeline/output.py` — 4-layer processing | Personality-preserving post-processing |
| **Rate limiting** | `infra/rate_limiter.py` — token bucket | Per-model + per-API control |
| **Request queue** | `infra/request_queue.py` — priority queue | Per-workspace fairness |
| **Email** | `integrations/agentmail_client.py` + `email_listener.py` | Native email identity (zeeyamail.com) |
| **Spaces (app builder)** | `spaces/` — Convex + Vercel deployment | Custom deployment pipeline |
| **CamoFox browser** | `integrations/camofox.py` | Anti-detection headless browser |

---

## OpenClaw Features Available But NOT Used

These exist in OpenClaw but Lucy builds its own equivalent or doesn't
need them:

| OpenClaw Feature | Lucy's Alternative | Status |
|-----------------|-------------------|--------|
| **Lobster workflow runtime** | Supervisor + agent loop | Not needed — Lucy's supervisor is sufficient |
| **Workspace files API** | `workspace/filesystem.py` | Not used — Lucy manages files locally |
| **Sessions API** | Database-backed sessions | Not used — Lucy uses PostgreSQL |
| **Hooks/Webhooks API** | Potential future use | Not used yet — see `docs/openclaw/hooks.md` |
| **Engram memory** | Three-tier memory system | Not used — field exists in response but unpopulated |
| **Skill system** | `workspace/skills.py` | Not used — Lucy has its own SKILL.md format |
| **Multi-agent routing** | `core/sub_agents.py` | Not used — Lucy handles delegation internally |
| **Heartbeat API** | `crons/heartbeat.py` | Not used — Lucy's native heartbeat is more controllable |

---

## When to Use OpenClaw vs Build Native

| Use OpenClaw When... | Build Native When... |
|---------------------|---------------------|
| Need to execute commands on VPS | Need fine-grained control over behavior |
| Need background process management | Need to integrate with Slack directly |
| Installing/managing MCP servers | Need custom retry/escalation logic |
| Need future sandbox isolation | Need workspace-level isolation |
| Feature is a thin wrapper around exec | Feature needs database state |

---

## Integration Points

```
Lucy Application
├── LLM Calls ──────────────────→ OpenRouter API (direct)
├── Tool Execution ─────────────→ Composio SDK (direct)
├── Slack Events ───────────────→ Slack API (Slack Bolt)
├── Email ──────────────────────→ AgentMail API (direct)
├── Database ───────────────────→ PostgreSQL (SQLAlchemy)
├── VPS Operations ─────────────→ OpenClaw Gateway (/tools/invoke)
│   ├── exec_command
│   ├── start_background / poll / kill
│   ├── write_file / read_file
│   └── web_fetch
└── App Deployment ─────────────→ Vercel + Convex APIs (direct)
```

---

## OpenClaw Docs Reference

Local copies of relevant OpenClaw documentation are stored in
`docs/openclaw/` for offline reference. These cover only the features
Lucy uses or might use in the future. See the
[README](./openclaw/README.md) for what's included.
