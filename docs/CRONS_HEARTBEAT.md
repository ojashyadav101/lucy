# Crons & Heartbeat — Deep Dive

> How Lucy schedules recurring jobs, executes them through the agent pipeline,
> delivers results to Slack, and monitors external services in real time.

---

## Cron System Overview

**File:** `src/lucy/crons/scheduler.py`

Lucy's cron engine is built on APScheduler and manages two types of jobs:

| Type | How It Runs |
|------|------------|
| `agent` | Full Lucy agent pipeline — builds instruction, runs agent, delivers result |
| `script` | Executes Python script directly — runs in Composio sandbox or locally |

---

## CronConfig

```python
@dataclass
class CronConfig:
    path: str                        # Workspace-relative path ("/heartbeat")
    cron: str                        # Cron expression ("*/30 8-22 * * *")
    title: str                       # Display name
    description: str                 # What the cron should do
    workspace_dir: str               # Workspace root directory
    type: str = "agent"              # "agent" or "script"
    condition_script_path: str = ""  # Pre-condition script
    max_runs: int = 0                # 0 = unlimited
    depends_on: str = ""             # Dependency cron path
    created_at: str = ""
    updated_at: str = ""
    timezone: str = ""               # IANA timezone
    max_retries: int = 3             # Retry count on failure
    notify_on_failure: bool = True   # DM workspace owner on persistent failure
    delivery_channel: str = ""       # Target Slack channel
    requesting_user_id: str = ""     # User who created the cron
    delivery_mode: str = "channel"   # "channel" or "dm"
```

### Storage

Each cron is a `task.json` file in the workspace:

```
workspaces/{workspace_id}/crons/
├── heartbeat/
│   ├── task.json
│   └── LEARNINGS.md
├── daily-seo-report/
│   ├── task.json
│   └── LEARNINGS.md
└── weekly-competitor-analysis/
    ├── task.json
    └── LEARNINGS.md
```

`LEARNINGS.md` stores observations from past runs — the agent reads this
before each execution so it can improve over time.

---

## CronScheduler Class

### Startup: `start()`

```
Scheduler starts
    │
    ├── Discover all workspace directories
    │
    ├── For each workspace:
    │   └── For each crons/*/task.json:
    │       └── Parse CronConfig → schedule with APScheduler
    │
    └── Schedule system crons:
        ├── slack_sync (periodic Slack message sync)
        ├── memory_consolidation (promote session → knowledge)
        ├── humanize_pool_refresh (regenerate message pools)
        └── heartbeat_loop (evaluate due heartbeat monitors)
```

### Cron CRUD

| Method | Purpose |
|--------|---------|
| `create_cron(ws_id, name, cron_expr, ...)` | Create new cron: validate expression, write task.json, register |
| `delete_cron(ws_id, cron_name)` | Delete cron: remove directory + unschedule (supports fuzzy match) |
| `modify_cron(ws_id, name, new_expr?, ...)` | Update schedule, description, or title |
| `reload_workspace(ws_id)` | Reload all crons for a workspace |
| `trigger_now(ws_id, cron_path)` | Manually fire a cron (for testing) |
| `list_jobs()` | Snapshot of all scheduled jobs |

### Validation

```python
validate_cron_expression(expr) -> str | None
# Returns None if valid, error message if invalid
# Also used by create_cron() before scheduling

_estimate_daily_runs(cron_expr) -> int
# Rough estimate: warns if >50 runs/day (potential resource concern)
```

---

## Cron Execution: `_run_cron()`

This is the core execution method. Every cron fire goes through this flow:

```
Cron fires (APScheduler trigger)
    │
    ├── 1. DEPENDENCY CHECK
    │     If depends_on is set:
    │       Check if dependency cron ran successfully today
    │       If not → skip this run
    │
    ├── 2. CONDITION SCRIPT
    │     If condition_script_path is set:
    │       Execute script → check return value
    │       If script returns falsy → skip this run
    │
    ├── 3. READ LEARNINGS
    │     Load LEARNINGS.md from cron directory
    │     Contains insights from past runs
    │
    ├── 4. BUILD INSTRUCTION
    │     _build_cron_instruction(cron, learnings)
    │     Wraps description with personality framing:
    │       "You are running a scheduled task..."
    │       + description
    │       + learnings context
    │       + self-validation rules
    │       + HEARTBEAT_OK option
    │
    ├── 5. EXECUTE
    │     ├── type="agent": Full Lucy agent pipeline
    │     │     agent.run(instruction, ctx, model_override=...)
    │     └── type="script": Direct script execution
    │           execute_workspace_script(script_path)
    │
    ├── 6. RESPONSE FILTERING
    │     response.strip().upper()
    │     Skip delivery if:
    │       ├── Empty response
    │       ├── "SKIP"
    │       ├── "HEARTBEAT_OK"
    │       └── Starts with "HEARTBEAT_OK"
    │
    ├── 7. DELIVER TO SLACK
    │     _deliver_to_slack(channel, response)
    │     Channel determined by _resolve_delivery_target():
    │       ├── delivery_mode="dm" → DM to requesting_user_id
    │       ├── delivery_channel set → post to that channel
    │       └── Neither → log only (no Slack post)
    │
    ├── 8. LOG EXECUTION
    │     Activity log: "Ran {cron.title}"
    │
    ├── 9. MAX_RUNS CHECK
    │     If max_runs > 0 and runs >= max_runs:
    │       Self-delete cron
    │
    └── 10. ERROR HANDLING
          On failure:
            ├── Retry with exponential backoff (up to max_retries)
            └── If all retries fail + notify_on_failure:
                _notify_cron_failure() → DM to workspace owner
```

### Instruction Building: `_build_cron_instruction()`

The instruction the agent receives looks like:

```
You are running a scheduled task: "Daily SEO Report"

## Task
Get today's SEO performance metrics from Google Search Console
and create a summary with key changes from yesterday.

## Context from Past Runs
- Last run found that impressions data takes 2 days to finalize
- Position data for long-tail keywords fluctuates more on Mondays

## Important Rules
- This runs automatically. DO NOT ask the user anything.
- Include real data only. Never use sample/placeholder data.
- Self-validate your output before responding.
- For heartbeat check-ins, return HEARTBEAT_OK if nothing needs action.
- DO NOT create or modify other cron jobs from within this cron.
```

### Delivery to Slack: `_deliver_to_slack()`

```
Raw response text
    │
    ├── Check if response is Block Kit JSON
    │     If yes → post blocks directly
    │
    ├── Output pipeline:
    │     process_output() → format_links()
    │
    ├── Block Kit conversion:
    │     text_to_blocks() → enhance_blocks()
    │
    └── Post to Slack:
          If blocks → chat_postMessage(blocks=..., text=fallback)
          If no blocks → chat_postMessage(text=...)
```

---

## HEARTBEAT_OK Suppression

When a cron runs and finds nothing actionable, the agent responds with
`HEARTBEAT_OK`. This response is suppressed — it's never posted to Slack.

**Why:** Prevents noise. The heartbeat cron fires every 30 minutes; posting
"all clear" 48 times a day would be annoying. Users only see messages
when something actually needs attention.

**Detection:**
```python
_upper = response.strip().upper()
skip = (
    not response
    or _upper == "SKIP"
    or _upper == "HEARTBEAT_OK"
    or _upper.startswith("HEARTBEAT_OK")
)
```

---

## Heartbeat Monitor System

**File:** `src/lucy/crons/heartbeat.py`

Heartbeats are real-time condition monitors that check external services
and alert when conditions are met.

### How It Differs from Crons

| Feature | Cron | Heartbeat |
|---------|------|-----------|
| Trigger | Time-based (cron expression) | Condition-based (checked every 30s) |
| Execution | Full agent pipeline | Direct HTTP/script evaluation |
| Output | Posted to Slack always | Alert only when condition fires |
| Use case | Reports, syncs, maintenance | "Alert me when X happens" |
| Resource cost | Full LLM call per run | Lightweight HTTP checks |

### Heartbeat DB Model

Stored in PostgreSQL via SQLAlchemy:

| Field | Type | Purpose |
|-------|------|---------|
| `id` | UUID | Primary key |
| `workspace_id` | UUID FK | Owner workspace |
| `name` | String | Display name |
| `condition_type` | String | Type of check |
| `condition_config` | JSONB | Check parameters |
| `check_interval_seconds` | Integer | How often to check (default: 300) |
| `last_checked_at` | DateTime | Last evaluation time |
| `last_result` | JSONB | Last check result |
| `status` | Enum | active/paused/triggered/error |
| `alert_template` | String | Alert message template |
| `alert_cooldown_seconds` | Integer | Min time between alerts (default: 3600) |
| `last_alerted_at` | DateTime | Last alert time |
| `consecutive_failures` | Integer | Error counter |
| `description` | String | Human description |

### Condition Types

#### 1. `api_health` — HTTP Health Check

```python
config = {
    "url": "https://api.example.com/health",
    "expected_status": 200,         # default: 200
    "timeout": 15,                  # seconds
    "_slack_alert_channel": "C01234567"  # where to post alerts
}
```

**Evaluator:** `_eval_api_health(config)`
- Makes HTTP GET request
- Triggers if status code != expected_status
- Triggers if request times out or connection fails

#### 2. `page_content` — Content Check

```python
config = {
    "url": "https://store.example.com/product/123",
    "contains": "In Stock",          # trigger if text found
    "not_contains": "Out of Stock",  # trigger if text found
    "regex": "price.*\\$\\d+",       # trigger if regex matches
    "_slack_alert_channel": "C01234567"
}
```

**Evaluator:** `_eval_page_content(config)`
- Fetches page HTML
- Checks for text presence/absence
- Checks regex match
- Triggers based on conditions

#### 3. `metric_threshold` — Numeric Threshold

```python
config = {
    "url": "https://api.example.com/metrics",
    "json_path": "data.active_users",  # dot-separated path into JSON
    "operator": "<",                    # >, <, >=, <=, ==, !=
    "threshold": 100,
    "_slack_alert_channel": "C01234567"
}
```

**Evaluator:** `_eval_metric_threshold(config)`
- Fetches JSON from URL
- Navigates to value via `json_path`
- Compares against threshold using operator

#### 4. `custom` — Custom Script

```python
config = {
    "script_path": "scripts/check_inventory.py",
    "workspace_id": "ws_123",
    "_slack_alert_channel": "C01234567"
}
```

**Evaluator:** `_eval_custom(config)`
- Executes Python script
- Script must return JSON with `"triggered": true/false`
- Triggered if script returns `"triggered": true`

### Evaluation Loop

```
evaluate_due_heartbeats(slack_client) — runs every 30 seconds
    │
    ├── Query all heartbeats where:
    │     status = "active"
    │     last_checked_at + check_interval_seconds < now
    │
    ├── For each due heartbeat:
    │   ├── Select evaluator by condition_type
    │   ├── Run evaluator → check_result
    │   ├── Update last_checked_at, last_result
    │   │
    │   ├── If triggered:
    │   │   ├── Check cooldown (last_alerted_at + cooldown < now?)
    │   │   ├── If cooldown passed:
    │   │   │   ├── _send_alert(hb, result, slack_client)
    │   │   │   └── Update last_alerted_at
    │   │   └── If in cooldown → skip alert
    │   │
    │   └── If error:
    │       ├── Increment consecutive_failures
    │       └── If consecutive_failures >= 3:
    │             Set status = "error"
    │
    └── Return count of heartbeats evaluated
```

### Alert Delivery: `_send_alert()`

```
Heartbeat condition triggered
    │
    ├── Determine alert channel:
    │     Priority: condition_config["_slack_alert_channel"]
    │     Fallback: workspace default channel
    │
    ├── Format alert message:
    │     Template: "Condition triggered: {name}"
    │     + check result details
    │
    └── Post to Slack via chat_postMessage
```

### CRUD Functions

| Function | Purpose |
|----------|---------|
| `create_heartbeat(ws_id, name, type, config, ...)` | Create monitor in DB |
| `delete_heartbeat(ws_id, name)` | Delete by name (supports fuzzy match) |
| `list_heartbeats(ws_id)` | List all monitors for workspace |
| `evaluate_due_heartbeats(slack_client)` | Run evaluation loop (called by cron) |

### How Lucy Decides: Heartbeat vs Cron

The decision tree is documented in `prompts/modules/tool_use.md`:

```
User request arrives
    │
    ├── Is it time-sensitive ("as soon as", "instantly", "immediately")?
    │     ├── Yes → Heartbeat (check_interval = 30-300 seconds)
    │     └── No → Continue evaluation
    │
    ├── Is it a periodic report ("daily", "every morning", "weekly")?
    │     └── Yes → Cron job
    │
    ├── Is it monitoring a specific condition?
    │     ├── API health check → Heartbeat (api_health)
    │     ├── Page content change → Heartbeat (page_content)
    │     ├── Metric threshold → Heartbeat (metric_threshold)
    │     └── Complex analysis → Cron job (agent type)
    │
    └── Default: Cron job (more flexible, handles complex logic)
```

---

## Learnings System

Each cron has a `LEARNINGS.md` file that accumulates knowledge from past
runs:

```markdown
## Observations
- GSC data for the current day is often incomplete; use yesterday's data
- Impressions spike on Tuesdays (product launch day)
- Position data for branded keywords is stable; focus on non-branded

## Failures
- 2026-02-20: GSC API returned 503, resolved on retry
- 2026-02-22: Rate limited by API, added 2s delay between requests
```

The agent reads this before each run and uses it to:
- Avoid known failure modes
- Adjust data collection strategies
- Improve output quality over time

---

## System Crons

These crons are registered at startup, not stored in workspaces:

| Cron | Schedule | Purpose |
|------|----------|---------|
| `slack_sync` | Every 15 minutes | Sync Slack messages to filesystem |
| `memory_consolidation` | Every 6 hours | Promote session facts to knowledge |
| `humanize_pool_refresh` | Every 6 hours | Regenerate message pools |
| `heartbeat_loop` | Every 30 seconds | Evaluate due heartbeat monitors |

---

## Cross-System Effects

| If You Change... | Also Check... |
|-----------------|---------------|
| Cron config schema | `task.json` format in all workspaces |
| `_run_cron()` flow | Agent `run()` method (cron uses `is_cron_execution=True`) |
| HEARTBEAT_OK suppression | `_build_cron_instruction()` (tells agent about it) |
| Heartbeat condition types | `_eval_*` functions, DB model schema |
| Alert delivery | `_send_alert()`, `_slack_alert_channel` in config |
| Evaluation loop timing | Startup `heartbeat_loop` cron schedule |
| Delivery formatting | `_deliver_to_slack()`, output pipeline, Block Kit |
| `LEARNINGS.md` format | `_build_cron_instruction()` (reads it) |
