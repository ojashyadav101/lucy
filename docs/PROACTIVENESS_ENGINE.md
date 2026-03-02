# Lucy's Proactiveness Engine

> How Lucy notices what's happening in the workspace, decides when to act, and
> takes visible actions — without being asked.

---

## Overview

Proactiveness is Lucy's ability to observe the workspace, identify opportunities
to add value, and act on them autonomously. It is not a single system but a
set of interlocking components:

```
Slack events ──► proactive_events.jsonl ──► heartbeat reads & acts
                                               │
slack_logs/ ────────────────────────────────────┘
(synced every 10 min)

daily-self-audit ──► proactive_events.jsonl ──► heartbeat picks up
workflow-discovery ──► DMs to team members
```

The heartbeat cron is the **central intelligence loop**. It fires every 30
minutes during working hours (8 AM – 10 PM), reads context from multiple
sources, and decides on one or more visible actions.

---

## Components

### 1. Local Slack Reader — `src/lucy/workspace/slack_local_reader.py`

The eyes of the heartbeat. Reads the locally synced Slack message files that
`slack_sync.py` writes every 10 minutes.

**Key functions:**

| Function | What it does |
|----------|-------------|
| `get_new_slack_messages(ws, since, channel_names)` | Returns messages since a given timestamp, grouped by channel with thread context. Zero API calls — reads filesystem only. |
| `get_last_heartbeat_time(ws)` | Parses `crons/heartbeat/execution.log` to find when the last heartbeat ran. Lets each run know exactly what's "new." |
| `get_channel_summary(ws, hours_back)` | Compact per-channel digest: message count, active users, unanswered questions. <500 tokens. |
| `get_unanswered_questions(ws, hours_back, bot_user_ids)` | Finds question-looking messages (ending in `?` or starting with what/how/when...) that received no follow-up reply within 2 hours. |

**Message file format** (written by `slack_sync.py`):
```
workspaces/{id}/slack_logs/{channel}/{YYYY-MM-DD}.md
workspaces/{id}/slack_logs/{channel}/threads/{thread_ts}.md

Each line: [HH:MM:SS] <USER_ID> message text
```

---

### 2. Proactive Event Queue — `src/lucy/workspace/proactive_events.py`

A lightweight append-only buffer at `data/proactive_events.jsonl`. Events are
written by Slack event handlers and the daily self-audit cron. The heartbeat
reads and **clears** the queue at the start of each run.

**Key functions:**

| Function | What it does |
|----------|-------------|
| `append_proactive_event(ws, event_type, data)` | Appends one JSON event line to the queue. |
| `read_and_clear_proactive_events(ws)` | Returns all queued events and empties the file. Called by the heartbeat. |
| `format_events_for_prompt(events)` | Formats events into a concise LLM-ready block for injection into the heartbeat instruction. |

**Event types:**

| Type | Trigger | Payload |
|------|---------|---------|
| `reaction_added` | Someone adds a celebration emoji (tada, rocket, fire, raised_hands, etc.) | `emoji`, `user`, `channel`, `message_ts`, `is_celebration` |
| `member_joined` | Someone joins a channel | `user`, `channel`, `inviter` |
| `channel_created` | A new channel is created | `channel_id`, `channel`, `creator` |
| `unanswered_question` | Daily self-audit found an unanswered question | `channel`, `user`, `text`, `message_ts` |

---

### 3. Proactive Slack Tools — `src/lucy/tools/slack_proactive.py`

Tools the heartbeat and other cron agents use to take visible Slack actions.
Available on every agent run (not just crons).

| Tool | Use case |
|------|---------|
| `lucy_react_to_message(channel_id, message_ts, emoji)` | Add emoji reaction to a message. Lowest noise, highest signal. Limit: 5 per heartbeat run. |
| `lucy_post_to_channel(channel_id, text, thread_ts?)` | Post to a channel or reply in a thread. Use when you have genuine value to add. |
| `lucy_send_dm(user_id, text)` | Send a DM to a Slack user. Use for personalized outreach. Limit: 3 DMs per heartbeat run. |

**Common emojis for reactions:**
`tada`, `eyes`, `rocket`, `fire`, `100`, `thinking_face`, `white_check_mark`,
`hugging_face`, `bulb`, `raised_hands`, `wave`, `heart`, `muscle`, `thumbsup`,
`clap`, `star`, `sparkles`

---

### 4. Slack Event Handlers — `src/lucy/slack/handlers.py`

Lightweight handlers that capture workspace events **without triggering a full
agent run**. They write to `proactive_events.jsonl` instead.

```python
@app.event("reaction_added")     # → captures celebration emojis
@app.event("member_joined_channel")  # → captures new members
@app.event("channel_created")    # → captures new channels
@app.event("app_home_opened")    # → silently acknowledged
```

**Why not trigger an agent run directly?** A workspace with an active team
might generate dozens of reactions per hour. Each agent run costs ~500ms+ and
LLM tokens. The event queue batches them for the heartbeat to process as a
group, not individually.

---

### 5. The Heartbeat Cron — `workspace_seeds/crons/heartbeat/task.json`

The central intelligence loop. Runs every 30 minutes on weekdays (8 AM – 10 PM).

**Run flow:**

```
1. Read LEARNINGS.md (mandatory — sets context from previous runs)
2. Read proactive_events.jsonl (injected by scheduler before run)
3. Scan recent Slack via lucy_search_slack_history / lucy_get_channel_history
4. Apply decision framework → choose ONE or more actions
5. Execute actions (react, post, DM)
6. Update LEARNINGS.md (mandatory — log findings, update pending items)
```

**Decision framework (what to act on):**

| Signal | Action |
|--------|--------|
| Unanswered question >2h old | Reply in thread or channel |
| Celebration emoji / team win | React with :tada: or :raised_hands: |
| Pattern observed 3+ times | DM person with automation proposal |
| New team member joined | Introduce Lucy via DM |
| New channel created | Note it, potentially introduce |
| Nothing new since last run | Respond `HEARTBEAT_OK` |

**Anti-spam limits (per run):**
- Max 5 emoji reactions
- Max 3 DMs
- Never DM the same person about the same topic within 24h
- Never react to Lucy's own messages
- Only answer questions that are >2h old with no existing reply

**Tone matching:**
| Time | Tone |
|------|------|
| Weekday morning | Task-focused, direct |
| Weekday afternoon | Conversational, available |
| Friday / end of day | Lighter touch |
| Weekend | Minimal — react to celebrations only |

---

### 6. Heartbeat LEARNINGS.md — `workspace_seeds/crons/heartbeat/LEARNINGS.md`

The heartbeat's cross-run memory. Seeded on workspace creation, updated after
every run. Structure:

```markdown
### Critical    ← deadlines, outages, urgent items
### Active      ← in-progress items, follow-ups
### Context     ← recent decisions, patterns observed
### Team Dynamics ← how each person communicates, preferences
### Pending     ← [ ] items to check next run
### Resolved    ← [x] items completed
### Heartbeat Strategy ← learned cadence preferences
```

The heartbeat MUST read this file first and update it last on every run. This
is what turns an isolated cron call into a continuous intelligence loop.

---

### 7. Workflow Discovery — `workspace_seeds/crons/workflow-discovery/task.json`

Runs daily at 10 AM. Investigates one team member per day to find automation
opportunities, then proposes them via DM.

**Run flow:**
1. Read `crons/workflow-discovery/discovery.md` — who was investigated, what was proposed, any responses
2. Choose the team member investigated LEAST recently
3. Search their Slack activity for: recurring manual tasks, repeated questions, expressed frustrations
4. If 2+ examples of a pattern found → send personalized DM proposal
5. Update `discovery.md`

**Evidence requirement:** Only reach out if you have at least 2 specific Slack
examples. No generic "I can automate things for you" messages.

**Adaptation rules:**
- 3 DMs with no response → switch strategy, demonstrate value to their manager instead
- Someone says "not now" → log it, don't re-contact for 14 days
- Accepted automation → log it as a win in `discovery.md`

State file: `crons/workflow-discovery/discovery.md`

---

### 8. Daily Self-Audit — `workspace_seeds/crons/daily-self-audit/task.json`

Runs daily at 7 AM. Performs a background intelligence check across all
channels and coordinates with the heartbeat.

**What it checks:**
1. Channel awareness — any channels with no Lucy activity in 3+ days?
2. Unanswered questions — searches last 24h for missed questions, writes them to `proactive_events.jsonl`
3. Stale knowledge — scans `company/SKILL.md` and `team/SKILL.md` for outdated entries
4. Automation opportunities — looks for patterns worth proposing (hands off to `workflow-discovery`)
5. Heartbeat coordination — checks `crons/heartbeat/LEARNINGS.md` for overdue Pending items
6. Pending items — reviews `data/session_memory.json` for follow-up tasks

Output: `crons/daily-self-audit/audit.md` log + events written to `proactive_events.jsonl`

---

## DM Delivery Fix

Seed crons (workflow-discovery, daily-self-audit) use `delivery_mode: "dm"` but
historically had an empty `requesting_user_id`, causing output to be silently
dropped.

**Fix (two layers):**

1. **On onboarding** (`onboarding.py`): `_patch_dm_crons_with_owner()` scans
   all seeded DM-mode crons and populates `requesting_user_id` with the
   onboarding user's Slack ID.

2. **Fallback in scheduler** (`scheduler.py`): `_resolve_delivery_target_async()`
   — if a DM-mode cron has no `requesting_user_id`, looks up the workspace owner
   from the database and uses that.

---

## Context Injection into Heartbeat

Before each heartbeat run, `scheduler.py` automatically injects two blocks
into the agent instruction:

```
[Proactive Events Queue]
Events captured since last run: reactions, new members, channel_created events,
unanswered questions from the daily-self-audit.

[Recent Channel Activity]
Per-channel summary: message counts, active users, unanswered questions
for the last hour (or 4 hours if this is the first run).
```

This means the heartbeat agent starts each run with pre-built awareness of what
happened — it doesn't need to call any tools to get the overview, it only needs
tools for deeper investigation.

---

## Architecture Diagram

```
Every 10 min:        Every 30 min:            Once daily:
slack_sync.py        heartbeat cron           self-audit cron
    │                    │                         │
    ▼                    │                         │
slack_logs/          LEARNINGS.md  ◄──────────────┤
{channel}/           proactive_    ◄────────────── proactive_
{date}.md            events.jsonl               events.jsonl
                         │
                    Slack event handlers
                    (reaction_added,
                     member_joined,
                     channel_created)
                         │
                         ▼
                  [HEARTBEAT AGENT]
                  1. Read LEARNINGS.md
                  2. Read proactive events
                  3. Read slack_logs/
                  4. Decide & act:
                     - lucy_react_to_message
                     - lucy_post_to_channel
                     - lucy_send_dm
                  5. Write LEARNINGS.md
```

---

## What Proactiveness Is NOT

- **Not real-time.** Slack messages are synced every 10 minutes. The heartbeat
  runs every 30 minutes. Lucy will not respond to a message within seconds via
  the proactive system — only via direct `@mentions`.

- **Not hard-coded reactions.** The heartbeat agent decides what to react to
  based on context. The tools enable actions; the instructions and LEARNINGS.md
  guide judgment.

- **Not a notification spam machine.** Anti-spam rules are baked into the
  heartbeat instruction: rate limits per run, topic cooldowns, weekend silence.

- **Not a replacement for direct mentions.** If someone `@Lucy` asks a question,
  it's handled immediately by the main event handler, not the heartbeat.

---

## Files Reference

| File | Role |
|------|------|
| `src/lucy/workspace/slack_local_reader.py` | Read synced Slack files, find unanswered questions, channel summaries |
| `src/lucy/workspace/proactive_events.py` | Event queue (append/read/clear) |
| `src/lucy/tools/slack_proactive.py` | Slack action tools (react, post, DM) |
| `src/lucy/slack/handlers.py` | Event capture handlers (reaction_added, etc.) |
| `src/lucy/crons/scheduler.py` | Heartbeat context injection, DM delivery fallback |
| `src/lucy/workspace/onboarding.py` | Seed cron DM patching on workspace creation |
| `workspace_seeds/crons/heartbeat/task.json` | Heartbeat instruction + decision framework |
| `workspace_seeds/crons/heartbeat/LEARNINGS.md` | Heartbeat memory seed template |
| `workspace_seeds/crons/workflow-discovery/task.json` | Workflow discovery instruction |
| `workspace_seeds/crons/workflow-discovery/discovery.md` | Workflow discovery state tracker |
| `workspace_seeds/crons/daily-self-audit/task.json` | Daily self-audit instruction |
