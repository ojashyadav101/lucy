---
name: scheduled-crons
description: Create and manage scheduled cron jobs for proactive automation. Use when setting up recurring tasks, heartbeats, or monitoring jobs.
---

# Scheduled Crons

Crons are how Lucy acts proactively — without being asked. Each cron is a `task.json` file that defines what to do and when.

## Cron Types

### Agent Crons
Lucy receives the task description as a message and acts on it with full context (skills, tools, Slack access).

### Script Crons
A Python script runs on schedule. Good for data collection, simple checks, and API polling.

## task.json Schema

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
| `description` | Yes | Complete instructions for the agent or script path |
| `created_at` | Yes | ISO 8601 timestamp |
| `updated_at` | Yes | ISO 8601 timestamp |

## Writing Good Task Descriptions

The Lucy instance that runs a cron has **NO context** from previous conversations. The description must contain EVERYTHING it needs:

1. **What to do** — step-by-step instructions
2. **Where to find data** — which skills to read, which Slack channels to check
3. **What format to output** — how to structure the Slack message
4. **Who to notify** — which channels or people to message
5. **When to skip** — conditions under which the cron should do nothing

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

## LEARNINGS.md

Each cron can have a `LEARNINGS.md` file that accumulates knowledge across runs:
- What worked and what didn't
- Edge cases discovered
- User preferences learned
- Timing adjustments needed

Lucy reads this before each cron run to avoid repeating mistakes.

## Anti-Patterns

- Don't create crons with vague descriptions
- Don't schedule agent crons more than 6x/day without user approval (each run costs LLM tokens)
- Don't skip the LEARNINGS.md — it's how crons get smarter over time
- Don't assume the executing instance has any conversation context
