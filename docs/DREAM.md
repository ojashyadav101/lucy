# Lucy — Dream Doc (v2)

> Updated: Feb 22, 2026 — After deep research into Viktor, OpenClaw, Composio, and Slack Bolt.

## The North Star

**Lucy's intelligence is not in the model. It's in the filesystem, the scheduling, and the instructions that force her to look for ways to help.**

The model is a commodity. What makes an AI coworker useful:

1. **Persistent memory** via plain files that accumulate over days/weeks
2. **Proactive behavior** via scheduled crons that run without being asked
3. **Detailed instructions** via a system prompt + skill files that define *how* to think
4. **Code execution** that grounds every fact in real API calls and computation
5. **A read-write discipline** that ensures every interaction makes Lucy smarter

---

## What Lucy Must Be

Lucy is a Slack-native AI coworker that:

- **Day 1**: Profiles every team member, explores every connected integration, creates skill files for each, sets up heartbeat and monitoring crons
- **Day 3**: Heartbeat runs 4x/day, issue monitor watches channels, team members receive personalized workflow proposals
- **Day 7**: 50+ lines of accumulated LEARNINGS.md, 5+ active cron jobs, team members use Lucy without prompting
- **Day 30**: Knows each person's communication style, work hours, pet peeves. Catches issues before they're reported. Automates recurring tasks.

### Personality (from SOUL.md — preserved)

- Direct because she respects people's time
- Warm without being sycophantic
- Admits uncertainty rather than bullshitting
- Pushes back when something doesn't make sense

### What Lucy Is NOT

- A chatbot that answers and forgets
- A passive system that only activates when @mentioned
- An over-engineered pile of infrastructure that doesn't help anyone

---

## The Tech Stack (Confirmed by Research)

### What We Use and Why

| Component | Technology | Role | Status |
|-----------|-----------|------|--------|
| **Slack** | Slack Bolt `AsyncApp` + Socket Mode | Primary interface, event handling, proactive messaging | Working |
| **LLM Gateway** | OpenClaw on VPS | LLM calls (OpenAI-compat API), code execution sandbox (Docker) | Working |
| **Tool Integrations** | Composio SDK v0.11+ (session + meta-tools) | 10,000+ tools via 5 meta-tools, per-user OAuth | Needs upgrade |
| **Database** | PostgreSQL + async SQLAlchemy | Operational data only (workspaces, users, tasks) | Working |
| **Scheduling** | APScheduler `AsyncIOScheduler` + croniter | Heartbeat, monitors, cron jobs | To build |
| **Workspace Memory** | Plain markdown files on local filesystem | Skills, learnings, team/company knowledge | To build |
| **Logging** | structlog (JSON) + daily log files | Structured logging + cron-readable activity logs | Partial |
| **HTTP** | httpx (async) | External API calls | Working |

### What We Removed and Why

| Removed | Reason |
|---------|--------|
| Qdrant + Mem0 (vector memory) | Viktor proves plain files + grep is simpler and more effective |
| LiteLLM + RouteLLM (model routing) | Unnecessary. OpenClaw handles model selection. |
| BM25 tool retrieval | Wrong approach. Composio meta-tools + skill descriptions solve this better. |
| SLO evaluation module | Premature. Build the product first, measure later. |
| Per-tool timeout budgets | Over-engineering. Basic retry-with-backoff is sufficient. |
| Circuit breaker (complex) | Simplified to tenacity retry-with-backoff. |

---

## How Tool Calling Works (The Integration Problem — Solved)

This was Lucy's biggest pain point. Here's how we fix it.

### The Problem

Lucy was passing too many Composio tool schemas to the LLM (via BM25 retrieval), causing:
- LLM confusion with 50+ tool definitions
- False claims of "I don't have access to Gmail" when it does
- Inconsistent tool selection
- This would get **worse** at 500+ integrations, not better

### Viktor's Solution

Viktor never passes 150+ schemas to the LLM. Instead:

1. **Skill descriptions** in the system prompt tell the LLM what capabilities exist
2. **Skill files** provide detailed instructions when the LLM reads them
3. **Python scripts** call SDK-generated wrappers directly
4. The LLM writes code → executes it → reports results

### Lucy's Solution (Hybrid: Viktor's Skills + Composio's Meta-Tools)

We combine the best of both:

**Layer 1: Skill Descriptions (from Viktor)**
- Every integration gets a `SKILL.md` with YAML frontmatter
- Descriptions are auto-injected into the system prompt's `<available_skills>` section
- The LLM knows "I have access to Google Calendar, Linear, GitHub..." from the prompt
- When it needs to use one, it reads the full skill file for implementation details

**Layer 2: Composio Meta-Tools (5 tools, not 500)**
- `COMPOSIO_SEARCH_TOOLS` — discovers specific tools by use-case
- `COMPOSIO_MANAGE_CONNECTIONS` — generates OAuth links when user isn't connected
- `COMPOSIO_MULTI_EXECUTE_TOOL` — executes up to 20 tools in parallel
- `COMPOSIO_REMOTE_WORKBENCH` — runs Python in a persistent sandbox
- `COMPOSIO_REMOTE_BASH_TOOL` — runs bash commands

The LLM only ever sees these 5 meta-tools. When it needs "create a GitHub issue", it:
1. Sees `integrations/github` in `<available_skills>` → knows it has GitHub
2. Calls `COMPOSIO_SEARCH_TOOLS("create github issue")` → gets the exact schema
3. Calls `COMPOSIO_MULTI_EXECUTE_TOOL` → executes the tool

This scales to 10,000 integrations without confusion because the LLM never sees more than 5 tool definitions.

**Layer 3: OpenClaw Code Execution (for data grounding)**
- When facts need computation (MRR, analytics, data processing), Lucy writes Python
- OpenClaw's Docker sandbox executes the script
- Results are grounded in real data, not generated text

### Why This Beats BM25 Retrieval

| Old Approach (BM25) | New Approach (Skills + Meta-Tools) |
|---------------------|-----------------------------------|
| BM25 selects top-K tool schemas | LLM sees skill descriptions → knows what it has |
| LLM gets 15-30 tool definitions per call | LLM gets 5 meta-tools per call |
| False "no access" hallucinations | Skill descriptions confirm what's connected |
| Breaks at 500+ integrations | Scales to 10,000 (Composio's actual catalog) |
| Tool schemas are opaque | Skill files document what works, what's broken, workarounds |

---

## Competitive Edge Over Viktor

Once at parity:

1. **OpenClaw sandbox** — Viktor runs on Coworker's platform. We control the execution environment.
2. **Self-hosted LLM routing** — We choose the model (Kimi K2.5, Claude, GPT-4, local Ollama).
3. **Open architecture** — Viktor is $50-$5,000/mo SaaS. Lucy can be self-hosted for free.
4. **Composio's catalog** — 10,000 tools vs Viktor's ~3,141.

But these advantages are worthless until Lucy matches Viktor's core behavior. **Parity first, then differentiate.**
