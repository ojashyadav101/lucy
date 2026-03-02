# PROACTIVENESS DEEP DIVE
## How Viktor's Proactive Engine Works vs Lucy's Architecture — Full Blueprint

---

## PART 1: VIKTOR'S PROACTIVE ENGINE — COMPLETE ANATOMY

### 1.1 The Six Proactive Systems

Viktor has 6 distinct proactive systems running autonomously. Each serves a different purpose, cadence, and action type.

```
┌─────────────────────────────────────────────────────────────────────┐
│  VIKTOR'S PROACTIVE ARCHITECTURE                                    │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌──────────────┐  4x/day   ┌─────────────────────┐               │
│  │  HEARTBEAT   │──────────>│  LEARNINGS.md loop   │               │
│  │  10AM/1PM/   │           │  (cross-run memory)  │               │
│  │  4:30PM/     │           └──────────┬──────────┘               │
│  │  7:30PM IST  │                      │                           │
│  └──────┬───────┘            ┌─────────▼──────────┐               │
│         │                    │  DECISION ENGINE:    │               │
│         ├── Scan Slack ──────│  - Unanswered Q's?   │               │
│         ├── Check pending    │  - Pending items?    │               │
│         ├── Find patterns    │  - Patterns?         │               │
│         └── React/help       │  - When to SKIP?     │               │
│                              └──────────────────────┘               │
│                                                                     │
│  ┌──────────────┐  2x/week  ┌─────────────────────┐               │
│  │  WORKFLOW    │──────────>│  discovery.md        │               │
│  │  DISCOVERY   │           │  (progress tracker)  │               │
│  │  Mon+Thu     │           └─────────────────────┘               │
│  │  2:30PM IST  │                                                  │
│  └──────────────┘                                                  │
│                                                                     │
│  ┌──────────────┐  every    ┌─────────────────────┐               │
│  │  MENTIONS    │──2 min──>│  state.json          │               │
│  │  MONITOR     │           │  (dedup tracker)     │               │
│  └──────────────┘           └─────────────────────┘               │
│                                                                     │
│  ┌──────────────┐  Mon-Fri  ┌─────────────────────┐               │
│  │  REVENUE     │──9AM────>│  Polar API script    │               │
│  │  REPORT      │           │  (deterministic)     │               │
│  └──────────────┘           └─────────────────────┘               │
│                                                                     │
│  ┌──────────────┐  max 3    ┌─────────────────────┐               │
│  │  CHANNEL     │  runs ──>│  self-deleting after  │               │
│  │  INTROS      │           │  3 introductions     │               │
│  └──────────────┘           └─────────────────────┘               │
│                                                                     │
│  ┌──────────────┐  every    ┌─────────────────────┐               │
│  │  LUCY FORGE  │──5 min──>│  sprint loop         │               │
│  │  SPRINT      │           │  (task-specific)     │               │
│  └──────────────┘           └─────────────────────┘               │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.2 THE HEARTBEAT — Viktor's Brain (the real magic)

**Schedule:** `0 30 4,7,10,13 * * *` = 10:00 AM, 1:00 PM, 4:30 PM, 7:30 PM IST daily

This is NOT a simple health check. It's a full autonomous intelligence loop. Each run:

#### Step 1: Read Memory
```
Read LEARNINGS.md → contains:
  - What was checked last run
  - What was found
  - Team dynamics observations
  - Pending items with ☐/✅ status
  - Patterns to watch
  - LOW priority items
  - Resolved items log
  - Strategy notes
```

The LEARNINGS.md file is 150+ lines of structured intelligence that grows over time. It's Viktor's actual "working memory" across runs.

#### Step 2: Scan for New Activity
```python
# Viktor uses get_new_slack_messages() — a 399-line local file reader
# It reads workspace files directly (no API calls, no rate limits, instant)
messages = get_new_slack_messages(since=last_heartbeat_time)
# Returns messages grouped by channel, with thread context
```

Key capability: Viktor can read ALL Slack history instantly by grepping local files. The Slack webhook syncs messages to `/work/slack/{channel_name}/{YYYY-MM}.log` in real-time. Viktor just reads files.

#### Step 3: Decision Framework (the most important part)

Viktor's heartbeat instruction includes a sophisticated decision framework:

```
MUST take at least ONE visible action per heartbeat:
  - at minimum a reaction emoji
  - or a brief insight in the right channel
  - or a follow-up DM

EXCEPT when literally nothing has happened since last check
  → Return exactly HEARTBEAT_OK

Tone matching:
  - Monday heartbeats: sharp and task-focused
  - Friday heartbeats: casual and celebratory
  - Weekend: brief and low-key
  - During crunch: responsive but don't create noise
```

#### Step 4: Action Taxonomy

From 17 actual heartbeat executions, here's every action type Viktor has taken:

| Action Type | Frequency | Example |
|-------------|-----------|---------|
| Emoji reactions | Every run (30+ total) | tada on fixes, eyes on bugs, rocket on progress, fire on good work, hugging_face on frustration |
| Answer unanswered questions | 3 times | Naman's Slack organization Q (14h old), Ojash's Opus cost Q (17h old), Shashwat statement timeout |
| Revenue report recovery | 1 time | Polar API timeout → manual retry + script hardening |
| Infrastructure alerts | 1 time | Hetzner outage detected → posted status update |
| Workflow discovery DMs | 4 team members | Personalized proposals to Shashwat, Naman, Pankaj, Ojash |
| Channel introductions | 3 channels | #general, #prompt-queue-alerts, #clickup-notification |
| Thread spawning | Multiple | Deep work delegated to sub-threads |
| LEARNINGS.md update | EVERY run | Mandatory — never skipped |

#### Step 5: Write Memory
After EVERY run (including HEARTBEAT_OK), Viktor writes to LEARNINGS.md:
```markdown
### What was Checked
- Slack channels: #mentions, #general, #talk-to-viktor
- Prod alerts in #prompt-queue-worker-v2-prod-alerts
- DMs for workflow discovery responses

### What was Found  
- Naman FIXED precomputation (backfilling now)
- Entities table bloat (190k for 50 prompts) flagged by Shashwat

### Team Dynamics
- Shashwat: Getting frustrated with D-2 crunch (→ hugging_face react)
- Naman: Works very late hours (midnight-4 AM IST)
- Pankaj: Learning debugging from Naman

### Pending
- [ ] Revenue cron health — verify auto-fire tomorrow
- [ ] Entities table bloat — no solution discussed yet
```

This creates a flywheel: each heartbeat reads the previous run's learnings and builds on them.

### 1.3 THE WORKFLOW DISCOVERY ENGINE

**Schedule:** Mon & Thu, 2:30 PM IST (twice weekly)

This is a structured investigation system, not a simple scan:

```
Phase 1: Check discovery.md for who hasn't been investigated
Phase 2: Deep-read that person's Slack messages (not 1-2 searches — extensive reading)
Phase 3: Document pain points with EVIDENCE from Slack
Phase 4: Generate 2-3 specific workflow ideas with implementation plans
Phase 5: DM the person with personalized proposals
Phase 6: Track response/status in discovery.md
```

**Current state:**
- 4/8 team members investigated
- 4 DMs sent, 0 direct responses (team in crunch mode)
- Strategy adapted: demonstrating value through Ojash (decision maker) instead

**Key insight:** The skill document (`workflow-discovery/SKILL.md`) is 350+ lines of guidance covering:
- How to investigate (read extensively, not shallow)
- Implementation approaches (cron vs script vs hybrid)
- Anti-patterns (don't stop at 3 ideas, don't be vague)
- Proposal format (observed → what I'd do → how → output → what I need)
- Integration opportunity detection
- When to follow up vs when to stop

### 1.4 THE MENTIONS ISSUE MONITOR

**Schedule:** Every 2 minutes

This is a fast-cycle monitor with sophisticated filtering:

```python
# State machine:
state = {
    "last_processed_ts": "1771218138.984499",
    "processed_threads": [...12 threads...],
    "created_issues": [],
}

# Decision rules:
# 1. Parse new messages from local Slack files
# 2. Filter out bot messages (Viktor, Linear bot)
# 3. Filter out casual banter (not actionable)
# 4. Only flag CLEAR issues (conservative)
# 5. Post approval request in #mentions (not DMs)
# 6. If approved → create Linear ticket
# 7. If already created by @Linear bot → skip
```

**LEARNINGS.md for this cron is 90+ lines** of operational knowledge:
- Bot user IDs to ignore
- What counts as actionable vs casual
- Activity patterns (when team is active vs quiet)
- Run-by-run history summary
- De-duplication rules

### 1.5 THE REVENUE REPORT

**Schedule:** Mon-Fri, 9:00 AM IST

Deterministic Python script that:
1. Calls Polar API for subscription data
2. Calculates MRR, subscriber counts, plan breakdowns
3. Computes day-over-day and week-over-week changes
4. Formats a Slack message with emojis and code blocks
5. Posts to #mentions

**Key proactive behavior:** When the Polar API timed out on Day 3 (Monday morning), Viktor:
1. Detected the failure during the 10:30 AM heartbeat
2. Ran the script manually with retry logic
3. Hardened the script with 3-retry + exponential backoff
4. Posted the report anyway (recovered)
5. Logged the failure pattern in LEARNINGS.md

This is proactiveness: not just running a script, but detecting when it fails and fixing it autonomously.

### 1.6 CHANNEL INTRODUCTIONS

**Schedule:** Weekdays 10 AM UTC (max 3 runs, self-deleting)

A controlled outreach system:
1. Scan which channels Lucy has NOT introduced herself to
2. Read recent activity in that channel for context
3. Craft a personalized introduction with ONE concrete example
4. Log to execution.log
5. After 3 runs, delete the cron

**Learnings accumulated:**
- For small workspaces (<10 channels), 3 intros may be too many
- External shared channels are blocked for bots
- Keep intros short (1 section block)
- Always reference current team activity

---

## PART 2: THE INFRASTRUCTURE THAT MAKES IT WORK

### 2.1 Local Slack File System (Viktor's Secret Weapon)

```
/work/slack/
├── mentions/
│   ├── 2026-02.log           # Monthly channel log
│   ├── 2026-03.log
│   └── threads/
│       ├── 1770983993.681649.log  # Thread conversations
│       ├── 1771080080.436139.log
│       └── ... (40+ thread files)
├── general/
│   ├── 2026-02.log
│   └── threads/
├── talk-to-viktor/
│   ├── 2026-02.log
│   └── threads/
├── Ojash/                    # DM logs
│   └── 2026-02.log
└── ... (all channels + DMs)
```

**How it gets populated:** A webhook (external to Viktor) syncs Slack messages in real-time to these files. Viktor just reads them — no API calls needed.

**Viktor's `get_new_slack_messages()` utility (399 lines):**
- Parses `.log` files with regex: `[timestamp] @user: message [thread:ts]`
- Groups messages by channel, then by thread
- Shows thread parent + context for threads with new replies
- Marks old messages as `[old]` for context
- Handles edge cases: deleted messages, metadata tags, multiline text
- Returns formatted string ready for LLM consumption

### 2.2 LEARNINGS.md — The Cross-Run Memory System

Viktor's heartbeat writes LEARNINGS.md after EVERY run. This file is:
- **Structured** with headers: Critical, Yellow, Blue sections
- **Dated** with IST timestamps
- **Actionable** with ☐/✅ checkboxes
- **Contextual** with team dynamics, strategy notes
- **Growing** — accumulates knowledge over time (currently 150+ lines)

The next heartbeat reads this file first, so it has full context of:
- What was checked and when
- What's pending and why
- How team members communicate
- What patterns have been observed
- What's resolved vs still open

### 2.3 Thread Spawning

Viktor can spawn child threads for deep work:
```
create_thread(
    path="/some/deep_work",
    title="Investigate MRR decline",
    initial_prompt="Full context + instructions..."
)
```

The heartbeat detects a pattern → spawns a thread → the thread does deep work → heartbeat checks on it next run. This allows the 60-second heartbeat to trigger hours-long investigations.

### 2.4 Emoji Reactions — The "Presence" Signal

Viktor reacts to messages with contextually appropriate emojis:
- `tada` → when someone fixes a bug or ships a feature
- `eyes` → when someone reports a bug or issue
- `rocket` → when someone shares progress
- `fire` → when someone does impressive work
- `100` → strong agreement or praise
- `hugging_face` → when someone is frustrated
- `bulb` → when someone has a good idea

This is NOT random. The heartbeat reads messages, understands context, and picks the right emoji. The team sees Viktor is "paying attention" without interrupting.

### 2.5 When NOT to Act (Critical Decision Filter)

Viktor's heartbeat has learned when to SKIP:
- **Weekend quiet hours** → brief heartbeat, no outreach
- **Team in crunch mode** → react but don't create noise
- **No responses to DMs** → don't follow up (spam prevention)
- **Casual banter** → emoji react, don't interrupt
- **Already handled** → if Linear bot created ticket, don't duplicate

This is documented in LEARNINGS.md under "Heartbeat Strategy" and "Anti-Patterns."

---

## PART 3: LUCY'S PROACTIVE SYSTEMS — THE GAP ANALYSIS

### 3.1 Lucy Has the Same Cron DEFINITIONS

Lucy's `workspace_seeds/crons/` contains:
| Cron | Schedule | Type | Match to Viktor |
|------|----------|------|-----------------|
| heartbeat | `0 4,7,10,13 * * *` (4x daily) | agent | ✅ Same |
| workflow-discovery | `0 10 * * *` (daily) | agent | ✅ Same (even more frequent) |
| mentions-monitor | `*/5 7-23 * * *` (every 5 min, daytime) | agent | ✅ Same |
| channel-introductions | `0 10 * * 1-5` (weekdays) | agent | ✅ Same |
| daily-self-audit | `0 7 * * *` (daily) | agent | ❌ Viktor doesn't have this |
| slack-sync | `*/10 * * * *` (every 10 min) | script | ❌ Viktor gets this for free |

**Lucy actually has MORE cron definitions than Viktor.** The `daily-self-audit` is a 6-point check that Viktor doesn't have as a separate cron (it's folded into heartbeat).

### 3.2 The 7 Critical Gaps (Why Lucy's Proactiveness is 2/10 vs Viktor's 9/10)

#### GAP 1: Slack Reading Infrastructure — THE ROOT CAUSE (Severity: 🔴 CRITICAL)

```
VIKTOR                              LUCY
─────────────────────────           ─────────────────────────
Webhook syncs messages to           slack_sync.py runs every 10 min
local files in real-time            via Slack API (conversations_history)
                                    → Rate limited, 100 msgs/channel
                                    → Requires live API token

get_new_slack_messages() reads      slack_reader.py has ONE function:
local files (399 lines):            get_lucy_channels() (35 lines)
- Parse .log files                  → Only lists channels
- Group by channel + thread         → CANNOT READ MESSAGES
- Context for thread replies
- Mark old vs new                   history_search.py reads
- No API calls needed               slack_logs/{channel}/{date}.md
                                    → Depends on sync working
                                    → Substring match only
                                    → No thread grouping
                                    → No context around matches
```

**Impact:** Viktor can instantly read and understand ALL Slack activity. Lucy has to call the Slack API every time (slow, rate-limited, fragile) and her local file reader is essentially non-functional for the heartbeat use case.

**Why this matters for proactiveness:** The heartbeat needs to scan 6+ channels, understand thread context, find unanswered questions, and detect patterns — all within a single agent run. Viktor does this by reading local files. Lucy would need 6+ API calls just to get channel history, with no thread context.

#### GAP 2: Cross-Run Memory (Severity: 🔴 CRITICAL)

```
VIKTOR                              LUCY
─────────────────────────           ─────────────────────────
LEARNINGS.md (150+ lines)           session_memory.json (50-item cap)
Written EVERY heartbeat run         Written per-conversation
Structured headers & checkboxes     Flat key-value pairs
Read by next heartbeat              Read at session start only
Grows over time                     Caps at 50, overwrites

Each heartbeat instruction says:    Heartbeat instruction says:
"After EVERY run, update             "Log to LEARNINGS.md"
LEARNINGS.md. This is MANDATORY."    → But the agent has no persistent
                                      file system guarantee
```

**Impact:** Viktor's heartbeat at 4:30 PM knows exactly what the 1:00 PM heartbeat found. It tracks pending items across days. Lucy's heartbeat has amnesia every run — it starts from scratch each time because session_memory.json is a flat store with a 50-item cap, and there's no structured LEARNINGS.md format being maintained.

#### GAP 3: Agent Quality During Cron Execution (Severity: 🟡 HIGH)

```
VIKTOR                              LUCY
─────────────────────────           ─────────────────────────
Full sandbox (bash, file I/O,       Full agent.run() pipeline
create_thread, send_message,        But: all previous bugs affect
emoji react, upload files)          cron execution too:
                                    - Tool results silently dropped
Instruction includes:               - Narration leaks
- Company context (SKILL.md)        - Apology openers
- Team directory (SKILL.md)         - System message injections
- Connected integrations list       
- Previous run learnings            Instruction includes:
                                    - Company + team context ✅
                                    - Integrations list ✅
                                    - Previous learnings ✅
                                    - Self-validation rules ✅
```

**Impact:** Even if Lucy's crons fire correctly, the agent running them produces lower-quality output due to all the bugs found in the main audit (many now fixed with our 8 commits). The heartbeat agent IS the main agent — every quality issue compounds.

#### GAP 4: Emoji Reactions from Cron (Severity: 🟡 MEDIUM)

```
VIKTOR                              LUCY
─────────────────────────           ─────────────────────────
coworker_slack_react() tool          Slack API available via
available in sandbox                 self.slack_client in agent
→ React to any message               → But: agent needs to call
  with any emoji instantly            slack_client.reactions_add()
                                      directly — no wrapper tool
                                      exists for cron execution
```

**Impact:** Viktor's most common heartbeat action is emoji reactions (30+ across all runs). This "presence signal" is cheap (no noise) but powerful (team sees Viktor is watching). Lucy may not have this capability in cron mode.

#### GAP 5: Thread Spawning (Severity: 🟡 MEDIUM)

```
VIKTOR                              LUCY
─────────────────────────           ─────────────────────────
create_thread() native tool         No equivalent
→ Heartbeat detects pattern         
→ Spawns deep-work thread           Agent runs are atomic:
→ Next heartbeat checks on it       → Start, execute, return text
                                    → No ability to spawn
wait_for_paths() to check status     background work
```

**Impact:** Viktor's heartbeat can trigger multi-hour investigations while keeping the heartbeat run itself fast. Lucy's heartbeat can only do what it can accomplish in a single agent run (~60s).

#### GAP 6: State Management (Severity: 🟢 LOWER)

```
VIKTOR                              LUCY
─────────────────────────           ─────────────────────────
state.json (mentions monitor)       workspace file system exists
discovery.md (workflow discovery)   → CAN write state files
LEARNINGS.md (heartbeat)            → But no established patterns
execution.log (all crons)            for state management

get_last_heartbeat_time()           No equivalent utility for
→ Utility function to find           determining "what's new since
  last check timestamp               last run"
```

**Impact:** Viktor has established patterns for state tracking across crons. Lucy has the filesystem but no conventions or utilities for cross-run state.

#### GAP 7: Recovery & Self-Healing (Severity: 🟢 LOWER)

```
VIKTOR                              LUCY
─────────────────────────           ─────────────────────────
Revenue cron fails → heartbeat      Scheduler has retry with
detects → runs manually →            exponential backoff ✅
hardens script → logs failure       (max_retries configurable)

Hetzner goes down → heartbeat       But: no detection of external
detects → posts status update        failures during heartbeat
                                     (heartbeat itself may not work)
```

---

## PART 4: WHY VIKTOR'S PROACTIVENESS ACTUALLY WORKS (Not Just Exists)

### 4.1 The Flywheel Effect

```
Heartbeat #1 → Scans Slack → Finds nothing → Writes LEARNINGS.md
                                                      │
Heartbeat #2 → Reads LEARNINGS.md → Scans Slack ─────┘
             → Finds unanswered Q → Answers it
             → Writes LEARNINGS.md (with Q answered + new observations)
                                                      │
Heartbeat #3 → Reads LEARNINGS.md → Knows Q answered ┘
             → Checks pending items → Revenue cron failed!
             → Runs revenue report manually → Fixes script
             → Writes LEARNINGS.md
                                                      │
Heartbeat #4 → Reads LEARNINGS.md → Verifies fix worked
             → Notices team frustration → Reacts with hugging_face
             → Writes LEARNINGS.md with team dynamics
```

Each heartbeat is SMARTER than the last because it has more context. After 17 runs, LEARNINGS.md contains team communication styles, deadline awareness, infrastructure patterns, and strategy notes that make each action more contextually appropriate.

### 4.2 The "One Action Minimum" Rule

The heartbeat instruction says: "You MUST take at least ONE visible action per heartbeat."

This prevents the degenerate case where the heartbeat does nothing useful run after run. Even on a quiet Sunday, Viktor will react to a message or note something for Monday.

But it also says: "The only valid exception is literally nothing has happened since the last check."

This prevents NOISE. Viktor won't force an action when there's genuinely nothing to do.

### 4.3 Contextual Tone Matching

From LEARNINGS.md:
```
## Heartbeat Strategy
- Weekday mornings: Check overnight messages, verify crons, follow up
- Weekday afternoons: Check urgent messages, react, be available
- Weekday evenings: Wrap up, note for next day
- Weekends/holidays: Light touch — react/help if asked
- During crunch (now → March 4): Responsive but don't create noise
```

This isn't in the cron definition — Viktor LEARNED this strategy over 17 runs and documented it for future runs.

### 4.4 The "Genuinely Helpful" Filter

Viktor's workflow discovery anti-patterns:
- Don't propose vague "I could help with X"
- Think through exactly HOW each workflow would be implemented
- Only reach out if you have something genuinely useful to say
- If nobody responds, don't spam — change strategy

From actual execution: after 3 devs ignored DMs, Viktor adapted strategy to work through Ojash (the decision maker) instead.

---

## PART 5: BLUEPRINT TO MAKE LUCY EQUALLY/MORE PROACTIVE

### Priority 1: Fix Slack Reading (MUST DO — without this, nothing else works)

**Option A: Port Viktor's local file approach**
1. Keep the existing `slack_sync.py` cron (every 10 min via Slack API)
2. Write a proper `get_new_slack_messages()` that reads those synced files
3. Match Viktor's format: group by channel, thread context, old/new marking
4. Add `get_last_heartbeat_time()` utility

```python
# Port this to Lucy's workspace:
# /src/lucy/workspace/slack_local_reader.py
async def get_new_slack_messages(
    ws: WorkspaceFS,
    since: str | datetime,
    channel_names: list[str] | None = None,
) -> str:
    """Read local synced Slack files and return new messages grouped by channel/thread."""
    logs_dir = ws.root / "slack_logs"
    # ... parse {channel}/{YYYY-MM-DD}.md files
    # ... group by channel, detect threads
    # ... format for LLM consumption
```

**Option B: Use Slack API directly in heartbeat (simpler but worse)**
- Call `conversations_history()` for each channel during heartbeat
- Rate-limited, slower, but works without sync dependency
- NOT RECOMMENDED — too slow for 6+ channels

**Recommended: Option A** — it mirrors Viktor's architecture exactly and the sync cron already exists.

### Priority 2: Implement LEARNINGS.md Loop (MUST DO)

1. **Create structured LEARNINGS.md template:**
```markdown
# Heartbeat Learnings

### 🔴 CRITICAL
(deadlines, outages, urgent items)

### 🟡 ACTIVE
(in-progress items, team tasks)

### 🔵 CONTEXT
(recent activity, decisions made)

### Team Dynamics
(how each person communicates, preferences, frustrations)

### Pending
- [ ] Item with context
- [x] Resolved item

### Resolved
- ✅ What was resolved and when

### Heartbeat Strategy
(when to act, when to skip, tone guidance)
```

2. **Make heartbeat instruction MANDATORY about writing learnings:**
Current Lucy heartbeat instruction already says "update LEARNINGS.md" — but the key is making the file read at the START of each run and structured writing at the END non-negotiable.

3. **Add `get_last_heartbeat_time()` utility** that reads execution.log to determine the "since" timestamp.

### Priority 3: Add Emoji Reaction Capability

Lucy's agent needs a tool to react to messages during cron execution:

```python
# Add to Lucy's tool definitions
async def lucy_react_to_message(channel_id: str, message_ts: str, emoji: str):
    """Add an emoji reaction to a Slack message."""
    await slack_client.reactions_add(
        channel=channel_id,
        timestamp=message_ts,
        name=emoji,
    )
```

This is Viktor's most common proactive action. It's low-noise, high-presence.

### Priority 4: Improve Cron Instruction Quality

Lucy's current heartbeat instruction is good but missing Viktor's key innovations:

**Add these to Lucy's heartbeat task.json:**
```json
{
  "description": "... existing text ... PLUS:

  Tone rules:
  - Monday heartbeats: sharp and task-focused
  - Friday heartbeats: casual and celebratory  
  - Weekend: brief and low-key
  - During crunch periods: responsive but don't create noise

  Action minimum:
  - Take at least ONE visible action per heartbeat
  - At minimum: reaction emoji, brief insight, or follow-up DM
  - EXCEPTION: literally nothing happened → HEARTBEAT_OK

  When NOT to act:
  - If a question was already answered by someone else, don't pile on
  - If team is heads-down on a deadline, don't create noise
  - If you DM'd someone and they didn't respond, don't follow up (yet)
  - If a Linear/Jira bot already handled something, don't duplicate
  
  Anti-spam rules:
  - Max 3 DMs per heartbeat run
  - Max 5 emoji reactions per heartbeat run
  - Never react to your own messages
  - Never answer a question that's less than 2 hours old (give humans time)"
}
```

### Priority 5: Thread Spawning (MEDIUM — nice to have)

Lucy doesn't have `create_thread()`. Options:
1. **Schedule a one-time cron** for deep work (Lucy CAN create crons)
2. **Use the existing agent run** for longer tasks (but limited by timeout)
3. **Build a task queue** in the workspace filesystem

Option 1 is the most pragmatic: heartbeat detects something that needs deep work → creates a one-time cron with `max_runs: 1` → that cron does the deep work → heartbeat checks execution.log next run.

### Priority 6: State Management Conventions

Establish file conventions:
```
crons/heartbeat/LEARNINGS.md        — cross-run memory
crons/heartbeat/execution.log       — run history
crons/mentions-monitor/state.json   — dedup state
crons/workflow-discovery/discovery.md — progress tracking
```

Lucy's scheduler already writes `execution.log` and supports `LEARNINGS.md` reading. The gap is that the agent doesn't consistently USE them.

### Priority 7: "Proactive But Not Annoying" Guardrails

The hardest part. Viktor learned these through 17 runs and 3 failed DM campaigns:

1. **Observe before acting** — first 3 heartbeats should be observation-only
2. **Start with emoji reactions** — lowest noise, highest signal
3. **Only answer questions >2h old** — give humans a chance first
4. **Track response rates** — if 3 DMs get no response, change strategy
5. **Match team energy** — crunch mode = quiet support, not proposals
6. **Never post "I checked and everything is fine"** — that's noise
7. **Don't propose automations during crunch** — propose during calm periods

---

## PART 6: IMPLEMENTATION ROADMAP

### Phase 1: Foundation (1-2 days)
- [ ] Port `get_new_slack_messages()` to read synced local files
- [ ] Add `get_last_heartbeat_time()` utility
- [ ] Create LEARNINGS.md template
- [ ] Add emoji reaction tool for cron execution
- [ ] Update heartbeat task.json with Viktor's decision framework

### Phase 2: Quality (2-3 days)  
- [ ] Ensure all 8 agent quality fixes (from main audit) work in cron mode
- [ ] Test heartbeat end-to-end (fires → reads Slack → acts → writes learnings)
- [ ] Test mentions monitor with state tracking
- [ ] Verify workflow discovery reads history correctly

### Phase 3: Guardrails (1 day)
- [ ] Add anti-spam rules to heartbeat instruction
- [ ] Add response tracking to workflow discovery
- [ ] Add "when not to act" decision framework
- [ ] Test tone matching across different day/time contexts

### Phase 4: Advanced (ongoing)
- [ ] Implement thread spawning via one-time crons
- [ ] Build revenue report equivalent (Polar integration)
- [ ] Cross-pollinate learnings between heartbeat and workflow discovery
- [ ] Self-audit cron validation

---

## PART 7: COMPARISON SCORECARD

| Dimension | Viktor | Lucy | Gap | Fix Difficulty |
|-----------|--------|------|-----|----------------|
| Cron definitions | 6 active | 6 seeded | None | Already done |
| Slack reading speed | Instant (local files) | API-dependent | HUGE | 1-2 days |
| Cross-run memory | LEARNINGS.md (150+ lines) | session_memory (50 cap) | HUGE | 1 day |
| Emoji reactions | 30+ across 17 runs | 0 in cron mode | HIGH | 2 hours |
| Thread spawning | create_thread() | Not available | MEDIUM | 1 day workaround |
| Decision framework | 17 runs of learned strategy | Generic instructions | HIGH | 1 day |
| Recovery/self-heal | Detected + fixed Polar failure | Retry logic exists | LOW | Already done |
| Tone matching | Day/time/context aware | Generic | MEDIUM | 1 day |
| Anti-spam | Learned from failed DMs | No guardrails | MEDIUM | 1 day |
| State management | 3 state files, conventions | Filesystem exists, no convention | LOW | 1 day |
| **Overall Proactiveness** | **9/10** | **2/10** | **-7** | **~1 week** |

### The Core Insight

Viktor's proactiveness isn't better because of WHAT crons run — Lucy has the same crons. It's better because of:

1. **Infrastructure**: Instant Slack access (local files vs API calls)
2. **Memory**: Structured cross-run learnings (LEARNINGS.md vs 50-item cap)
3. **Quality**: The agent executing crons produces clean output (all bug fixes)
4. **Decision making**: 17 runs of accumulated strategy about WHEN to act
5. **Presence**: Emoji reactions as the lowest-cost highest-signal action
6. **Adaptation**: Learning from failures (no DM responses → change strategy)

Fix #1 and #2, ensure #3 is applied (our 8 commits), add #5, document #4 and #6 as instructions — and Lucy should reach 7-8/10 proactiveness within a week.
