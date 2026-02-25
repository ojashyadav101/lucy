# Lucy's Cron Job System: Technical Deep Dive

This document details the architecture, execution flow, and design principles of Lucy's intelligent cron job and scheduling system.

## Overview

Unlike traditional cron jobs that merely execute a script, Lucy's cron jobs trigger a fully autonomous agent session. This allows scheduled tasks to leverage Lucy's full cognitive abilities, tool access (integrations, memory, browser), and natural language processing. 

The system is designed to be **proactive, intelligent, self-validating, and human-like** in its delivery.

## Core Components

1. **The Scheduler (`src/lucy/crons/scheduler.py`)**: 
   - Built on `APScheduler` (`AsyncIOScheduler` with `CronTrigger`).
   - Runs continuously in the background, managing the lifecycle of all scheduled tasks.
2. **Configuration Storage (`CronConfig`)**: 
   - Scheduled tasks are persisted as `task.json` files within `workspace_seeds/crons/{slug}/`.
   - Each config contains:
     - `task`: The detailed instruction for the agent.
     - `cron_expression`: Standard cron syntax for scheduling.
     - `delivery_channel`: The Slack channel where the request originated.
     - `requesting_user_id`: The Slack ID of the user who created the cron.
     - `delivery_mode`: Routes output to the original `"channel"` or directly via `"dm"`.
3. **Agent Tools**:
   - The LLM interacts with the cron system via specialized tools: `lucy_create_cron`, `lucy_modify_cron`, `lucy_delete_cron`, `lucy_trigger_cron`, and `lucy_list_crons`.

## Creation & Context Management

When a user asks Lucy to schedule a recurring task (e.g., "Check my PRs every morning at 9 AM and DM me"):

1. The agent calls the `lucy_create_cron` tool.
2. **Race Condition Prevention**: The tool securely extracts the `channel_id` and `user_slack_id` directly from the `AgentContext` object tied to the specific request. This guarantees that concurrent requests from different users don't overwrite each other's delivery targets.
3. The configuration is saved to the filesystem, and `APScheduler` is immediately updated with the new job without requiring a restart.

## Execution Flow in Depth

When the scheduled time arrives, the `_run_cron` method is invoked. This process is far more complex than just running a script:

### 1. Instruction Building (`_build_cron_instruction`)
Instead of simply passing the user's original task description to the agent, the scheduler constructs a highly specialized, context-rich prompt. This prompt:
- **Primes the Agent**: Tells Lucy she is waking up to run a scheduled task for a specific user (`<@user_id>`).
- **Prevents Confusion**: Explicitly forbids meta-actions that make sense in live chat but not in background tasks (e.g., "Do NOT ask clarifying questions", "Do NOT suggest setting up reminders").
- **Injects State**: Loads the contents of `LEARNINGS.md` (if the cron maintains persistent state between runs).
- **Enforces Self-Validation**: Instructs the agent to "critically verify the output" and note discrepancies before returning a result.

### 2. Autonomous Agent Run
A fresh `LucyAgent` instance is instantiated. 
- It receives the crafted instruction and the `AgentContext` mapped to the correct delivery target.
- The agent has full access to the `slack_client` and all connected tools to perform research, fetch data, or summarize.

### 3. Output Processing & The De-AI Engine
Before the agent's response is posted to Slack, it passes through the rigorous `process_output()` pipeline in `src/lucy/core/output.py`:
- **Sanitizer**: Strips internal file paths, tool names (e.g., `COMPOSIO_SEARCH_TOOLS`), and model control tokens.
- **Markdown Converter**: Translates standard Markdown (like `**bold**`) to Slack's specific mrkdwn format (`*bold*`).
- **Tone Validator & De-AI Engine**: A two-tier system that detects and neutralizes "AI telltale signs":
  - *Tier 1 (Instant)*: A fast regex pass removes sycophantic openers ("Absolutely!"), chatbot closers ("Hope this helps!"), and converts em dashes (`—`) to commas.
  - *Tier 2 (Contextual)*: If the text scores highly for AI patterns (power words like "delve", "tapestry", heavy hedging, or structural monotony), the text is routed through a fast, cheap LLM pass for a contextual rewrite to sound like a real human colleague.

### 4. Intelligent Delivery
The `_resolve_delivery_target` method determines the final destination:
- If `delivery_mode` is `"channel"`, it posts to the original `delivery_channel`.
- If `delivery_mode` is `"dm"`, it resolves the `requesting_user_id` and sends the message privately.

## Self-Correction and State
Cron jobs are not stateless. The agent can write to `LEARNINGS.md` within its specific cron directory. This allows a cron job to remember what it did yesterday (e.g., "I already alerted them about PR #123, I should skip it today unless it has new comments"). If the agent determines the cron is irrelevant or failing, it can use this state to log issues or adjust its approach on the next run.

## Testing
The system includes `lucy_trigger_cron` to force immediate execution of any scheduled job. This allows users and administrators to verify the logic and output formatting of complex cron jobs without waiting for the scheduled time.