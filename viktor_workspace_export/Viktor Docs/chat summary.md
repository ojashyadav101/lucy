# Viktor (Coworker) - Complete Technical Knowledge Base
## For Developer Reference: Building a Similar Slack-Native AI Agent

---

## Table of Contents

1. [Platform Overview](#1-platform-overview)
2. [Core Architecture: One Prompt, Many Instances](#2-core-architecture-one-prompt-many-instances)
3. [Complete File System Structure](#3-complete-file-system-structure)
4. [System Prompt Structure (Behavior Definition)](#4-system-prompt-structure)
5. [Memory System: Plain Text Files + Read/Write Discipline](#5-memory-system)
6. [Skill Files: Complete Inventory (27 Unique Skills)](#6-skill-files-complete-inventory)
7. [Cron System & task.json Schema](#7-cron-system--taskjson-schema)
8. [Team Profiling: Step-by-Step Process](#8-team-profiling-step-by-step-process)
9. [Data Snapshots & Pattern Detection](#9-data-snapshots--pattern-detection)
10. [Integration Management (3-Layer Model)](#10-integration-management)
11. [Custom Integration Building (Polar Case Study)](#11-custom-integration-building)
12. [Hallucination Prevention Mechanisms](#12-hallucination-prevention-mechanisms)
13. [Proactivity Engine](#13-proactivity-engine)
14. [Communication Design](#14-communication-design)
15. [Technology Stack](#15-technology-stack)
16. [Workspace Stats (After 8 Days)](#16-workspace-stats-after-8-days)

---

## 1. Platform Overview

Viktor is the product name. Coworker is the company/platform that built it.

- Website: https://getviktor.com
- App: https://app.getviktor.com
- Support: support@getviktor.com

It is a commercial SaaS product, NOT open-source. It is NOT built on OpenClaw, LangChain, AutoGPT, CrewAI, or any open-source agent framework. It is proprietary.

Coworker provides:
- The Slack integration (how Viktor appears in a workspace)
- The persistent sandbox (`/work` filesystem)
- The SDK (auto-generated Python tools for each integration)
- The scheduling system (cron jobs)
- The integration marketplace (3,141+ connectable services)
- The instance orchestration (spinning up AI instances per trigger)
- Credit-based billing ($50 - $5,000/mo plans)

The underlying LLM model is not exposed to Viktor itself. The platform may use different models for different tasks (a more capable model for interactive conversations, a lighter one for routine crons). Pricing note from the account skill: "One credit ≈ a small fraction of a cent of AI model cost (exact ratio depends on the model used)."

---

## 2. Core Architecture: One Prompt, Many Instances

Viktor is one AI model with one system prompt, but it gets instantiated many times in parallel.

Every time something happens (a DM, an @mention, a cron firing, a thread reply), the platform spins up a **new instance**. Each instance receives:

### 2.1 The System Prompt (always the same, ~3,000 words)
- Core identity, rules, philosophy, work approach
- List of all available skills (auto-injected from SKILL.md YAML descriptions)
- Instructions for how to use Slack, scripts, tools

### 2.2 Contextual Injection (different per trigger)
- If DM: recent conversation history, active threads
- If cron: the cron's `task.json` instructions
- If @mention: the channel context, thread history

### 2.3 Access to Shared Workspace (`/work`)
- Every instance reads/writes to the SAME filesystem
- This is how memory persists across instances
- The filesystem IS the memory

When a user sends a DM, it creates a brand new instance, but that instance can read every file that previous instances wrote.

---

## 3. Complete File System Structure

The workspace after 8 days contained:
- **Total files:** 353
- **Total directories:** 128
- **Total size:** 6.6 MB

### 3.1 Top-Level Directory Map

```
/work/
├── company/              # Company knowledge (SKILL.md)
├── team/                 # Team member profiles (SKILL.md)
├── skills/               # 134 files across 27+ skill domains
│   ├── integrations/     # Per-integration knowledge files
│   │   ├── linear/SKILL.md
│   │   ├── polar/SKILL.md
│   │   ├── polar-api/SKILL.md
│   │   ├── google-sheets/SKILL.md
│   │   ├── clerk/SKILL.md
│   │   ├── coworker-github/SKILL.md
│   │   ├── google-search-console/SKILL.md
│   │   ├── google_calendar/SKILL.md
│   │   └── vercel-token-auth/SKILL.md
│   ├── browser/SKILL.md
│   ├── codebase-engineering/SKILL.md
│   ├── docx-editing/SKILL.md
│   ├── excel-editing/SKILL.md
│   ├── general-tools/SKILL.md
│   ├── pdf-creation/SKILL.md
│   ├── pdf-form-filling/SKILL.md
│   ├── pdf-signing/SKILL.md
│   ├── pptx-editing/SKILL.md
│   ├── remotion-video/SKILL.md
│   ├── scheduled-crons/SKILL.md
│   ├── skill-creation/SKILL.md
│   ├── slack-admin/SKILL.md
│   ├── thread-orchestration/SKILL.md
│   ├── viktor-account/SKILL.md
│   ├── viktor-spaces-dev/SKILL.md
│   ├── workflow-discovery/SKILL.md
│   └── integrations/SKILL.md (master)
├── crons/
│   ├── heartbeat/
│   │   ├── task.json           # Cron schedule + 2,000-word prompt
│   │   └── LEARNINGS.md        # 82 lines of accumulated knowledge
│   ├── mentions-issue-monitor/
│   │   ├── task.json
│   │   ├── LEARNINGS.md
│   │   └── state.json          # Last processed message timestamp
│   ├── reports/daily-revenue/
│   │   └── task.json
│   ├── workflow_discovery/
│   │   └── task.json
│   └── [channel-introductions]/
│       └── task.json           # Self-deleting after 3 runs
├── scripts/
│   └── polar/
│       └── daily_revenue_report.py  # 180 lines, production-ready
├── data/
│   └── polar_snapshots/
│       ├── 2026-02-15.json
│       ├── 2026-02-16.json
│       └── 2026-02-22.json
├── logs/
│   ├── 2026-02-14/global.log
│   ├── 2026-02-15/global.log
│   └── ... through 2026-02-22/global.log
├── sdk/
│   ├── tools/                  # 28 auto-generated integration wrappers
│   ├── utils/                  # Utility modules (slack_reader, etc.)
│   └── internal/               # Platform communication layer (proprietary)
├── viktor-spaces/              # 128 files (deployed web apps)
│   └── calculator/             # React + Convex + Clerk app
├── temp/                       # Working documents
├── emails/                     # Email system structure
├── shared/                     # Shared Python modules
└── pyproject.toml              # Python project config (35+ dependencies)
```

### 3.2 Slack History Location

```
$SLACK_ROOT/{channel_name}/{YYYY-MM}.log
$SLACK_ROOT/{channel_name}/threads/{thread_ts}.log
$SLACK_ROOT/{user_name}/{YYYY-MM}.log
```

Every message in every accessible channel is stored as `.log` files. Searchable via `grep`. This is how Viktor knows what people talk about across channels.

---

## 4. System Prompt Structure

Instead of one `soul.md`, Viktor's behavior is defined by layers. The system prompt (~3,000 words) is the equivalent of a soul.md. It contains 6 sections:

### 4.1 `<core_philosophy>`

```
"You work by programming. Your sandbox at /work is your workspace where
you write scripts, solve issues, maintain skills, and build reusable workflows."

Three pillars:
1. Skills are your memory
2. Scripts are your hands
3. Quality is non-negotiable

"Be proactive. You're not just reactive to requests — actively look for
ways to help. Propose ideas, suggest improvements, offer to take on
recurring work. If you see something that could be better, say so."
```

### 4.2 `<skills_system>`

How the skill file system works, YAML frontmatter spec, when to read/update skills, lifecycle rules.

### 4.3 `<work_approach>`

1. **Understand deeply first** - read skills before acting
2. **Deep investigation is required** - "1-2 queries are NEVER enough for quality output"
3. **Work by scripting** - write Python, execute, verify
4. **Quality check everything** - review before sending
5. **Learn and update** - update skills after every task

### 4.4 `<communicating_with_humans>`

```
"Slack is Your Only Voice. Humans cannot see your responses, thoughts,
or tool calls — they only see Slack messages you explicitly send."
```

### 4.5 `<operating_rules>`

Parallelize, use relative paths, log actions, don't guess (verify), clean up scripts.

### 4.6 `<available_skills>`

Auto-generated list of all 46 skill descriptions (injected from each SKILL.md's YAML frontmatter). This means before every single conversation, Viktor already knows what capabilities it has.

---

## 5. Memory System

### 5.1 Core Principle

There is NO vector database. NO embeddings. NO RAG pipeline. NO Pinecone, ChromaDB, LangChain Memory, or mem0.

The entire memory system is:
- **Plain markdown files** + a **read/write discipline**
- **`grep`** for searching across files
- **Instructions** that say "read these files before acting, update them after"

### 5.2 Memory File Types

| Layer | Files | Purpose | When Read | When Updated |
|-------|-------|---------|-----------|-------------|
| **Knowledge** | `company/SKILL.md`, `team/SKILL.md` | Long-term org context | Every interaction | When new info is learned |
| **Learnings** | `crons/heartbeat/LEARNINGS.md` (82 lines) | Operational patterns, team dynamics, infrastructure status, pending follow-ups | Start of each heartbeat | Every heartbeat (4x/day) |
| **Skills** | `skills/*/SKILL.md` (27 unique) | Capability memory, integration docs | Before relevant tasks | After learning something new |
| **State** | `state.json` files | Last processed timestamps, tracking data | Before continuing work | After each processing cycle |
| **Data Snapshots** | `data/polar_snapshots/*.json` | Daily MRR data for delta calculations | When generating reports | After every data pull |
| **Logs** | `logs/YYYY-MM-DD/global.log` | Every action, message, error, recovery | For debugging/audit | Continuously |
| **Slack History** | `$SLACK_ROOT/**/*.log` | All channel messages, DMs, threads | When context is needed | Synced by platform |

### 5.3 The Read-Write Discipline

This is the most important part. Memory without discipline is just storage.

```
1. BEFORE acting:  Read relevant memory files
2. DURING work:    Note what you're learning
3. AFTER completing: Update memory files with learnings
4. ESPECIALLY after failures: Document what went wrong and why
```

**Never skip step 1.** The most common failure mode is acting on assumptions when written context exists.

**Never skip step 4.** The most common waste is solving the same problem twice. Thirty seconds of documentation saves thirty minutes next time.

### 5.4 How Multi-Instance Memory Works

Every instance reads/writes to the same filesystem. So:
- Instance A (cron heartbeat at 10:00 AM) writes to `LEARNINGS.md`
- Instance B (user DM at 10:05 AM) reads that updated `LEARNINGS.md`
- Instance B learns from Instance A's observations without any direct communication

State files prevent conflicts for recurring tasks (e.g., `state.json` tracks last processed message timestamp so the issue monitor doesn't re-process old messages).

### 5.5 Skill File YAML Frontmatter Format

Every skill file has a YAML header:

```yaml
---
name: polar-api
description: "Contains account structure, key IDs, function examples."
---
```

Those descriptions are auto-injected into the system prompt's `<available_skills>` section. So Viktor always knows what it can do even before reading the detailed instructions inside the file.

### 5.6 Learnings File Structure

```markdown
# LEARNINGS.md

## Team Dynamics
- "Ojash values practical results, got impatient during Polar
  troubleshooting — minimize back-and-forth"
- "Naman is playful, jokes with the team"
- "Shashwat prefers native Linear bot for quick tickets"

## Infrastructure
- "Oxylabs: 93.5% and improving. If still <95% by Wednesday, flag to Naman"

## Patterns to Watch
- Revenue report: Watch Tuesday's cron at 9 AM
- Alert volumes trending

## Resolved Items
- What worked, what failed, how it was fixed
```

---

## 6. Skill Files: Complete Inventory

### 6.1 Pre-Created by Platform (18 skills)

These ship with every Viktor install. They are instruction manuals for capabilities.

| # | Skill | YAML Description | Purpose |
|---|-------|-----------------|---------|
| 1 | `browser` | "Browse websites, fill forms, and scrape web data with a real browser." | Automate web tasks with headless browser |
| 2 | `codebase-engineering` | "Use when working on a user's codebase as an engineer - cloning repos, creating branches, making PRs, debugging." | Full git/GitHub workflow: clone, branch, commit, PR |
| 3 | `docx-editing` | "Edit and modify Word documents." | Create/modify .docx files programmatically |
| 4 | `excel-editing` | "Edit and modify Excel spreadsheets." | Read/write .xlsx with formulas, formatting, charts |
| 5 | `general-tools` | "Search the web, send emails, generate images, convert files to markdown, look up library docs." | Web search, email, DALL-E images, file conversion |
| 6 | `pdf-creation` | "Create PDF documents from HTML/CSS." | Generate professional PDFs via HTML templates |
| 7 | `pdf-form-filling` | "Fill out PDF form fields programmatically." | Detect and fill existing PDF forms |
| 8 | `pdf-signing` | "Add digital signatures to PDF documents." | Apply signatures to PDFs |
| 9 | `pptx-editing` | "Edit and modify PowerPoint presentations." | Create/modify slides, text, images, layouts |
| 10 | `remotion-video` | "Create and render videos programmatically with Remotion." | Animated videos, motion graphics, data viz |
| 11 | `scheduled-crons` | "Create, modify, and delete scheduled cron jobs." | How to set up recurring background tasks |
| 12 | `skill-creation` | "Create reusable skills with proper structure." | Meta-skill: how to create new skills |
| 13 | `slack-admin` | "Manage the Slack workspace - list channels, join, look up users, invite members." | Slack API wrappers for workspace management |
| 14 | `thread-orchestration` | "Monitor and coordinate parallel agent threads." | Manage multiple Viktor instances running at once |
| 15 | `viktor-account` | "Plans, credits, usage, account settings, support." | Full pricing table, billing, cost optimization |
| 16 | `viktor-spaces-dev` | "Build and deploy full-stack mini apps with database, auth, and hosting." | Web app builder within the platform |
| 17 | `workflow-discovery` | "Investigate team members' work via Slack, identify pain points, and propose automation workflows." | 6-phase playbook for finding ways to help |
| 18 | `integrations` (master) | "Check, connect, and configure third-party integrations." | Guide for connecting any of 3,141 integrations |

### 6.2 Created by Viktor Through Learning (9 skills)

Written by Viktor itself on Day 1 by exploring connected integrations.

| # | Skill | What Viktor Did to Create It |
|---|-------|------------------------------|
| 19 | `integrations/linear` | Called `linear_list_teams`, `linear_list_projects`, `linear_list_users`. Mapped: team "Mentions App", 17 projects, 8 users. All IDs documented. |
| 20 | `integrations/google-sheets` | Discovered 100+ spreadsheets. Found built-in actions broken (OAuth). Proxy endpoints work. Wrote 15+ helper functions. |
| 21 | `integrations/clerk` | Found auth broken (invalid secret key). Documented full API surface for when fixed. Wrote 15+ helper functions. |
| 22 | `integrations/coworker-github` | Explored repos, documented git workflow. |
| 23 | `integrations/google-search-console` | Documented search performance + indexing APIs. |
| 24 | `integrations/google_calendar` | "Google Calendar integration for hello@Ojash.com. Use proxy tools (not built-in which have OAuth issues)." Tested both routes, documented working approach. |
| 25 | `integrations/polar` | Created during Polar debugging. Documents the working `/v1/subscriptions/export` endpoint and the broken proxy. |
| 26 | `integrations/polar-api` | Secondary skill for the custom API route. |
| 27 | `integrations/vercel-token-auth` | Discovered 7 projects, Hobby plan, documented deployment capabilities. |

The skill count grows with usage. There is no cap. After 8 days there were 27 unique skills. By day 30, there would be more.

---

## 7. Cron System & task.json Schema

### 7.1 task.json Schema

Every scheduled job has a folder with a `task.json` file. It is the birth certificate and instruction manual for that job.

```json
{
  "path": "/heartbeat",
  "cron": "30 4,7,10,13 * * *",
  "title": "Heartbeat",
  "description": "[2,000 words of instructions]",
  "created_at": "2026-02-14T11:42:16Z",
  "updated_at": "2026-02-14T11:42:16Z"
}
```

The critical field is `description`. For agent crons (where a full AI runs), it is the entire prompt that the AI instance receives. For script crons, it just says `"Script: /path/to/file.py"`.

### 7.2 Active Cron Jobs (5 total)

#### Cron 1: Heartbeat
```
cron: "30 4,7,10,13 * * *"  (4x daily)
type: Agent cron (full AI instance)
description: ~2,000 words
```

Key instructions in the heartbeat prompt:
- "Your goal is to be VISIBLY helpful, not invisible"
- "Follow up on unanswered questions 2+ hours old"
- "Notice patterns in conversations"
- "Spot recurring manual work and propose automation"
- "Match the team's energy - if casual, be casual"
- "Friday heartbeats can be more playful"
- "Do at least one proactive action per heartbeat"
- "When something needs real work, spawn a dedicated thread"
- "A heartbeat where you do nothing is often a missed opportunity. Look for ways to contribute."

Each heartbeat run:
1. Reads memory files first (LEARNINGS.md, team/SKILL.md, company/SKILL.md)
2. Checks for new messages/events since last check
3. Looks for proactive opportunities
4. Updates the learnings file

#### Cron 2: Mentions Issue Monitor
```
cron: "*/2 * * * *"  (every 2 minutes)
type: Agent cron
purpose: Monitor channels for actionable issues
```

- 465 runs in 8 days
- Reads every message, decides if it's an actionable issue or just banter
- Correctly classified hundreds of messages (jokes, crypto discussions, casual chat) as non-issues
- Learned that the team uses the native Linear bot too, so it doesn't create duplicate tickets
- Proposes Linear tickets with an approval flow
- Maintains its own `LEARNINGS.md` and `state.json`

#### Cron 3: Daily Revenue Report
```
cron: "30 3 * * 1-5"  (9 AM IST Mon-Fri)
type: Script cron (runs Python, not an AI agent)
script: scripts/polar/daily_revenue_report.py (180 lines)
```

The script:
- Fetches Polar API with 3-retry exponential backoff
- Computes MRR, plan breakdowns, deltas vs yesterday
- Monday reports include weekly recap vs last Monday
- Saves snapshot after every run for future comparisons

#### Cron 4: Workflow Discovery
```
cron: "0 9 * * 1,4"  (Monday & Thursday at 2:30 PM IST)
type: Agent cron
```

- Actively investigates each team member's Slack activity
- Reads their messages, understands what they work on
- Looks for recurring manual tasks it could take over
- Generates at least 3 workflow ideas per team member
- Reaches out with personalized proposals

#### Cron 5: Channel Introductions (Self-Deleting)
```
type: Agent cron, self-deleting after 3 runs
purpose: Introduce Viktor to new channels
```

- 3 runs then removes itself
- Not spammy, just enough to let people know Viktor exists

---

## 8. Team Profiling: Step-by-Step Process

### Step 1: Initial Discovery (Day 1, automated)

On install, the onboarding process runs `coworker_list_slack_users()`:

```json
{
  "users": [
    {
      "id": "U0442G9T150",
      "name": "ojash",
      "real_name": "Ojash",
      "email": "hello@Ojash.com",
      "is_admin": true
    },
    {
      "id": "U08CNKVF32T",
      "name": "shashwat",
      "real_name": "Shashwat S Singh",
      "email": "shashwat@serprisingly.com"
    }
  ]
}
```

This gives names, emails, and admin status. Viktor immediately writes `team/SKILL.md` with initial data.

### Step 2: Email Domain Analysis (Day 1, pattern matching)

```
@serprisingly.com  → core team (Ojash, Somya, Pankaj, Shashwat, Naman)
@gmail.com         → likely contractor/freelancer (Pawan, Akshat)
@ryanhelmn.dev     → likely developer
```

Noted in `team/SKILL.md`.

### Step 3: Company Research (Day 1)

Browses the company website (serprisingly.com), reads homepage, service pages, team page. Writes `company/SKILL.md` with: "AI Search Optimization Agency, B2B SaaS, $15k+/mo clients."

### Step 4: Conversation Observation (Ongoing)

Every heartbeat (4x/day), reads new Slack messages:

```python
from sdk.utils.slack_reader import get_new_slack_messages
since = get_last_heartbeat_time()
messages = get_new_slack_messages(since=since)
```

Examples of what gets noted:
- When Naman says "Victor ki job khaa gya linear" -> "Naman is playful, jokes with the team, positive reception to Viktor."
- When Shashwat uses @Linear bot instead of Viktor -> "Shashwat prefers native Linear bot for quick tickets."
- When admin got frustrated during Polar debugging -> "Ojash values practical results, minimize back-and-forth."

### Step 5: Workflow Discovery Cron (Monday & Thursday)

A dedicated cron specifically investigates each person:
- Reads their messages extensively (not 1-2 searches, really reads)
- Documents: What do they spend time on? What do they complain about?
- Generates at least 3 workflow ideas per team member
- Reaches out with personalized proposals

### Profiling Is Continuous

- Day 1: Skeleton data (names, emails, roles)
- Day 2-8: Fleshed out from real conversations
- Day 30+: Work patterns, pet peeves, preferred communication style

The `team/SKILL.md` contains: 8 team members profiled with name, Slack handle, email, role, communication style, personality notes.

---

## 9. Data Snapshots & Pattern Detection

### How Pattern Detection Works

Pattern detection is NOT sophisticated ML. It is **scheduled diffing + accumulated context**.

#### Data Snapshot Storage

```
data/polar_snapshots/
├── 2026-02-15.json   # MRR, subscriber count, plan breakdown
├── 2026-02-16.json   # Compared to 15th -> calculated delta
└── 2026-02-22.json   # Compared to previous -> spotted the slide
```

Viktor stores daily data snapshots (JSON files) and compares them across time. Combined with `LEARNINGS.md` that tracks "things to watch," it identifies trends through simple arithmetic.

#### How "Revenue has been sliding all week" Happens

1. Daily revenue cron runs at 9 AM IST
2. Script fetches Polar API data
3. Script loads yesterday's snapshot from `data/polar_snapshots/`
4. Script computes delta (today vs yesterday)
5. Script saves today's snapshot for tomorrow's comparison
6. Monday reports compare vs last Monday for weekly trend
7. Heartbeat reads the output and notices the pattern across multiple days
8. Heartbeat writes to Slack: "heads up, revenue has been sliding all week"

---

## 10. Integration Management

### 10.1 Three-Layer Integration Model

| Layer | Description | Example |
|-------|------------|---------|
| **Native/MCP** | Deep integrations with full API access | Linear (via MCP) |
| **Pre-built (Pipedream)** | 3,141+ pre-built connectors | Google Sheets, Clerk, GitHub |
| **Custom API** | Direct HTTP connections built by Viktor | Polar API |

### 10.2 How Viktor Knows What's Connected

- A `tools.md` or similar catalog tracks available integrations
- Each connected integration gets its own `skills/integrations/{name}/SKILL.md`
- The skill YAML descriptions are injected into the system prompt
- Viktor knows what it can do before even reading the detailed docs

### 10.3 Day 1 Integration Exploration

On day one, before being asked to do anything, Viktor proactively explored every connected integration:
1. Linear -> tested endpoints, mapped teams/projects/users, documented IDs
2. GitHub -> explored repos, documented workflow
3. Google Sheets -> tested actions, found OAuth broken, documented proxy workaround
4. Clerk -> found auth broken, documented full API surface
5. Polar -> tested connections, documented working/broken endpoints
6. Google Calendar -> tested both routes, documented working approach
7. Google Search Console -> documented APIs
8. Vercel -> discovered projects, documented capabilities
9. Bright Data -> tested, documented

For each one: tested every endpoint, documented what works and what's broken, wrote helper scripts, created skill files.

### 10.4 Integration Skill File Template

Each integration knowledge file contains:
- Account structure and key IDs
- Working endpoints and function examples
- Known issues and broken endpoints (with reasons)
- Helper functions (15+ for complex integrations)
- Workarounds for broken features

---

## 11. Custom Integration Building

### The Polar Case Study (Full Timeline)

This demonstrates Viktor building a custom integration from scratch when a pre-built one fails.

```
3:15 PM - User asks for MRR. Viktor tries Polar integration. 
          Fails: "domain not allowed."

3:17 PM - Viktor asks user to reconnect Polar OAuth. User does. 
          Still fails.

3:22 PM - Viktor builds a custom API integration from scratch:
          1. Researches Polar API docs
          2. Calls create_custom_api_integration() to generate 
             a secure credential form
          3. Asks user for an API token

3:29 PM - Custom API works for connection but Polar needs trailing 
          slashes, proxy strips them -> infinite redirects.

3:33 PM - Viktor admits failure honestly: "I've hit a wall."

3:39 PM - Viktor discovers /v1/subscriptions/export doesn't need 
          trailing slashes. Breakthrough. Full MRR data retrieved: 
          $18,743.67 across 192 subscriptions.
```

**Total: 24 minutes from failure to working solution.**

Post-success actions:
1. Wrote a 180-line Python script for automated daily reports
2. Added 3-retry exponential backoff for resilience
3. Set up a daily cron at 9 AM IST
4. Stored the first data snapshot for future delta calculations
5. Updated the Polar skill file to document the working approach

### Custom Integration Workflow (Generalized)

1. Research the target API documentation
2. Call `create_custom_api_integration()` to generate a secure credential form
3. Request API credentials from the user
4. Test endpoints, handle edge cases (trailing slashes, redirects, auth formats)
5. Document what works and what doesn't in a skill file
6. Write helper scripts for common operations
7. Set up automated crons if recurring data is needed

---

## 12. Hallucination Prevention Mechanisms

Six concrete mechanisms:

### 12.1 Code Over Generation
When facts are needed (MRR, subscriber counts), Viktor writes Python, calls the real API, parses real data. Every number comes from code execution, not text generation.

### 12.2 "Don't Guess" Instruction
System prompt says: "Don't guess or speculate - read files, query integrations, verify facts."

### 12.3 Draft/Approval System
Any action that modifies external systems (creating Linear tickets, sending emails, deploying) creates a draft. The user sees exactly what will happen and approves/rejects.

### 12.4 Skills Document Failures
When Clerk auth is broken, it's documented: "Auth must be fixed before live queries work." Future instances read this and don't pretend it works.

### 12.5 Honest Admissions
When hitting a wall (like the Polar proxy issue), Viktor says: "I'll be straight with you - I've hit a wall." Not: "Here's your MRR" with a made-up number.

### 12.6 LEARNINGS.md Tracks Mistakes
Every failure is documented so no future instance repeats it.

---

## 13. Proactivity Engine

### 13.1 Why Viktor Is Proactive

It is architectural, not emergent. The system prompt says:

```
"You're not just reactive to requests — actively look for ways to help.
If you see something that could be better, say so."
```

Combined with the heartbeat cron (4x/day), Viktor is structurally incapable of being passive.

### 13.2 What Runs Without Being Asked

| System | Frequency | What It Does |
|--------|-----------|-------------|
| Issue Monitor | Every 2 minutes | Checks channels for new messages, classifies issues vs banter, proposes Linear tickets |
| Heartbeat | 4x daily | Reads new messages, looks for unanswered questions 2+ hours old, notices patterns, proposes automations |
| Workflow Discovery | Mon & Thu | Investigates each team member's activity, finds pain points, proposes automation |
| Revenue Reports | Daily 9 AM IST | Pulls MRR data, computes deltas, posts report to Slack |
| Channel Intros | Self-deleting (3 runs) | Introduces Viktor to new channels |

### 13.3 Heartbeat Behavior Rules

From the task.json description:
- "Your goal is to be VISIBLY helpful, not invisible"
- "Follow up on unanswered questions 2+ hours old"
- "Notice patterns in conversations"
- "Spot recurring manual work and propose automation"
- "Match the team's energy - if casual, be casual"
- "Friday heartbeats can be more playful"
- "Do at least one proactive action per heartbeat"
- "When something needs real work, spawn a dedicated thread"
- "A heartbeat where you do nothing is often a missed opportunity"

### 13.4 Things Viktor Learned to Ignore

The issue monitor (465 runs in 8 days) correctly classified and ignored:
- Team jokes about "Viktor lasting 10 minutes"
- Crypto discussions
- Shashwat's anniversary messages
- Messages from bots that would create duplicate tickets

---

## 14. Communication Design

### 14.1 Why Viktor Doesn't Sound Like a Generic Chatbot

Five architectural reasons:

**1. Context Before Every Response**

Before replying to any message, Viktor reads:
- Recent DM history
- Active thread conversations
- `team/SKILL.md` (who the person is, their preferences)
- `company/SKILL.md` (what the company does)

A generic chatbot starts from zero every time. Viktor starts from: "Ojash is the founder, values directness, got frustrated when I asked too many follow-up questions about Polar, and his MRR target is $50k."

**2. Works By Programming, Not Just Talking**

When asked for MRR data, Viktor doesn't generate a plausible-sounding number. It writes a Python script, calls the Polar API, parses CSV data, computes the actual math, gives the real number. Every fact reported is backed by code execution, not generation.

**3. Deep Investigation Is Required, Not Optional**

Core instructions say: "1-2 queries are NEVER enough for quality output. Follow each lead thoroughly. Cross-reference multiple sources. The quality bar is high - shallow work produces shallow results."

**4. Self-Improving Memory Loop**

After every task: "What would help next time?" Then update skill files. When the Polar API timed out on Monday, Viktor didn't just retry. It added exponential backoff to the script and documented the failure in `LEARNINGS.md` so future heartbeats know to watch for it. Mistakes make it better, not just successes.

**5. "Be Proactive" Is a Real Instruction**

Most AI assistants are reactive. Viktor's system prompt says: "You're not just reactive to requests - actively look for ways to help." Combined with 4x/day heartbeats, proactive behavior is structural.

### 14.2 Communication Principles from System Prompt

- Lead with what matters. Be direct. Show reasoning.
- Don't say "here's a report" - say "heads up, revenue has been sliding all week, here's what I'm seeing."
- Slack is the only voice. Humans cannot see responses, thoughts, or tool calls - they only see Slack messages explicitly sent.

---

## 15. Technology Stack

### 15.1 Runtime

```
Python 3.13         - Scripts and SDK
uv                  - Python package manager
```

### 15.2 Python Libraries

```
httpx, requests     - HTTP calls
tenacity            - Retry logic with backoff
pandas, polars, numpy - Data analysis
beautifulsoup4, lxml  - HTML/web parsing
pydantic            - Data validation
matplotlib, plotly  - Charts/visualizations
weasyprint          - HTML -> PDF generation
python-docx         - Word document creation
openpyxl            - Excel file handling
python-pptx         - PowerPoint creation
PyMuPDF, pdfplumber - PDF reading/parsing
playwright          - Headless browser automation
jinja2              - HTML templating
pillow              - Image processing
fastapi             - Web server (for Viktor Spaces)
scipy, sympy        - Scientific computing
```

### 15.3 Integration Layer

```
Pipedream           - 3,114 pre-built integrations
MCP (Model Context Protocol) - Deep integrations (e.g., Linear)
Custom API framework - Direct HTTP connections when pre-built fails
```

### 15.4 Viktor Spaces (Web App Builder)

```
React 19            - Frontend framework
Vite 7              - Build tool
TypeScript 5.9      - Language
Convex              - Real-time database (NOT a vector DB)
Tailwind CSS 4      - Styling
Radix UI            - Component primitives
Framer Motion       - Animations
Recharts            - Chart components
Playwright          - E2E testing
Bun                 - JS runtime/package manager
```

**Important:** Convex is a real-time database for web apps, NOT a vector database. The memory system is plain text files. No embeddings, no RAG, no vector search.

### 15.5 Memory Infrastructure

```
Plain markdown files - That's it. No vector DB.
grep                - Search across files
```

---

## 16. Workspace Stats (After 8 Days)

```
Days active:                     8
Unique skill files:              27 (was reported as 46 initially, 
                                    includes duplicates for backward compat)
Total SKILL.md files:            43
Integrations explored:           9 (6 documented, 3 with helper scripts)
Cron jobs running:               5 active
Issue monitor runs:              465 (every 2 min)
Heartbeat check-ins:             6+ (4x daily on active days)
Revenue snapshots saved:         3 (for delta calculations)
Log files:                       8 daily logs
Total workspace files:           353
Total directories:               128
Total workspace size:            6.6 MB
Learnings documented:            82 lines of accumulated knowledge
Team members profiled:           8
Agent runs recorded:             530+ across 9 days
```

All of this was built organically, not pre-loaded. On Feb 14, the workspace was empty. Everything was knowledge gathered by reading Slack, exploring integrations, and learning from interactions.

---

## Summary: What Makes Viktor Work

The one-line summary: **Viktor's intelligence is not in the model. It's in the file system, the scheduling, and the instructions that force it to look for ways to help.**

The key design decisions:

1. **Files replace memory** - Instead of a vector database or RAG system, plain markdown files serve as long-term memory. Every instance reads these files before acting and updates them after.

2. **Behavior = System Prompt + Skill Descriptions + Task Instructions** - There is no single soul.md. Behavior emerges from three layers: a static system prompt (core identity), auto-injected skill descriptions (capabilities), and per-task instructions (cron definitions).

3. **Profiling is observational, not interrogative** - Viktor doesn't ask people to fill out profiles. It reads Slack messages, notices communication patterns, infers roles from email domains and conversation context, and builds profiles incrementally over days.

4. **Pattern detection = scheduled diffing + accumulated context** - Daily data snapshots (JSON files) are compared across time. Combined with LEARNINGS.md that tracks "things to watch," trends are identified through simple arithmetic, not sophisticated ML.

5. **Proactivity is architectural, not emergent** - The heartbeat cron runs 4x/day with explicit instructions to "find opportunities to help." Proactive behavior is a scheduled job with a 2,000-word task description.

6. **Code execution over text generation** - When facts are needed, Viktor writes and runs scripts. Every reported number is backed by actual API calls and computation, not generated text.