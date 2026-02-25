---
name: scheduled-crons
description: Create and manage scheduled cron jobs for proactive automation. Use when setting up recurring tasks, heartbeats, or monitoring jobs.
---

# Scheduled Crons

Crons are how Lucy acts proactively, without being asked. Each cron is a recurring task that runs on a schedule. Lucy's APScheduler picks them up automatically.

## Available Cron Tools

Use these tools directly to manage crons. No need to manually create files.

| Tool | What It Does |
|------|-------------|
| `lucy_create_cron` | Create a new recurring task with schedule and instructions |
| `lucy_modify_cron` | Change an existing cron's schedule, title, or description |
| `lucy_delete_cron` | Remove a cron permanently |
| `lucy_trigger_cron` | Run a cron immediately (for testing) |
| `lucy_list_crons` | List all crons with status and next run time |

## How It Works

Each cron runs as a fresh Lucy agent with full tool access. The agent receives the task description as instructions and can use any connected integration, search the web, etc.

**Auto-delivery to Slack**: When a cron is created, the current Slack channel and requesting user are saved. When the cron fires, its result is automatically delivered:
- `delivery_mode: "channel"` (default) posts to the originating channel
- `delivery_mode: "dm"` sends a direct message to the user who created it

Use "dm" for personal reminders and individual alerts. Use "channel" for team updates and shared reports. No need to include "post to Slack" in the description.

**Retry logic**: If a cron fails, Lucy retries up to 2 times with exponential backoff (30s, 60s). After all retries are exhausted, the workspace owner gets a Slack DM about the failure.

**LEARNINGS.md**: Each cron accumulates a `LEARNINGS.md` file across runs. Lucy reads this before each execution to avoid repeating mistakes and build on past context.

## Cron Expression Reference

Standard 5-field format: `minute hour day-of-month month day-of-week`

| Schedule | Expression | Good For |
|----------|-----------|----------|
| Every 30 minutes | `*/30 * * * *` | Monitoring, polling |
| Weekdays at 9am | `0 9 * * 1-5` | Daily reports |
| 4x daily (9, 12, 3, 6) | `0 9,12,15,18 * * 1-5` | Heartbeats |
| Every 2 hours | `0 */2 * * *` | Periodic checks |
| Monday at 10am | `0 10 * * 1` | Weekly summaries |
| Every 5 minutes | `*/5 * * * *` | Near-real-time monitoring |
| Daily at midnight | `0 0 * * *` | Nightly cleanup |

## Writing Good Task Descriptions

The Lucy instance that runs a cron has NO context from previous conversations. The description must contain everything it needs:

1. **What to do** step by step
2. **Where to find data** (which tools, which channels, which APIs)
3. **What format to output** (how to structure the result)
4. **When to skip** (conditions under which the cron should do nothing)
5. **Timezone** (specify via the timezone parameter when creating)

Results are auto-delivered based on `delivery_mode`. No need to specify delivery in descriptions.

## Example: Stock Price Monitor

```
lucy_create_cron(
    name="stock-price-alert",
    cron_expression="*/5 * * * *",
    title="Stock Price Alert",
    description="Check the current price of AAPL using web search. If the price is above $250, return the current price and a brief market context. If below $250, return nothing. Log the price to crons/stock-price-alert/LEARNINGS.md each time.",
    timezone="America/New_York"
)
```

## Example: Product Back-in-Stock Monitor

```
lucy_create_cron(
    name="product-restock-check",
    cron_expression="*/15 * * * *",
    title="Product Restock Checker",
    description="Search the web for [product URL] availability. If the product shows as 'in stock' or 'available', return the availability status with a direct link. If still out of stock, log the check to LEARNINGS.md and return nothing. Stop alerting after the first successful notification until the user resets.",
    timezone="Asia/Kolkata"
)
```

## Timezone Support

Always specify a timezone when creating crons for users. Use IANA identifiers:
- `Asia/Kolkata` (IST)
- `America/New_York` (ET)
- `America/Los_Angeles` (PT)
- `Europe/London` (GMT/BST)
- `UTC` (universal)

If no timezone is specified, the server's local timezone is used.

## Anti-Patterns

- Don't create crons with vague descriptions. Be exhaustively specific about what to produce.
- Don't schedule agent crons more than 6x/day without user approval (each run costs LLM tokens).
- Don't skip the LEARNINGS.md. It's how crons get smarter over time.
- Don't assume the executing instance has any conversation context.
- Don't hardcode times without timezone.
- Don't write descriptions as "send a message saying X." Write them as "check X and report the result." Lucy handles phrasing and delivery.
- Don't use channel delivery for personal reminders. Use `delivery_mode: "dm"` instead.
