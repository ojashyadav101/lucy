# Workspace & Memory — Deep Dive

> How Lucy manages per-workspace filesystems, three-tier memory, skills,
> onboarding, code execution, snapshots, and Slack history.

---

## Workspace Filesystem

**File:** `src/lucy/workspace/filesystem.py`

Every Slack workspace gets its own directory tree under `settings.workspace_root`
(default: `./workspaces/`).

### Directory Structure

```
workspaces/
└── {workspace_id}/
    ├── company/
    │   └── SKILL.md          # Company profile + learned context
    ├── team/
    │   └── SKILL.md          # Team members, roles, timezones
    ├── skills/
    │   └── {skill-name}/
    │       └── SKILL.md      # Per-skill instructions
    ├── crons/
    │   └── {cron-name}/
    │       ├── task.json      # Cron configuration
    │       └── LEARNINGS.md   # What the cron learned from past runs
    ├── scripts/
    │   └── *.py               # User-created workspace scripts
    ├── data/
    │   ├── session_memory.json # Session-level facts
    │   └── {category}/
    │       └── YYYY-MM-DD.json # Snapshots
    ├── logs/
    │   ├── YYYY-MM-DD.md      # Daily activity logs
    │   └── threads/
    │       └── {thread_ts}.jsonl # Per-thread trace logs
    ├── slack_logs/
    │   ├── {channel_name}/
    │   │   └── YYYY-MM-DD.md  # Synced Slack messages
    │   └── _last_sync_ts      # Last sync timestamp
    └── state.json             # Workspace state metadata
```

### WorkspaceFS Class

| Method | Purpose |
|--------|---------|
| `ensure_structure()` | Creates directory tree, initializes `state.json` |
| `read_file(path)` | Read file, returns `None` if not found |
| `write_file(path, content)` | Atomic write (tmp → rename) |
| `append_file(path, content)` | Append to existing file |
| `delete_file(path)` | Delete file, returns success boolean |
| `list_dir(path)` | List directory contents (dirs have `/` suffix) |
| `search(query, directory)` | Plain-text grep across workspace files |
| `copy_seeds(seeds_dir, target)` | Copy seed files preserving structure |
| `read_state()` | Read `state.json` |
| `update_state(updates)` | Merge updates into `state.json` |

**Security:** `_resolve()` prevents directory traversal attacks by ensuring
resolved paths stay within the workspace root.

### Singleton Access

```python
get_workspace(workspace_id, base_path=None) -> WorkspaceFS
```

---

## Three-Tier Memory System

**File:** `src/lucy/workspace/memory.py`

Lucy has three layers of memory, each with different scope and persistence:

```
┌─────────────────────────────────────────────────────┐
│ TIER 1: THREAD MEMORY (ephemeral)                   │
│ Source: Slack thread conversation history            │
│ Scope: Single thread                                │
│ Persistence: As long as thread exists in Slack       │
│ How used: Included in LLM conversation messages      │
│ Max: 40 most recent messages                        │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│ TIER 2: SESSION MEMORY (medium-term)                │
│ Source: Facts extracted from conversations           │
│ Storage: data/session_memory.json                   │
│ Scope: Entire workspace                             │
│ Persistence: Rolling window (max 50 facts)          │
│ How used: Injected into system prompt as context     │
│                                                     │
│ Each fact:                                          │
│ {                                                   │
│   "fact": "CEO's name is Alex",                     │
│   "source": "conversation",                         │
│   "ts": "2026-02-26T09:30:00",                      │
│   "category": "team",                               │
│   "thread_ts": "1234567890.123456"                   │
│ }                                                   │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│ TIER 3: KNOWLEDGE MEMORY (permanent)                │
│ Source: Promoted from session + onboarding           │
│ Storage: company/SKILL.md, team/SKILL.md            │
│ Scope: Entire workspace                             │
│ Persistence: Permanent until manually edited         │
│ How used: Directly injected into system prompt       │
└─────────────────────────────────────────────────────┘
```

### Memory Functions

| Function | Purpose |
|----------|---------|
| `should_persist_memory(message)` | Quick check if message has facts worth saving |
| `classify_memory_target(message)` | Returns "company", "team", or "session" |
| `read_session_memory(ws)` | Load all session facts |
| `write_session_memory(ws, items)` | Save session facts (max 50) |
| `add_session_fact(ws, fact, ...)` | Add fact with deduplication |
| `get_session_context_for_prompt(ws, thread_ts?)` | Format for prompt injection |
| `append_to_company_knowledge(ws, fact)` | Promote to company SKILL.md |
| `append_to_team_knowledge(ws, fact)` | Promote to team SKILL.md |
| `consolidate_session_to_knowledge(ws)` | Auto-promote categorized facts |

### How Memory Flows

```
User says something in conversation
    │
    ├── Post-response: should_persist_memory(response)?
    │     Checks for names, preferences, facts, decisions
    │
    ├── If yes → classify_memory_target(response)
    │     ├── "company" → append_to_company_knowledge()
    │     ├── "team" → append_to_team_knowledge()
    │     └── "session" → add_session_fact()
    │
    └── Periodic: consolidate_session_to_knowledge()
          Promotes session facts with category="company"/"team"
          to permanent knowledge files
```

### Memory in the System Prompt

Session memory appears as:

```
## Recent Context
- CEO's name is Alex (from conversation, Feb 26)
- Main product is a SaaS dashboard (from conversation, Feb 25)
```

Knowledge (company/team SKILL.md) appears directly in the prompt as
permanent context that's always available.

### Concurrency Protection

Memory operations use per-workspace `asyncio.Lock` to prevent concurrent
writes from corrupting data.

---

## Skills System

**File:** `src/lucy/workspace/skills.py`

Skills are structured knowledge files that teach Lucy domain-specific
capabilities.

### Skill File Format

```markdown
---
name: SEO Analysis
description: Analyze website SEO performance
triggers: seo, search engine, rankings, keywords
---

## How to Analyze SEO Performance

1. Connect to Google Search Console
2. Pull performance data for the requested period
3. Look for: impressions, clicks, CTR, average position
...
```

**Frontmatter fields:**
- `name` — display name
- `description` — one-line summary (shown in prompt)
- `triggers` — comma-separated keywords for matching

### Skill Discovery Flow

```
User sends message
    │
    ├── detect_relevant_skills(message) → ["seo-analysis", "reporting"]
    │     Regex-matches message against trigger keywords
    │     Returns up to 3 skills sorted by match count
    │
    ├── load_relevant_skill_content(ws, message) → full skill text
    │     Loads SKILL.md content for matched skills
    │     Max 8000 chars total
    │
    └── Injected into dynamic suffix of system prompt
```

### Skill Locations

Skills are discovered from three directories:
- `skills/` — general skills
- `company/` — company-specific knowledge
- `team/` — team-specific knowledge

### SkillInfo Dataclass

```python
@dataclass
class SkillInfo:
    name: str
    description: str
    path: str            # relative path within workspace
```

### Key Functions

| Function | Purpose |
|----------|---------|
| `parse_frontmatter(content)` | Extract YAML frontmatter + body |
| `list_skills(ws)` | Discover all SKILL.md files |
| `read_skill(ws, path)` | Read specific skill |
| `write_skill(ws, name, content, subdir)` | Create/update skill |
| `get_skill_descriptions_for_prompt(ws)` | Format names + descriptions for prompt |
| `detect_relevant_skills(message)` | Regex-based skill matching |
| `load_relevant_skill_content(ws, message)` | Load full skill content for matches |
| `get_key_skill_content(ws)` | Load company + team SKILL.md content |

---

## Onboarding

**File:** `src/lucy/workspace/onboarding.py`

When a Slack workspace first interacts with Lucy, the onboarding process
scaffolds their workspace.

### `ensure_workspace()` Flow

```
First message from new workspace
    │
    ├── Check if workspace directory exists
    │     ├── Yes → return existing WorkspaceFS
    │     └── No → onboard_workspace()
    │
    └── onboard_workspace():
        1. Create directory structure (ensure_structure)
        2. Copy platform skills from workspace_seeds/skills/
        3. Copy default crons from workspace_seeds/crons/
        4. Profile team members from Slack
           ├── Fetch users via users.list API
           ├── Extract names, timezones, roles
           └── Write team/SKILL.md
        5. Create company/SKILL.md
           ├── Fetch workspace info via team.info
           └── Enrich with team name, domain
        6. Update state.json (onboarded_at, version)
        7. Reload cron scheduler (register default crons)
```

### Seed Files

Default workspace content lives in `workspace_seeds/`:

```
workspace_seeds/
├── crons/
│   └── heartbeat/
│       └── task.json       # Proactive heartbeat cron (every 30 min)
└── skills/
    └── spaces/
        └── SKILL.md        # Lucy Spaces web app building skill
```

---

## Code Execution

**File:** `src/lucy/workspace/executor.py`

Lucy can execute code through two paths:

### Execution Paths

| Path | Method | When Used |
|------|--------|-----------|
| Composio sandbox | `COMPOSIO_REMOTE_WORKBENCH` / `COMPOSIO_REMOTE_BASH_TOOL` | Default (preferred) |
| Local subprocess | Python subprocess, restricted to `scripts/` | Fallback when Composio unavailable |

### ExecutionResult

```python
@dataclass
class ExecutionResult:
    success: bool
    output: str
    error: str = ""
    exit_code: int = 0
    elapsed_ms: int = 0
    method: str = ""        # "composio" or "local"
```

### Functions

| Function | Purpose |
|----------|---------|
| `execute_python(workspace_id, code, timeout)` | Run Python code |
| `execute_bash(workspace_id, command, timeout)` | Run bash command |
| `execute_workspace_script(workspace_id, script_path, args, timeout)` | Run script from `scripts/` |

### Security

- Local execution is restricted to the workspace `scripts/` directory
- Script paths are validated to prevent directory traversal
- Timeout: `SUBPROCESS_TIMEOUT` (configurable)
- Composio sandbox provides full isolation

---

## Snapshots

**File:** `src/lucy/workspace/snapshots.py`

Snapshots store periodic data for trend analysis and comparison.

### Storage

```
data/
└── {category}/
    ├── 2026-02-24.json
    ├── 2026-02-25.json
    └── 2026-02-26.json
```

Each file:
```json
{
    "category": "seo_performance",
    "captured_at": "2026-02-26T09:00:00",
    "data": { ... }
}
```

### Functions

| Function | Purpose |
|----------|---------|
| `save_snapshot(ws, category, data, date?)` | Save data for today (or specific date) |
| `load_latest(ws, category)` | Load most recent snapshot |
| `load_snapshot(ws, category, date)` | Load specific date's snapshot |
| `compute_delta(ws, category, key, days_back)` | Compute numeric delta between today and N days ago |
| `list_categories(ws)` | List all snapshot categories |

### Delta Output

```python
{
    "current": 1250.0,
    "previous": 1180.0,
    "delta": 70.0,
    "pct_change": 5.93
}
```

---

## Activity Log

**File:** `src/lucy/workspace/activity_log.py`

Timestamped daily log of agent actions.

### Storage

```
logs/
└── 2026-02-26.md
```

Format:
```markdown
- [09:30:15] Ran SEO performance cron for MacBook Journal
- [09:45:22] Created heartbeat monitor for product page
- [10:12:08] Sent email to alex@example.com
```

### Functions

| Function | Purpose |
|----------|---------|
| `log_activity(ws, message)` | Append timestamped entry |
| `get_recent_activity(ws, days)` | Read recent log(s) |

---

## Slack History

### Sync (`src/lucy/workspace/slack_sync.py`)

Periodically syncs Slack messages to the workspace filesystem for searchable
history.

```
slack_logs/
├── general/
│   ├── 2026-02-25.md
│   └── 2026-02-26.md
├── product/
│   └── 2026-02-26.md
└── _last_sync_ts
```

| Function | Purpose |
|----------|---------|
| `sync_channel_messages(ws, slack_client, since_ts?)` | Sync messages from all channels |
| `get_last_sync_ts(ws)` | Read last sync timestamp |
| `save_last_sync_ts(ws, ts)` | Save sync checkpoint |

Sync is triggered by a cron job (registered at startup).

### Search (`src/lucy/workspace/history_search.py`)

Enables searching through synced Slack history.

```python
@dataclass
class SearchResult:
    channel: str
    date: str
    time: str
    user: str
    text: str
    line_number: int
```

| Function | Purpose |
|----------|---------|
| `search_slack_history(ws, query, ...)` | Full-text search (newest first) |
| `get_channel_history(ws, channel, date?, limit?)` | Get recent messages for channel |
| `list_available_channels(ws)` | List channels with synced history |
| `format_search_results(results)` | Format for agent context injection |

### Tool Definitions

The history module exposes OpenAI-format tool definitions:
- `lucy_search_slack_history` — search by keyword
- `lucy_get_channel_history` — get recent messages
- `lucy_list_slack_channels` — list searchable channels

---

## Timezone Handling

**File:** `src/lucy/workspace/timezone.py`

| Function | Purpose |
|----------|---------|
| `get_user_local_time(ws_id, slack_id)` | User's current local time |
| `get_user_timezone_name(ws_id, slack_id)` | IANA timezone (e.g., "America/New_York") |
| `get_all_user_timezones(ws_id)` | All users' timezone data |
| `find_best_meeting_time(ws_id, participants, start, end)` | Find overlapping working hours |

---

## Cross-System Effects

| If You Change... | Also Check... |
|-----------------|---------------|
| Workspace directory structure | `ensure_structure()`, `onboard_workspace()`, `copy_seeds()` |
| Session memory format | `get_session_context_for_prompt()`, prompt injection |
| Skill frontmatter fields | `parse_frontmatter()`, `detect_relevant_skills()` |
| Snapshot file format | `compute_delta()`, `load_latest()` |
| Slack sync message format | `search_slack_history()`, `get_channel_history()` |
| `state.json` schema | `read_state()`, `update_state()`, all state consumers |
| Seed files in `workspace_seeds/` | `onboard_workspace()` copies them for new workspaces |

---

## Filesystem Abstraction (WorkspaceFS)

**File:** `src/lucy/workspace/filesystem.py`

`WorkspaceFS` manages the persistent directory for a single Slack
workspace. All file operations are sandboxed to prevent path traversal.

### Standard Directory Structure

Created by `ensure_structure()`:

```
workspaces/{workspace_id}/
├── company/        # Company profile (SKILL.md)
├── team/           # Team member profiles (SKILL.md)
├── skills/         # User-taught and platform skills
├── crons/          # Cron job definitions (*.yaml)
├── scripts/        # Generated scripts (Python/bash)
├── data/           # Snapshots, exports, generated files
└── logs/           # Activity logs, thread traces
    └── threads/    # Per-thread JSONL trace files
```

### Core Methods

| Method | Purpose |
|--------|---------|
| `read_file(path)` | Read file, returns None if missing |
| `write_file(path, content)` | Atomic write (tmp → rename) |
| `append_file(path, content)` | Append, creates file if needed |
| `delete_file(path)` | Delete, returns True if deleted |
| `list_dir(path)` | List directory entries |
| `search(query, directory)` | Plain-text grep across files |
| `copy_seeds(seeds_dir, target)` | Copy seed files preserving structure |
| `read_state()` | Read `state.json` (general state store) |
| `update_state(updates)` | Merge updates into `state.json` |

### Path Security

`_resolve(relative_path)` ensures all paths stay within the workspace
directory. Any attempt to use `..` or absolute paths to escape is blocked.

### `state.json` Schema

General-purpose key-value state for the workspace:

```json
{
    "last_slack_sync_ts": "1708000000.000000",
    "onboarded_at": "2025-01-15T10:00:00Z",
    "crons_loaded": true,
    "entity_id": "composio-entity-id"
}
```

---

## Onboarding Flow

**File:** `src/lucy/workspace/onboarding.py`

### `onboard_workspace(workspace_id, slack_client)` — Full Sequence

```
New workspace detected (first message from this Slack team)
    │
    ├── 1. Create WorkspaceFS + ensure directory structure
    │
    ├── 2. Copy platform skills from workspace_seeds/skills/
    │     (18 skills: coding, scheduling, email, search, etc.)
    │
    ├── 3. Copy default crons from workspace_seeds/crons/
    │     (4 crons: activity_digest, slack_sync, proactive, heartbeat)
    │
    ├── 4. Profile team via Slack API (_profile_team)
    │     ├── Fetch team members (users.list)
    │     ├── Extract: name, email, tz, tz_offset, is_admin
    │     └── Write team/SKILL.md with all members
    │
    ├── 5. Create company profile (_create_company_profile)
    │     ├── Fetch workspace info (team.info)
    │     ├── Extract: name, domain, icon
    │     └── Write company/SKILL.md
    │
    └── 6. Schedule default crons via CronScheduler
```

### `ensure_workspace(workspace_id, slack_client)` — Lazy Idempotent

Used in middleware — returns existing workspace or onboards a new one.

---

## Skills System

**File:** `src/lucy/workspace/skills.py`

### SKILL.md Format

```markdown
---
name: Web Search
description: How to search the web effectively
triggers: [search, find, look up, google]
---

## Instructions

When the user asks to search the web...
```

### Frontmatter Parsing

`parse_frontmatter(content)` extracts YAML metadata and body text.
Returns `(metadata_dict, body_string)`.

### Skill Discovery

`list_skills(ws)` walks the workspace directory for all `SKILL.md` files
and returns `list[SkillInfo]` with name, description, path.

### Skill Injection

`detect_relevant_skills(message)` matches user message against compiled
trigger patterns for all skills. Returns up to 3 (`_MAX_INJECTED_SKILLS`)
skill names, sorted by match count (most relevant first).

`load_relevant_skill_content(ws, message)` loads full skill content for
detected skills, capped at 8000 characters total.

### Key Skills (Always Loaded)

`get_key_skill_content(ws)` loads `team/SKILL.md` and `company/SKILL.md`
regardless of message content — these provide essential context about
the team and company for every interaction.

---

## Slack History Search

**File:** `src/lucy/workspace/history_search.py`

Searches over locally-synced Slack message logs (not the Slack API).

### `search_slack_history(ws, query, channel, days_back, max_results)`

- Greps through `slack_logs/{channel}/{YYYY-MM-DD}.md` files
- Supports channel filtering and date range (default 30 days)
- Returns up to 30 results, newest first
- Each result: channel, date, time, user, text, line number

### `get_channel_history(ws, channel, date, limit)`

Returns recent messages from a specific channel as formatted text
(for injection into agent context).

### `list_available_channels(ws)`

Lists channels with synced history by scanning `slack_logs/` subdirs.

### Internal Tool Definitions

`get_history_tool_definitions()` returns OpenAI-format tool schemas
so the agent can call `lucy_search_slack_history` and
`lucy_get_channel_history` as tool calls.

---

## Slack Message Sync

**File:** `src/lucy/workspace/slack_sync.py`

Periodically syncs channel messages to local filesystem for search.

### `sync_channel_messages(ws, slack_client, since_ts)`

- Fetches up to 100 messages per channel (`SYNC_LIMIT_PER_CHANNEL`)
- Writes to: `slack_logs/{channel_name}/{YYYY-MM-DD}.md`
- Format: `[HH:MM:SS] <USER_ID> message text`
- Returns total messages synced

### Timestamp Tracking

`get_last_sync_ts(ws)` / `save_last_sync_ts(ws, ts)` track the last
sync point in `state.json` to avoid re-fetching messages.

### Cron Integration

The `slack_sync` default cron runs every 2 hours, calling
`sync_channel_messages()` for each workspace.

---

## Timezone Utilities

**File:** `src/lucy/workspace/timezone.py`

### `get_user_local_time(workspace_id, user_slack_id)`

Computes user's current local time from their Slack `tz_offset`
(stored in `team/SKILL.md` during onboarding).

### `get_user_timezone_name(workspace_id, user_slack_id)`

Returns IANA timezone identifier (e.g., `Asia/Kolkata`).

### `get_all_user_timezones(workspace_id)`

Returns timezone data for all team members:

```python
{
    "U123": {"name": "Ojash", "tz": "Asia/Kolkata", "tz_offset": 19800},
    "U456": {"name": "Sarah", "tz": "America/New_York", "tz_offset": -18000}
}
```

### `find_best_meeting_time(workspace_id, participant_ids, start, end)`

Finds overlapping UTC hours where all participants are within
preferred working hours (default 9 AM – 5 PM). Returns list of
valid UTC hours.

---

## Activity Logging

**File:** `src/lucy/workspace/activity_log.py`

### Daily Activity Log

`log_activity(ws, action, details)` appends to:
`logs/activity/{YYYY-MM-DD}.md`

Format: `[HH:MM:SS] ACTION: details`

### Thread Trace Logs

Written by the tracing system (`infra/trace.py`) to:
`logs/threads/{thread_ts}.jsonl`

Each line is a JSON object with trace data for one agent turn.

---

## Snapshots

**File:** `src/lucy/workspace/snapshots.py`

### `save_snapshot(ws, category, data, label)`

Saves data snapshot to: `data/snapshots/{category}/{YYYY-MM-DD}_{label}.json`

### Categories

Snapshots are organized by type:
- `analytics` — Traffic, metrics data
- `users` — User lists, subscriber data
- `reports` — Generated analysis results
- `exports` — Data exports

### Delta Computation

`compute_delta(old, new)` compares two snapshot files and returns
a diff showing additions, removals, and changes.
