---
name: async-workflows
description: Orchestrate multi-step async workflows that span hours or days. Use when a task requires waiting for external events (email replies, webhook responses, schedule triggers) and reporting back.
---

# Async Workflows

Some tasks can't complete in a single conversation turn. When a user asks
for something that requires *waiting* — for an email reply, a calendar
event, a deployment to finish, or any external event — use a heartbeat
cron to monitor and report back automatically.

## Pattern: Wait and Report Back

**Example request:** "Send an email to the client and let me know when
they reply."

**Workflow:**

1. **Immediate action** — Send the email using the appropriate tools in
   the current turn.
2. **Create a heartbeat cron** — Set up a `task.json` that monitors for
   the expected event at regular intervals.
3. **Report back** — When the heartbeat detects the event, it posts
   results to the original Slack channel.

### Step-by-step implementation:

```
Turn 1 (immediate):
  → Send the email via Gmail tools
  → Confirm to user: "Sent! I'll monitor for their reply and let you know."

Turn 2 (create heartbeat):
  → Create crons/monitor-client-reply/task.json:
    {
      "path": "/monitor-client-reply",
      "cron": "*/15 * * * *",
      "title": "Monitor client email reply",
      "description": "Check Gmail inbox for replies from client@example.com
        to the email sent on 2026-02-24 with subject 'Q1 Proposal'.
        When a reply is found:
        1. Read the reply content
        2. Post a summary to #general: 'Client replied to the Q1 proposal...'
        3. Delete this cron file (self-destruct after completion)
        If no reply found, do nothing (silent run).",
      "created_at": "2026-02-24T10:00:00Z",
      "updated_at": "2026-02-24T10:00:00Z"
    }
```

## Pattern: Sequential Multi-Service Workflow

**Example request:** "Check my next calendar meeting, find who's attending,
and send them a prep document."

This doesn't need a heartbeat — it's sequential within one turn:

```
Turn 1: Check Google Calendar → get next meeting + attendees
Turn 2: Use attendee info → generate prep document
Turn 3: Send document via Gmail to each attendee
Turn 4: Confirm to user with summary
```

The supervisor handles this naturally through tool chaining. Each step's
output feeds into the next step's input.

## Pattern: Periodic Report

**Example request:** "Every Monday, check our GitHub PRs and Slack
activity and send me a weekly summary."

```
Create crons/weekly-summary/task.json:
  {
    "path": "/weekly-summary",
    "cron": "0 9 * * 1",
    "title": "Weekly engineering summary",
    "description": "Generate a weekly summary:
      1. Check GitHub for PRs opened/merged/closed this week
      2. Check Slack #engineering for key discussions
      3. Check Slack #bugs for reported issues
      4. Compile into a structured summary
      5. Post to #general with format:
         *Weekly Engineering Summary*
         • PRs: X opened, Y merged, Z pending
         • Key discussions: [topics]
         • Bugs reported: [count + severity]
      Read LEARNINGS.md before running for past context.",
    "created_at": "2026-02-24T10:00:00Z",
    "updated_at": "2026-02-24T10:00:00Z"
  }

Create crons/weekly-summary/LEARNINGS.md:
  (empty initially — accumulates across runs)
```

## Self-Destructing Heartbeats

For one-time "wait and report" patterns, the heartbeat should delete
itself after completing:

Include in the task description:
```
After successfully detecting the event and reporting:
1. Delete this task file (crons/monitor-xyz/task.json)
2. Log completion in LEARNINGS.md before deleting
```

## Choosing the Right Interval

| Urgency | Interval | Example |
|---------|----------|---------|
| Critical (deployment) | `*/2 * * * *` (every 2 min) | Monitor deploy status |
| Important (email) | `*/15 * * * *` (every 15 min) | Wait for reply |
| Standard (report) | `0 */4 * * *` (every 4 hours) | Check metrics |
| Low (weekly) | `0 9 * * 1` (weekly) | Summary reports |

## Anti-Patterns

- Don't poll every minute unless truly urgent — each run costs LLM tokens
- Don't create heartbeats without a self-destruct condition for one-time tasks
- Don't forget to include the Slack channel in the task description
- Don't assume the cron instance has context from the original conversation
- Always include enough detail in the description for a fresh Lucy instance to act
