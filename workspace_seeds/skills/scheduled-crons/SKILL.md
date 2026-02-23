---
name: scheduled-crons
description: Create and manage scheduled cron jobs for proactive automation. Use when setting up recurring tasks, heartbeats, or monitoring jobs.
---

# Scheduled Crons

Crons are how Lucy acts proactively — without being asked. Each cron is a `task.json` file in the workspace `crons/` directory that defines what to do and when. Lucy's APScheduler picks them up automatically.

## Two Execution Paths

### 1. Agent Crons (default)
Lucy receives the task description as a message and acts on it with full context (skills, meta-tools, Slack access). Use for tasks requiring judgment, multi-step reasoning, or tool use.

### 2. OpenClaw Native Crons
OpenClaw has a built-in cron system with first-class tools. Use this when you want crons managed directly within OpenClaw's runtime.

**OpenClaw cron tools:**

| Tool | Description |
|------|-------------|
| `cron.add` | Create a new cron with schedule and instructions |
| `cron.update` | Modify an existing cron's schedule or instructions |
| `cron.remove` | Delete a cron by ID |
| `cron.run` | Trigger a cron immediately |
| `cron.list` | List all crons with their status and next run |
| `cron.status` | Get detailed status and run history for a cron |

**Schedule types for OpenClaw crons:**

| Type | Example | Use For |
|------|---------|---------|
| `at` | `"at": "2026-03-01T09:00:00"` | One-time future execution |
| `every` | `"every": "2h30m"` | Fixed-interval repeating |
| `cron` | `"cron": "0 9 * * 1-5"` | Standard crontab expression |

**OpenClaw cron options:**
- `tz` — IANA timezone (e.g. `"America/Los_Angeles"`) for schedule evaluation
- `sessionTarget: "isolated"` — background cron with no shared state (recommended for most crons)
- `sessionTarget: "main"` — runs in the main session, can access ongoing context (use for reminders or context-aware tasks)
- `delivery: "announce"` — posts result to Slack (default for Lucy crons)
- `delivery: "webhook"` — sends result to a URL
- `delivery: "none"` — silent execution, results in run history only

**Invoking OpenClaw crons via API:**
```
POST /tools/invoke
{
  "tool": "cron.add",
  "args": {
    "title": "Morning Standup Reminder",
    "cron": "0 9 * * 1-5",
    "tz": "America/New_York",
    "instructions": "Remind the team in #engineering about standup...",
    "sessionTarget": "isolated",
    "delivery": "announce"
  }
}
```

**Run history:** Each cron tracks executions at `cron.runs`. On failure, OpenClaw applies exponential backoff before retry.

## Lucy's task.json Schema

```json
{
    "path": "/heartbeat",
    "cron": "30 4,7,10,13 * * *",
    "title": "Heartbeat",
    "description": "Full instructions for what Lucy should do...",
    "created_at": "2026-02-22T00:00:00Z",
    "updated_at": "2026-02-22T00:00:00Z"
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `path` | Yes | Unique identifier for this cron |
| `cron` | Yes | Cron expression (standard 5-field) |
| `title` | Yes | Human-readable name |
| `description` | Yes | Complete instructions for the agent |
| `created_at` | Yes | ISO 8601 timestamp |
| `updated_at` | Yes | ISO 8601 timestamp |

## Writing Good Task Descriptions

The Lucy instance that runs a cron has **NO context** from previous conversations. The description must contain EVERYTHING it needs:

1. **What to do** — step-by-step instructions
2. **Where to find data** — which skills to read, which Slack channels to check
3. **What format to output** — how to structure the Slack message
4. **Who to notify** — which channels or people to message
5. **When to skip** — conditions under which the cron should do nothing
6. **Timezone** — specify the timezone for any time-sensitive operations (use IANA identifiers)

## Common Cron Schedules

| Schedule | Cron Expression | Use For |
|----------|----------------|---------|
| 4x daily (9am, noon, 3pm, 6pm) | `0 9,12,15,18 * * 1-5` | Heartbeat |
| Every 2 minutes | `*/2 * * * *` | Issue monitoring |
| Mon & Thu at 10am | `0 10 * * 1,4` | Workflow discovery |
| Daily at 8am | `0 8 * * 1-5` | Morning reports |
| Weekly Monday 9am | `0 9 * * 1` | Weekly summaries |

## Creating a New Cron

1. Create the directory: `crons/{cron-name}/`
2. Write `task.json` with complete instructions
3. Optionally create `LEARNINGS.md` (accumulates across runs)
4. Optionally add scripts in `scripts/` subdirectory
5. Test by triggering it once immediately
6. After creation, Lucy's scheduler reloads automatically

## LEARNINGS.md

Each cron can have a `LEARNINGS.md` file that accumulates knowledge across runs:
- What worked and what didn't
- Edge cases discovered
- User preferences learned
- Timing adjustments needed

Lucy reads this before each cron run to avoid repeating mistakes.

## When to Use OpenClaw Crons vs task.json

| Scenario | Use |
|----------|-----|
| User asks "remind me every day at 9am" | OpenClaw `cron.add` with `delivery: "announce"` |
| Persistent workspace automation (heartbeat, discovery) | `task.json` in workspace |
| Quick one-time scheduled task | OpenClaw `cron.add` with `at` schedule |
| Complex multi-tool proactive workflow | `task.json` agent cron |

## Anti-Patterns

- Don't create crons with vague descriptions — be exhaustively specific
- Don't schedule agent crons more than 6x/day without user approval (each run costs LLM tokens)
- Don't skip the LEARNINGS.md — it's how crons get smarter over time
- Don't assume the executing instance has any conversation context
- Don't hardcode times without timezone — always include IANA timezone
- Don't use `sessionTarget: "main"` unless the cron needs access to ongoing conversation state
