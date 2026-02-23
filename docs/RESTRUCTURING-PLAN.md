# Lucy Restructuring Plan v2 — Viktor Parity

> Updated: Feb 22, 2026 — Incorporates OpenClaw, Composio, Slack Bolt research + post-cleanup state.

## Current State

After cleanup: **23 source files, 6,452 lines**. The dead-weight modules (vector memory, BM25 retrieval, model routing, SLOs) are deleted. What remains is a working Slack connection, OpenClaw client, Composio client, and PostgreSQL layer.

`agent.py` is **broken** — it imports 8 deleted modules. This is intentional. We rewrite it in Phase 2.

---

## Phase 1: Foundation — Workspace, Skills, Prompt (Build First)

### Goal
Build the workspace filesystem, skill system, and system prompt that are the foundation for everything else.

### 1.1 Workspace filesystem module

**New file**: `src/lucy/workspace/__init__.py`, `src/lucy/workspace/filesystem.py`

```python
class WorkspaceFS:
    """Manages the persistent workspace directory for a Slack workspace."""

    def __init__(self, workspace_id: str, base_path: Path):
        self.root = base_path / workspace_id

    async def ensure_structure(self) -> None:
        """Create the standard directory tree."""
        # company/, team/, skills/, crons/, scripts/, data/, logs/, state/

    async def read_file(self, relative_path: str) -> str: ...
    async def write_file(self, relative_path: str, content: str) -> None: ...
    async def list_dir(self, relative_path: str) -> list[str]: ...
    async def search(self, query: str, directory: str = ".") -> list[str]:
        """grep -rn query directory — plain text search."""
```

Config: `LUCY_WORKSPACE_BASE_PATH` (default: `~/.lucy/workspaces/` for dev, `/data/workspaces/` for prod).

### 1.2 Skill system

**New file**: `src/lucy/workspace/skills.py`

```python
@dataclass
class SkillInfo:
    name: str
    description: str
    path: str

async def list_skills(workspace_id: str) -> list[SkillInfo]:
    """Parse YAML frontmatter from all SKILL.md files."""

async def read_skill(workspace_id: str, skill_name: str) -> str:
    """Read full content of a skill file."""

async def write_skill(workspace_id: str, skill_name: str, content: str) -> None:
    """Create or update a skill file."""

async def get_skill_descriptions_for_prompt(workspace_id: str) -> str:
    """Format all skill descriptions for system prompt injection."""
    # Returns:
    # - integrations/linear: "Linear project management. Use when creating issues."
    # - general-tools: "Search the web, send emails. Use when a task needs general tools."
    # ...
```

### 1.3 Pre-seed platform skills

**New directory**: `workspace_seeds/skills/` containing adapted versions of Viktor's 18 platform skills.

Each adapted from `reference/viktor/skills/`:
- Replace "Viktor" with "Lucy"
- Replace SDK tool references with Composio meta-tool patterns
- Keep the structure, philosophy, instructions intact

Skills to port: browser, codebase-engineering, docx-editing, excel-editing, general-tools, integrations (master), pdf-creation, pdf-form-filling, pdf-signing, pptx-editing, remotion-video, scheduled-crons, skill-creation, slack-admin, thread-orchestration, workflow-discovery

Plus new: `lucy-account` (replaces viktor-account)

### 1.4 System prompt assembler

**New file**: `src/lucy/core/prompt.py`

```python
async def build_system_prompt(workspace_id: str) -> str:
    """Build the ~3,000 word system prompt with 6 sections."""
    # 1. Load template from assets/SYSTEM_PROMPT.md
    # 2. Generate <available_skills> from skill descriptions
    # 3. Inject workspace-specific context (timezone, company name)
    # 4. Return assembled prompt
```

**New file**: `assets/SYSTEM_PROMPT.md` — the template (~3,000 words):
- `<core_philosophy>` — skills are memory, scripts are hands, be proactive
- `<skills_system>` — YAML frontmatter, read/update discipline
- `<work_approach>` — deep investigation, scripting, quality checks
- `<communicating_with_humans>` — Slack is your only voice
- `<operating_rules>` — don't guess, verify, log, clean up
- `<available_skills>` — `{available_skills}` placeholder

### 1.5 Composio upgrade

**Update** `pyproject.toml`:
- Remove: `composio-core`, `composio-openai`
- Add: `composio>=0.11,<1`

**Rewrite** `src/lucy/integrations/composio_client.py`:
- Use the new session-based API: `composio.create(user_id=workspace_id)`
- Get 5 meta-tools via `session.tools()`
- Handle tool calls via `composio.provider.handle_tool_calls()`
- Remove the old `get_tools()` / `execute()` pattern

### 1.6 Onboarding flow

**New file**: `src/lucy/workspace/onboarding.py`

Triggered on first message from a new workspace:
1. Create workspace directory structure (1.1)
2. Copy pre-seeded platform skills (1.3)
3. Fetch team members via Slack API (`users.list`)
4. Create `team/SKILL.md` with names, emails, roles
5. Research company (from email domains + Slack channel names)
6. Create `company/SKILL.md` with company data
7. Explore connected integrations (via Composio)
8. Create `skills/integrations/{name}/SKILL.md` for each connected app
9. Set up default crons (heartbeat, issue monitor, workflow discovery)

### Deliverable
Workspace filesystem + skill system + system prompt + Composio meta-tools + onboarding.

---

## Phase 2: Agent Rewrite (The Core Loop)

### Goal
Rewrite `agent.py` from scratch. The new agent is simple: read skills → build prompt → call LLM with meta-tools → execute tools → update skills.

### 2.1 New agent.py (~400 lines, down from 1,099)

```python
class LucyAgent:
    async def execute_task(self, task_id: UUID) -> TaskStatus:
        # 1. Load task + workspace context from DB
        # 2. Read workspace skills (company, team, relevant skills)
        # 3. Build system prompt via prompt.py (with skill descriptions)
        # 4. Build conversation from Slack thread history
        # 5. Get Composio meta-tools (5 tools) for this workspace
        # 6. Multi-turn LLM loop:
        #    a. Call OpenClaw with messages + tools
        #    b. If tool calls → execute via Composio → append results → continue
        #    c. If text response → done
        # 7. Send response to Slack
        # 8. Update skill files if new knowledge learned
        # 9. Log activity to daily log
```

Key changes from old agent:
- **No BM25 retrieval** — skill descriptions in prompt + 5 Composio meta-tools
- **No model tier selection** — single model via OpenClaw
- **No memory search** — read relevant skill files before the call
- **No SLO/metrics accumulation** — basic structlog only
- **No staged K expansion** — not needed with meta-tools
- **Simpler write-action handling** — draft/approval pattern instead of hard block

### 2.2 Slack response sending (actually works)

Current `_send_result_to_slack()` just logs. Make it actually post to Slack:
```python
async def _send_to_slack(self, ctx: TaskContext, text: str) -> None:
    from slack_bolt.async_app import AsyncApp
    await app.client.chat_postMessage(
        channel=ctx.slack_channel_id,
        thread_ts=ctx.slack_thread_ts,
        text=text,
    )
```

### 2.3 Simplify router.py

Remove all tier logic. Single path:
```python
async def call_llm(messages, tools=None, temperature=0.7) -> LLMResponse:
    """Call OpenClaw with messages and optional tools."""
    # POST to OpenClaw /v1/chat/completions
    # Parse response (content + tool_calls)
```

### Deliverable
A working agent that reads skills, calls LLM via OpenClaw, executes tools via Composio meta-tools, and responds in Slack.

---

## Phase 3: Cron System — Proactivity Engine

### Goal
Build the scheduling system that makes Lucy proactive. This is the single highest-impact feature for matching Viktor.

### 3.1 Cron scheduler

**New file**: `src/lucy/crons/__init__.py`, `src/lucy/crons/scheduler.py`

Uses APScheduler's `AsyncIOScheduler` (runs on the same event loop as Slack Bolt):

```python
class CronScheduler:
    def __init__(self):
        self.scheduler = AsyncIOScheduler()

    async def start(self, workspaces: list[str]) -> None:
        """Load crons from all workspace task.json files, schedule them."""
        for ws_id in workspaces:
            crons = await self._load_workspace_crons(ws_id)
            for cron in crons:
                self.scheduler.add_job(
                    self._run_cron,
                    CronTrigger.from_crontab(cron.schedule),
                    args=[ws_id, cron],
                    id=f"{ws_id}:{cron.path}",
                )
        self.scheduler.start()

    async def _run_cron(self, workspace_id: str, cron: CronConfig) -> None:
        """Execute a cron — either agent cron or script cron."""
        if cron.is_script:
            # Execute Python script via OpenClaw sandbox
            ...
        else:
            # Spin up a Lucy agent instance with cron.description as the user message
            # Include workspace context (skills, learnings)
            ...
```

### 3.2 task.json schema (identical to Viktor)

```json
{
    "path": "/heartbeat",
    "cron": "30 4,7,10,13 * * *",
    "title": "Heartbeat",
    "description": "2,000 words of instructions for the AI instance...",
    "created_at": "2026-02-14T11:42:16Z",
    "updated_at": "2026-02-14T11:42:16Z"
}
```

### 3.3 Port heartbeat cron

Copy Viktor's heartbeat task.json (2,000 words) and adapt:
- Replace `from sdk.utils.slack_reader import ...` with Lucy's Slack API calls
- Replace `from sdk.utils.heartbeat_logging import ...` with Lucy's logging
- Keep the philosophy: "be VISIBLY helpful, not invisible", "do at least one proactive action"

### 3.4 Port issue monitor cron

Copy Viktor's mentions-issue-monitor task.json and adapt.

### 3.5 Port workflow discovery cron

Copy Viktor's workflow_discovery task.json (points to workflow-discovery skill).

### 3.6 Slack message reader

**New file**: `src/lucy/workspace/slack_reader.py`

```python
async def get_new_messages(workspace_id: str, since: str, channels: list[str]) -> list[Message]:
    """Fetch new Slack messages since timestamp across channels."""
    # Uses Slack Bolt client.conversations_history()
    # Rate limit: 50 req/min (Tier 3) — generous for monitoring
```

### 3.7 Activity logging

**New file**: `src/lucy/workspace/activity_log.py`

```python
async def log_activity(workspace_id: str, message: str) -> None:
    """Append to logs/YYYY-MM-DD/global.log"""

async def get_last_heartbeat_time(workspace_id: str) -> str | None:
    """Read from state/heartbeat_last.txt"""
```

### Deliverable
Lucy runs heartbeat 4x/day, monitors channels, discovers workflows. She is now proactive.

---

## Phase 4: Code Execution & Data Grounding

### Goal
Let Lucy write, execute, and save Python scripts. Ground facts in computation.

### 4.1 OpenClaw sandbox execution

**New file**: `src/lucy/workspace/executor.py`

OpenClaw supports Docker-based code execution (the `exec` tool). We use the OpenAI-compatible API with a system prompt that instructs the model to execute code:

```python
async def execute_python(workspace_id: str, code: str) -> ExecutionResult:
    """Execute Python code via OpenClaw's sandbox."""
    # Option A: Use OpenClaw's exec tool if available
    # Option B: Direct Docker execution if we have sandbox access
    # Option C: Include code execution in the agent's tool loop
    #           (the LLM writes code, COMPOSIO_REMOTE_WORKBENCH executes it)
```

**Note**: Composio also provides `COMPOSIO_REMOTE_WORKBENCH` and `COMPOSIO_REMOTE_BASH_TOOL` for sandboxed execution. This may be the simplest path — the meta-tools already include code execution.

### 4.2 Data snapshots

**New file**: `src/lucy/workspace/snapshots.py`

```python
async def save_snapshot(workspace_id, category, data) -> None:
    """Save JSON to data/{category}/YYYY-MM-DD.json"""

async def load_latest(workspace_id, category) -> dict | None:
    """Load most recent snapshot for delta calculations."""
```

### Deliverable
Lucy can execute code and persist data snapshots for trend detection.

---

## Phase 5: Polish & Integration

### Goal
Wire everything together, test end-to-end, verify Viktor parity.

### 5.1 Update app.py startup

- Start cron scheduler alongside Slack Bolt
- Initialize workspace filesystem for known workspaces
- Remove BM25 warmup, SLO endpoints

### 5.2 Update config.py

- Remove: Qdrant, Mem0, LiteLLM config
- Add: `workspace_base_path`, Composio session settings
- Update: Composio package name

### 5.3 Update pyproject.toml

Remove:
```
qdrant-client, mem0ai, litellm
composio-core, composio-openai
```

Add:
```
composio>=0.11,<1
apscheduler>=3.10,<4
pyyaml>=6.0,<7
aiofiles>=24.1,<25
tenacity>=9.0,<10
```

### 5.4 Update .cursor/rules/

Rewrite rules to reflect new architecture (filesystem memory, cron system, skill-based routing).

### 5.5 Parity checklist

Run through every item in the gap analysis and verify:
- [x] Workspace filesystem created on first message (`workspace/onboarding.py`)
- [x] 16 platform skills pre-seeded (`workspace_seeds/skills/`)
- [x] Skill descriptions auto-injected into system prompt (`core/prompt.py`)
- [x] Composio meta-tools (5 tools) exposed to LLM (`integrations/composio_client.py`)
- [x] Day 1 onboarding runs (team profile, company stub) (`workspace/onboarding.py`)
- [x] Heartbeat cron fires 4x/day weekdays (`workspace_seeds/crons/heartbeat/`)
- [ ] Issue monitor cron (not yet seeded — requires workspace-specific channel IDs)
- [x] Workflow discovery runs Mon & Thu (`workspace_seeds/crons/workflow-discovery/`)
- [x] Code execution via Composio sandbox + local fallback (`workspace/executor.py`)
- [x] LEARNINGS.md read by crons before each run (`crons/scheduler.py`)
- [x] Agent reads skills → builds prompt before acting (`core/agent.py`)
- [x] False "no access" detection and correction in agent loop (`core/agent.py`)
- [x] Daily activity logs written and readable by crons (`workspace/activity_log.py`)
- [x] Data snapshots for trend detection (`workspace/snapshots.py`)
- [x] Channel introductions cron for onboarding (`workspace_seeds/crons/channel-introductions/`)
- [x] Dead modules removed: thread_manager, blocks, registry, routing, memory, observability

---

## File-Level Change Map (Final)

### Files to CREATE
```
src/lucy/workspace/__init__.py       # Package init
src/lucy/workspace/filesystem.py     # WorkspaceFS class
src/lucy/workspace/skills.py         # Skill parser and manager
src/lucy/workspace/onboarding.py     # Day 1 onboarding flow
src/lucy/workspace/slack_reader.py   # Fetch Slack messages for crons
src/lucy/workspace/activity_log.py   # Daily logging for cron readability
src/lucy/workspace/snapshots.py      # Data snapshot system
src/lucy/workspace/executor.py       # Code execution via sandbox
src/lucy/crons/__init__.py           # Package init
src/lucy/crons/scheduler.py          # APScheduler-based cron system
src/lucy/core/prompt.py              # System prompt builder
assets/SYSTEM_PROMPT.md              # 3,000 word system prompt template
workspace_seeds/skills/              # 18 platform skill SKILL.md files
workspace_seeds/crons/               # Default cron task.json files
```

### Files to REWRITE
```
src/lucy/core/agent.py               # Full rewrite (~1,099 → ~400 lines)
src/lucy/integrations/composio_client.py  # Upgrade to composio v0.11+ session API
src/lucy/routing/router.py           # Simplify to single call_llm() function
src/lucy/config.py                   # Remove dead config, add new settings
src/lucy/app.py                      # Add cron scheduler startup, remove dead endpoints
```

### Files to KEEP (minor updates only)
```
src/lucy/slack/handlers.py           # Update to use new agent
src/lucy/slack/middleware.py          # Keep as-is
src/lucy/slack/blocks.py             # Keep as-is
src/lucy/slack/thread_manager.py     # Keep as-is
src/lucy/db/models.py                # Simplify (remove unused models)
src/lucy/db/session.py               # Keep as-is
src/lucy/core/openclaw.py            # Keep as-is
src/lucy/core/types.py               # Keep as-is
src/lucy/integrations/registry.py    # Keep as-is
assets/SOUL.md                       # Keep (personality traits folded into system prompt)
```

### Files to DELETE (already done)
```
✓ 12 source files deleted (vector memory, BM25, routing, SLOs, etc.)
✓ 15 documentation files deleted (old summaries, test reports)
✓ 15 test files deleted (tested deleted modules)
✓ 5 script files deleted (debug/deploy scripts)
✓ 9 empty __init__.py files deleted (orphaned packages)
```

---

## Dependency Changes (Final)

### pyproject.toml

```toml
dependencies = [
    "slack-bolt>=1.21,<2",
    "fastapi>=0.115,<1",
    "uvicorn[standard]>=0.34,<1",
    "pydantic-settings>=2.7,<3",
    "sqlalchemy[asyncio]>=2.0,<3",
    "asyncpg>=0.30,<1",
    "alembic>=1.14,<2",
    "httpx>=0.28,<1",
    "structlog>=24.4,<25",
    "python-dateutil>=2.9,<3",
    "croniter>=6.0,<7",
    "composio>=0.11,<1",        # Upgraded from composio-core
    "apscheduler>=3.10,<4",     # NEW: Cron scheduling
    "pyyaml>=6.0,<7",           # NEW: YAML frontmatter parsing
    "aiofiles>=24.1,<25",       # NEW: Async file I/O
    "tenacity>=9.0,<10",        # NEW: Retry with backoff (replaces circuit breaker)
]
```

### Removed
```
qdrant-client          # Vector DB — replaced by filesystem
mem0ai                 # Memory library — replaced by filesystem
litellm                # Model routing — not needed
composio-core          # Deprecated package
composio-openai        # Deprecated package
psutil                 # Not needed
psycopg2-binary        # Not needed (asyncpg is sufficient)
```

---

## Execution Order

| Phase | What | Depends On | Effort |
|-------|------|-----------|--------|
| **1** | Workspace + Skills + Prompt + Composio upgrade | Nothing | ~2 sessions |
| **2** | Agent rewrite + Router simplification | Phase 1 | ~1 session |
| **3** | Cron system + Heartbeat + Monitors | Phase 2 | ~2 sessions |
| **4** | Code execution + Data snapshots | Phase 2 | ~1 session |
| **5** | Polish + Integration testing | All above | ~1 session |

Phases 3 and 4 can overlap.

**Total estimated effort**: 6-8 focused work sessions to reach Viktor parity.
