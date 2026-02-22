---
name: scheduled_crons
description: Create, modify, and delete scheduled cron jobs. Use when scheduling recurring tasks on a cron.
---

Before any scheduled cron operation, always list existing cron jobs first.

## List All Scheduled Crons

```bash
find /work/crons -name "task.json" -type f 2>/dev/null | while read f; do
  echo "=== $(dirname "$f" | sed 's|/work/crons/||') ==="
  jq -r '"title: \(.title // "untitled")\ncron: \(.cron)\ndesc: \(.description // "" | .[0:100])...\ncreated: \(.created_at)\nupdated: \(.updated_at)"' "$f" 2>/dev/null || cat "$f"
  echo ""
done
```
And for relevant cron jobs also their task.json, LEARNINGS.md and execution.log.

Review this output before creating a new cron job to avoid duplicates or modifying the wrong one.

## Read Cron Details Before Modifying

```bash
cat /work/crons/{cron_name}/task.json
cat /work/crons/{cron_name}/LEARNINGS.md
cat /work/crons/{cron_name}/execution.log
ls /work/crons/{cron_name}/scripts/  # Check for reusable scripts
```

## Creating an Agent Cron (runs AI agent with prompt)

Use `create_agent_cron` for tasks that need AI reasoning:
- `path`: Cron path (e.g., `/reports/weekly`)
- `title`: Short title
- `description`: Instructions for the agent to execute on each run
- `cron`: Cron expression (e.g., `0 9 * * 1-5`)
- `slack_sender_name` (optional): Custom Slack display name for messages from this cron (e.g., `"Viktor Reports"`)
- `model` (optional): `claude-opus-4-6#ReasoningLevel:very_high` (default, recommended), `claude-sonnet-4-6`, or `gemini-3-flash-preview`
- `trigger_now`: Set `true` for immediate execution

Model guidance:
- Prefer `claude-opus-4-6#ReasoningLevel:very_high` for most agent crons.
- Use `claude-sonnet-4-6` for lower-cost routine work.
- Use `gemini-3-flash-preview` only for simple, high-volume tasks where quality trade-offs are acceptable.

```python
from sdk.tools.scheduled_crons import create_agent_cron

await create_agent_cron(
    path="/reports/weekly",
    title="Weekly Report",
    description="Generate a weekly summary of team activity",
    cron="0 9 * * 1",  # Every Monday at 9am
    model="claude-opus-4-6#ReasoningLevel:very_high",
    slack_sender_name="Weekly Reports",
    trigger_now=False,
)
```

## Creating a Script Cron (runs Python script directly)

Use `create_script_cron` for deterministic tasks that don't need AI:
- `path`: Cron path (e.g., `/cleanup/logs`)
- `title`: Short title
- `script_path`: Path to Python script in sandbox (e.g., `/work/scripts/cleanup_logs.py`)
- `cron`: Cron expression
- `trigger_now`: Set `true` for immediate execution

```python
from sdk.tools.scheduled_crons import create_script_cron

await create_script_cron(
    path="/cleanup/logs",
    title="Log Cleanup",
    script_path="/work/scripts/cleanup_logs.py",
    cron="0 0 * * *",  # Daily at midnight
    trigger_now=False,
)
```

## Conditional Execution

Agent crons are expensive — each execution runs a full AI agent. Scheduling agent crons more than ~6 times per day can get costly fast. Use `condition_script_path` whenever possible to skip unnecessary runs, or limit the cron to work hours only (e.g. `0 9-17 * * 1-5`). If neither is feasible, tell the user that the frequency could become expensive and recommend a rarer schedule.

Both agent and script crons accept an optional `condition_script_path`. The scheduler runs this script before each execution — exit 0 to run, non-zero to skip that cycle.

Use for: only run when new Slack messages appeared, business days only, Stripe revenue above a threshold, a file was updated, etc.

Condition script at `/work/scripts/conditions/has_new_slack_messages.py`:

```python
import sys
from sdk.utils.slack_reader import get_new_slack_messages
from datetime import datetime, timedelta, timezone

since = datetime.now(timezone.utc) - timedelta(hours=1)
if not get_new_slack_messages(since=since, channel_names=["sales"]):
    sys.exit(1)
```

Usage:

```python
await create_agent_cron(
    path="/sales/respond-to-leads",
    title="Respond to Sales Leads",
    description="Check #sales for new inbound leads and draft responses",
    cron="0 * * * *",  # every hour
    condition_script_path="/work/scripts/conditions/has_new_slack_messages.py",
)
```

## Modifying a Cron

Only use `create_agent_cron` or `create_script_cron` if you need to change the cron configuration. Always read the LEARNINGS.md, execution.log, and scripts/ folder to understand the cron job and its context.

If changing title/description/cron/script:
1. List cron jobs to find the exact path
2. Use the appropriate create function with same path (upserts)

## Deleting a Cron

1. List cron jobs to confirm exact path
2. Use `delete_cron`

## SDK Reference

```python
from sdk.tools.scheduled_crons import (
    create_agent_cron,   # Create cron that runs an agent
    create_script_cron,  # Create cron that runs a script
    delete_cron,         # Delete a cron job
    trigger_cron,        # Manually trigger a cron job
)
```
<!-- ══════════════════════════════════════════════════════════════════════════
     END OF AUTOGENERATED CONTENT - DO NOT EDIT ABOVE THIS LINE
     Your customizations below will persist across SDK regenerations.
     ══════════════════════════════════════════════════════════════════════════ -->
