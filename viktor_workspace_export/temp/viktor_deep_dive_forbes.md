# Viktor (Coworker) — Complete Technical Deep-Dive
## Prepared for Forbes / Entrepreneur.com Editorial Review
### Compiled February 22, 2026 — by Viktor itself

---

## 1. What Is Coworker / Viktor?

**Viktor is the product name. Coworker is the platform.**

- **Platform**: Coworker (https://getviktor.com / https://app.getviktor.com)
- **Product Name**: Viktor — the AI coworker
- **What It Is**: An autonomous AI agent that lives inside your Slack workspace, has a persistent filesystem, can connect to 3,141 third-party integrations, runs scheduled jobs, writes and executes code, and builds its own memory over time.
- **NOT open-source**: Coworker is a proprietary platform. It is not built on OpenClaw, LangChain, AutoGPT, CrewAI, or any open-source agent framework.
- **Support**: support@getviktor.com
- **Pricing**: Plans range from $50/mo (20,000 credits) to $5,000/mo (2,400,000 credits). Higher tiers get volume discounts up to 16.7%.

**Key distinction from chatbots**: Viktor has a persistent workspace (filesystem), can write and execute Python code, runs scheduled background tasks, and accumulates knowledge over time. A chatbot starts fresh every conversation. Viktor starts every conversation by reading what all previous conversations learned.

---

## 2. The Skill System — All 27 Unique Skills Explained

### What Are Skills?

Skills are `SKILL.md` files — plain markdown with YAML frontmatter. They serve as Viktor's **long-term capability memory**. Each skill has a `name` and `description` in its YAML header. The descriptions are **auto-injected into Viktor's system prompt** before every conversation, so Viktor always knows what it can do.

### Are They Pre-Created or Learned?

**Both.** There are two categories:

**Pre-created by the platform (SDK-managed):**
These come with the Coworker platform and define Viktor's core capabilities. They contain instructions for how to do common tasks. They get updated when the platform releases new features. Examples: browser, pdf-creation, excel-editing, codebase-engineering.

**Created by Viktor through learning:**
These are written by Viktor itself when it explores your workspace. Examples: every integration skill (linear, polar, google-sheets, clerk) was created by Viktor on Day 1 after proactively exploring each connected integration. The company/SKILL.md and team/SKILL.md files were created during onboarding.

### Complete Inventory of All 27 Unique Skills

(Note: The filesystem shows 43 SKILL.md files because some skills exist in both hyphenated and underscored naming formats for backward compatibility. There are 27 truly unique skills.)

#### Core Capability Skills (Pre-Created)

| # | Skill | Description | What It Contains |
|---|-------|-------------|-----------------|
| 1 | **browser** | Browse websites, fill forms, and scrape web data with a real browser. Use when interacting with websites or automating web tasks without API access. | Instructions for creating browser sessions, navigating pages, filling forms, downloading files, scraping data. Uses a real headless browser — not just HTTP requests. |
| 2 | **codebase-engineering** | Use when working on a user's codebase as an engineer — cloning repos, creating branches, making PRs, debugging, or doing typical software engineering tasks. | Git workflows, how to clone repos, create branches, commit code, open pull requests via GitHub CLI. Full software engineering workflow. |
| 3 | **docx-editing** | Edit and modify Word documents. Use when working with .docx files. | How to read, create, and modify Microsoft Word documents programmatically using Python libraries. |
| 4 | **excel-editing** | Edit and modify Excel spreadsheets. Use when working with .xlsx files. | How to read, create, modify Excel files. Includes formula handling, formatting, chart creation, validation scripts. |
| 5 | **general-tools** | Search the web, send emails, generate images, convert files to markdown, and look up library docs. Use when a task needs one of these general-purpose tools. | Web search (AI-powered), email sending, image generation (DALL-E style), file-to-markdown conversion, library documentation lookup. |
| 6 | **pdf-creation** | Create PDF documents from HTML/CSS. Use when creating PDFs, reports, or formatted documents. | How to generate professional PDFs from HTML/CSS templates using WeasyPrint. Covers layout, styling, multi-page documents. |
| 7 | **pdf-form-filling** | Fill out PDF form fields programmatically. Use when completing or filling out PDF forms. | How to detect form fields in existing PDFs and fill them with data. |
| 8 | **pdf-signing** | Add digital signatures to PDF documents. Use when signing PDFs or adding signatures. | How to apply digital signatures or visual signature images to PDFs. |
| 9 | **pptx-editing** | Edit and modify PowerPoint presentations. Use when working with .pptx files. | How to create and modify PowerPoint slides — text, images, layouts, animations. |
| 10 | **remotion-video** | Create and render videos programmatically with Remotion. Use when making videos, motion graphics, or animated content. | How to use Remotion (React-based video framework) to create animated videos, motion graphics, data visualizations. |
| 11 | **scheduled-crons** | Create, modify, and delete scheduled cron jobs. Use when scheduling recurring tasks on a cron. | Full documentation for creating agent crons (AI-powered) and script crons (code-only). Includes condition scripts, cost optimization, frequency guidelines. |
| 12 | **skill-creation** | Create reusable skills with proper structure and frontmatter. Use when creating or editing a skill or workflow. | The meta-skill: how to create new skills with proper YAML frontmatter, directory structure, scripts, references. |
| 13 | **slack-admin** | Manage the Slack workspace — list channels, join and leave channels, open group DMs, look up users, invite members, check reactions. Use when discovering channels, finding users, or managing workspace membership. | Slack API wrappers for channel management, user lookup, workspace administration. |
| 14 | **thread-orchestration** | Monitor and coordinate parallel agent threads. Use when checking thread progress, listing running threads, or debugging stuck workflows. | How to manage multiple parallel Viktor instances — check which threads are running, wait for completion, coordinate work. |
| 15 | **viktor-account** | Viktor product knowledge — plans, credits, usage, account settings, and support. Use when the user asks about billing, costs, upgrading, or needs help with their account. | Full pricing table, credit system explanation, cost optimization tips, account management tools, support links. |
| 16 | **viktor-spaces-dev** | Build and deploy full-stack mini apps with database, auth, and hosting. Use when users want a custom web app, dashboard, or interactive tool. | How to build and deploy web applications with database, authentication, and hosting — all within Viktor's platform. |
| 17 | **workflow-discovery** | Investigate team members' work via Slack, identify pain points, and propose personalized automation workflows. Use when discovering how Viktor can help the team or exploring automation opportunities. | A 6-phase playbook for understanding each team member's work, finding pain points, generating automation ideas, proposing workflows, and implementing approved ones. |
| 18 | **integrations** (master) | Check, connect, and configure third-party integrations. Use when managing integrations or adding custom API connections. | How to check what's connected, guide users to connect new integrations, create custom API connections for unsupported services. |

#### Integration-Specific Skills (Created by Viktor on Day 1)

These were all written by Viktor itself after proactively exploring each connected integration:

| # | Skill | Description | How Viktor Created It |
|---|-------|-------------|-----------------------|
| 19 | **integrations/linear** | Use when working with Linear. Contains account structure, key IDs, and function examples. | Viktor called `linear_list_teams`, `linear_list_projects`, `linear_list_users`, `linear_list_issue_labels`, etc. Mapped the entire workspace: team "Mentions App" (ID: 85af8f8d), 17 projects, 8 users, 3 labels. Documented all IDs for future use. |
| 20 | **integrations/google-sheets** | Use when working with Google Sheets. Contains account structure, key IDs, and function examples. | Viktor discovered 100+ spreadsheets, tested all Pipedream actions (found them broken — OAuth error), tested proxy endpoints (found them working), wrote 15+ helper functions, documented everything. |
| 21 | **integrations/clerk** | Use when working with Clerk. Contains account structure, key IDs, and function examples. | Viktor tested all API endpoints, discovered auth is broken (invalid secret key format), documented the full API surface from Clerk docs, wrote 15+ helper functions ready for when auth is fixed. |
| 22 | **integrations/coworker-github** | Use when working with GitHub. Contains account structure, key IDs, and function examples. | Viktor explored GitHub repos, documented the git workflow and CLI commands available. |
| 23 | **integrations/google-search-console** | Use when working with Google Search Console. Contains account structure, key IDs, and function examples. | Viktor explored GSC API endpoints, documented search performance and indexing capabilities. |
| 24 | **integrations/google_calendar** | Google Calendar integration for hello@ojash.com. Use proxy tools (not built-in actions which have OAuth issues). Covers listing calendars, querying events, creating/updating events, and free/busy checks. | Viktor tested both built-in actions and proxy endpoints, documented that proxy tools work while built-in OAuth has issues. |
| 25 | **integrations/polar** | Use when working with Polar. Contains account structure, key IDs, and function examples. | Created during Polar API debugging. Documented the working endpoint (/v1/subscriptions/export), the broken Pipedream proxy issue, and the custom API workaround. |
| 26 | **integrations/polar-api** | Use when working with Polar API. Contains account structure, key IDs, and function examples. | Secondary Polar skill for the custom API integration route. |
| 27 | **integrations/vercel-token-auth** | Use when working with Vercel. Contains account structure, key IDs, and function examples. | Viktor explored Vercel API — discovered 7 projects (mostly Next.js), Hobby plan, documented deployment capabilities. |

---

## 3. The Cron System — task.json Explained in Detail

### What Is task.json?

Every scheduled job lives in its own folder under `/work/crons/`. The `task.json` file is the **configuration and instruction set** for that job. It tells the scheduling system:

1. **When to run** (cron expression)
2. **What to do** (either spin up a full AI agent with instructions, or run a Python script)
3. **Metadata** (creation/update timestamps)

### task.json Schema

```json
{
  "path": "/heartbeat",                    // Unique identifier for this cron
  "cron": "30 4,7,10,13 * * *",           // Cron schedule (UTC)
  "title": "Heartbeat",                    // Human-readable name
  "description": "Full instructions...",    // Either AI agent prompt OR script path
  "created_at": "2026-02-14T11:42:16Z",   // When this cron was first created
  "updated_at": "2026-02-14T11:42:16Z"    // Last modification
}
```

The `description` field serves two purposes:
- **For agent crons**: It IS the full prompt/instructions given to the AI instance when it wakes up. This can be 2,000+ words of detailed instructions. The AI instance that runs on each cron tick gets ONLY this description as context — it has no memory of the conversation that created the cron.
- **For script crons**: It simply contains `"Script: /path/to/script.py"` — the scheduler runs the Python file directly without an AI.

### Complete Cron Folder Structure

```
/work/crons/
├── heartbeat/                          ← Proactive check-in system
│   ├── task.json                       ← 2,000-word instruction prompt
│   ├── LEARNINGS.md                    ← 82 lines of accumulated knowledge
│   ├── execution.log                   ← Timestamped log of every run
│   └── todo.md                         ← Pending items to track
│
├── mentions-issue-monitor/             ← Channel monitoring system
│   ├── task.json                       ← Detailed monitoring instructions
│   ├── LEARNINGS.md                    ← Classification rules, patterns learned
│   ├── execution.log                   ← 465 runs logged
│   ├── state.json                      ← Last processed message timestamp
│   └── scripts/
│       └── check_new_messages.py       ← Script to parse Slack logs efficiently
│
├── reports/
│   └── daily-revenue/                  ← MRR reporting system
│       ├── task.json                   ← Points to Python script
│       └── execution.log              ← Run history
│
├── workflow_discovery/                 ← Automation opportunity finder
│   └── task.json                       ← Investigation methodology
│
└── channel_introductions/              ← Self-deleting intro system
    └── task.json                       ← 3 runs then self-destructs
```

### Detailed Breakdown of Each Cron

#### Cron 1: Heartbeat (task.json = 2,000 words of instructions)
- **Schedule**: 4x daily (10:00 AM, 1:00 PM, 4:00 PM, 7:00 PM IST)
- **Type**: Agent cron (full AI instance)
- **What it does**: Each run, a fresh AI instance is created with the 2,000-word prompt. It:
  1. Reads LEARNINGS.md (accumulated context from all previous runs)
  2. Reads today's global.log (what happened today)
  3. Calls `get_new_slack_messages()` to fetch everything since the last heartbeat
  4. Analyzes all new messages looking for:
     - Unanswered questions (2+ hours old)
     - Patterns in conversations
     - Recurring manual work that could be automated
     - Good news to celebrate, wins to acknowledge
  5. Takes at least one proactive action (DM, channel message, emoji reaction, research offer)
  6. Updates LEARNINGS.md with anything new it learned
  7. Logs what it did to execution.log
- **Key instructions from the prompt**:
  - "Your goal is to be VISIBLY helpful, not invisible"
  - "A heartbeat where you do nothing is often a missed opportunity"
  - "Match the team's energy — if they're casual, be casual"
  - "Friday heartbeats can be more playful"
  - "DM for personal/specific offers, channel message for team-wide insights"
  - "When something needs real work, spawn a dedicated thread"

#### Cron 2: Mentions Issue Monitor (465 runs in 8 days)
- **Schedule**: Every 2 minutes, 24/7
- **Type**: Agent cron (lightweight AI instance)
- **What it does**: Each run:
  1. Runs `check_new_messages.py` to find messages newer than state.json timestamp
  2. Filters out bot messages (Viktor, Linear bot)
  3. Analyzes each message/thread: Is this a bug? Feature request? Or just chat?
  4. If it detects an issue: Posts approval request in #mentions with a pre-filled Linear ticket
  5. If approved: Creates the issue on Linear with labels, priority, screenshots
  6. Updates state.json with latest processed timestamp
- **What it learned** (from LEARNINGS.md):
  - Shashwat prefers the native @Linear bot for quick tickets
  - Naman jokes about Viktor ("victor ki job khaa gya linear 😔") — not an issue
  - Weekend/evening messages are almost always casual banter
  - If a ticket was already created by @Linear bot, don't duplicate it

#### Cron 3: Daily Revenue Report
- **Schedule**: 9:00 AM IST, Monday–Friday
- **Type**: Script cron (runs Python directly, no AI)
- **What it does**: Runs a 180-line Python script that:
  1. Calls Polar API `/v1/subscriptions/export` with 3-retry exponential backoff
  2. Parses CSV response, computes MRR by plan
  3. Loads yesterday's snapshot from `/work/data/polar_snapshots/`
  4. Calculates delta (change in MRR, subscriber gains/losses)
  5. On Mondays: includes week-over-week recap
  6. Posts formatted report to #mentions channel
  7. Saves today's snapshot for tomorrow's comparison

#### Cron 4: Workflow Discovery
- **Schedule**: Monday & Thursday at 2:30 PM IST
- **Type**: Agent cron
- **What it does**: Investigates team members' Slack activity to find automation opportunities. Follows the 6-phase workflow-discovery skill.

#### Cron 5: Channel Introductions (Self-Deleting)
- **Schedule**: 3:30 PM IST, Monday–Friday
- **Type**: Agent cron
- **What it does**: Introduces Viktor to one new channel per run with a personalized example of how Viktor can help. After 3 introductions, it calls `delete_cron` on itself.

---

## 4. How Viktor Manages 3,141 Integrations

### Integration Architecture

Viktor's integration system has three layers:

**Layer 1: Pre-Built Integrations (3,141 available)**
The Coworker platform provides a catalog of 3,141 integrations:
- **3,114 Pipedream integrations** — Pipedream is an integration middleware platform. Each integration provides proxy HTTP methods (GET/POST/PUT/PATCH/DELETE) to the service's API, plus pre-built actions for common operations. Examples: Stripe, HubSpot, Shopify, Slack, Gmail, Notion, Asana, Jira, Salesforce, Twilio, and thousands more.
- **27 native integrations** — Built directly into Coworker with deeper functionality. Examples: GitHub (git + CLI), Google Drive, OneDrive, Notion, Linear (MCP protocol), Asana.

When a user connects an integration:
1. User clicks the connection URL (OAuth flow or API key form)
2. The Coworker platform generates new SDK tool files in Viktor's workspace
3. Viktor can immediately call those tools

**Layer 2: Connected Integrations (currently 10 for Serprisingly)**
```
GitHub (Git & CLI)           — native integration
Linear                       — MCP protocol integration
Google Sheets                — Pipedream integration
Google Calendar              — Pipedream integration
Google Search Console        — Pipedream integration
Clerk                        — Pipedream integration (auth broken)
Bright Data                  — Pipedream integration
Polar                        — Pipedream integration (proxy broken)
Polar (Custom API)           — Custom integration (Viktor-built)
Vercel                       — Pipedream integration
```

Each connected integration generates a Python SDK file:
```
sdk/tools/mcp_linear.py              — 40+ Linear functions
sdk/tools/pd_google_sheets.py        — 36 Google Sheets functions
sdk/tools/pd_clerk.py                — 12 Clerk functions
sdk/tools/pd_bright_data.py          — 9 Bright Data functions
sdk/tools/pd_polar.py                — 5 Polar proxy functions
sdk/tools/custom_api_vvpjfwhokmnwxs2d5xgqpm.py  — 1 custom Polar GET function
```

**Layer 3: Custom API Integrations (Viktor builds these itself)**
When a pre-built integration doesn't work, Viktor can create a custom API integration. This is what happened with Polar.

### The Polar Story — How Viktor Builds Its Own Integrations

This is the story for the article. It demonstrates Viktor's problem-solving approach when something breaks.

**The Problem (Feb 14, 3:15 PM IST):**
Ojash asked: "What is our MRR on Polar for Mentions?"

Viktor tried the connected Polar integration (`pd_polar_proxy_get`). It failed. Error: "domain not allowed for this app." The Pipedream integration's proxy was blocking all requests to api.polar.sh.

**Attempt 1 — Ask user to reconnect:**
Viktor asked Ojash to reconnect the Polar integration via OAuth. Ojash did. It still didn't work. The issue was platform-level, not auth-level.

**Attempt 2 — Build a custom integration:**
Viktor:
1. Researched the Polar API documentation (found 114 endpoints)
2. Called `create_custom_api_integration` with:
   - Base URL: `https://api.polar.sh`
   - Auth type: Bearer token
   - Methods: GET
3. The platform generated a secure credential form URL
4. Asked Ojash to create a Polar API token and paste it into the secure form
5. Once saved, a new SDK file was auto-generated: `custom_api_polar_get()`

**Attempt 3 — Debugging the trailing slash issue:**
The custom integration worked for the API connection but the Polar API requires trailing slashes on endpoints (`/v1/subscriptions/` not `/v1/subscriptions`). The integration proxy stripped trailing slashes, causing 307 redirects that failed.

Viktor tried: URL encoding, different base URLs, browser automation. All failed.

**The Breakthrough (Feb 14, 3:39 PM IST):**
Viktor discovered that sub-path endpoints like `/v1/subscriptions/export` DON'T require trailing slashes. By targeting the CSV export endpoint directly, it bypassed the redirect issue entirely.

Result: Full MRR data retrieved. $18,743.67 across 192 active subscriptions, broken down by plan.

**The Documentation:**
Viktor then:
1. Updated the Polar skill file with the working approach
2. Wrote a 180-line Python script for automated daily reports
3. Set up a cron job for daily MRR reports at 9 AM IST
4. Added retry logic with exponential backoff for resilience
5. Stored the first snapshot for future delta calculations

**Total time from first failure to working solution: ~24 minutes.**

This demonstrates a key architectural principle: Viktor doesn't just use tools — it can create new tools when existing ones break. The custom API integration system means Viktor can connect to ANY HTTP API, even if it's not in the 3,141-integration catalog.

---

## 5. How Viktor Manages Hallucinations

This is critical for the Forbes audience. Here's the real answer:

### The Architecture That Prevents Hallucination

**1. Code execution over generation:**
When Viktor needs a fact (like MRR), it doesn't generate a plausible number. It:
- Writes a Python script
- Calls the real API
- Parses the actual response
- Reports the real data

Every number in every revenue report is computed from API data, not generated. The script is saved and reusable — the same code runs every day.

**2. "Don't guess — verify" is a core instruction:**
Viktor's system prompt literally says:
> "Don't guess or speculate — read files, query integrations, verify facts."

And:
> "1-2 queries are NEVER enough for quality output. Follow each lead thoroughly before concluding. Cross-reference multiple sources to verify facts."

**3. The draft/approval system:**
For any action that modifies external systems (creating Linear tickets, sending emails, deploying code), Viktor creates a **draft** that requires explicit user approval before execution. Users see exactly what Viktor will do and can approve or reject.

**4. Skills document what works AND what doesn't:**
When the Clerk integration's auth was broken, Viktor didn't pretend it worked. It documented in the skill file:
> "Root cause: The Clerk Secret Key is invalid (not in sk_live_/sk_test_ format). Auth must be fixed before any live queries will work."

Future Viktor instances read this and know not to attempt Clerk API calls.

**5. Honest admission of limitations:**
When Viktor couldn't solve the Polar trailing slash issue, it said:
> "I'll be straight with you — I've hit a wall. The Polar API requires trailing slashes on all URLs, and the integration proxy strips them, causing infinite redirects. I tried every workaround."

Rather than fabricating data, it asked for the real number. Then it found the actual workaround 6 minutes later.

**6. LEARNINGS.md tracks failures:**
Every failure is documented so future instances don't repeat mistakes:
```
### LOW: Clerk integration auth broken
- Clerk secret key is invalid (not in sk_live_/sk_test_ format)
- Documented in skills/integrations/clerk/SKILL.md
- Mention to Ojash if they try to use Clerk features
```

### What About Conversational Hallucination?

Viktor's system prompt includes:
> "Slack is Your Only Voice. Humans cannot see your responses, thoughts, or tool calls — they only see Slack messages you explicitly send."

This means Viktor knows its Slack messages are the ONLY thing humans see. Combined with "Quality is non-negotiable" and "Review your output critically before sending," there's an explicit instruction loop of: investigate → verify → draft → review → send.

---

## 6. Memory Infrastructure — How It Actually Works

### Is It a Library? A Package? A Vector Database?

**None of the above.** Viktor's memory is the simplest possible architecture: **plain text files on a filesystem.**

There is no:
- Vector database (no Pinecone, no Weaviate, no ChromaDB)
- RAG (Retrieval-Augmented Generation) pipeline
- Embedding-based search
- External memory service
- Specialized AI memory library

Instead:
```
/work/company/SKILL.md          ← Plain markdown, ~50 lines
/work/team/SKILL.md             ← Plain markdown, ~50 lines
/work/crons/heartbeat/LEARNINGS.md ← Plain markdown, ~82 lines
/work/skills/*/SKILL.md         ← Plain markdown, ~50-500 lines each
```

Viktor reads these files at the start of relevant tasks and updates them after learning something new. That's the entire memory system.

### Why Plain Files Instead of a Database?

1. **LLMs are excellent at reading text.** A 200-line markdown file is trivially small for a language model to process. No need for embeddings or retrieval.
2. **Files are transparent.** You can read them, edit them, version them. Nothing is hidden in a vector space.
3. **Files are composable.** Each skill file is independent. Viktor reads only the relevant ones for each task, keeping context focused.
4. **grep is powerful search.** Viktor uses `grep` to search across all Slack history and skill files. For a workspace this size, it's instant.

### The Memory Lifecycle

```
Task arrives
    ↓
Viktor reads relevant SKILL.md files
    ↓
Viktor reads LEARNINGS.md for accumulated context
    ↓
Viktor performs the task (calling APIs, writing scripts, etc.)
    ↓
Viktor learns something new
    ↓
Viktor updates SKILL.md or LEARNINGS.md
    ↓
Next task starts with updated knowledge
```

This is a simple read-update loop, but because it happens across hundreds of tasks over weeks and months, the knowledge compounds. On Day 1, team/SKILL.md has 8 names and emails. By Day 30, it has communication styles, preferred tools, work hours, pet peeves, and project ownership.

---

## 7. Multi-Instance Architecture — How Viktor Creates "New Versions of Itself"

### The Instance Model

Viktor is not a single running process. There is no "Viktor daemon" running 24/7. Instead:

1. **Trigger occurs**: DM, @mention, cron fires, thread reply
2. **Platform spins up a new instance**: Fresh AI model session with the system prompt
3. **Instance reads workspace**: Skills, learnings, Slack history — all from shared files
4. **Instance performs work**: Writes scripts, calls APIs, sends Slack messages, updates files
5. **Instance terminates**: The session ends. Nothing is "in memory."
6. **Files persist**: Everything the instance wrote to disk is available to all future instances

### Why This Matters

- **Parallelism**: Multiple Viktor instances can run simultaneously. Right now, 3 threads are active: onboarding, and two heartbeat threads. They share the filesystem but operate independently.
- **Fault isolation**: If one instance crashes, others are unaffected. The shared filesystem means no knowledge is lost.
- **Scaling**: There's no limit to how many instances can run (beyond credit consumption).

### How Instances Communicate

Since instances don't share memory (only files), they communicate through:
- **LEARNINGS.md**: One instance writes "watch Oxylabs success rate." A later instance reads this and checks.
- **state.json**: The issue monitor uses this to track which messages have been processed. Each instance reads the state, processes new messages, and writes back the updated state.
- **execution.log**: Instances log what they did so future instances know what's already been handled.
- **Global log**: Every instance logs to the same daily global.log file. Heartbeat instances read this to understand today's activity.

---

## 8. The SDK — Viktor's Toolbox

### What Tools Viktor Has Access To

The SDK provides auto-generated Python wrappers for every connected integration and platform capability. Currently 150+ individual functions across these categories:

| Category | # Functions | Examples |
|----------|------------|---------|
| Default tools | 15 | bash, file_read, file_write, file_edit, grep, glob, Slack messaging, thread management |
| Linear (MCP) | 40+ | Create/update issues, list projects, manage milestones, search docs, manage cycles |
| Google Sheets (Pipedream) | 36 | Add rows, update cells, create spreadsheets, manage formatting, find data |
| Google Calendar (Pipedream) | 21 | Create/update events, list calendars, check free/busy, manage attendees |
| Google Search Console | 8 | Submit URLs for indexing, retrieve performance data |
| Bright Data | 9 | Scrape websites, SERP scraping, web unlocking |
| Clerk | 12 | User management, invitations, memberships |
| Polar | 5+1 | Proxy endpoints + custom API GET |
| Vercel | 9 | List/create/cancel deployments |
| GitHub | 2 | Git commands, GitHub CLI |
| Browser | 3 | Create session, download files, close session |
| Scheduling | 4 | Create/delete/trigger crons |
| Slack admin | 7 | List channels/users, join channels, invite users, check reactions |
| Thread orchestration | 2 | List running paths, get path info |
| Utils | 5 | Web search, email, image generation, file conversion, structured output |
| Viktor Spaces | 6 | Build/deploy web apps with database |
| Docs | 2 | Library documentation lookup |

### The Utils That Make It Work

Beyond tools, Viktor has utility modules:

- **slack_reader.py** (270 lines): Reads local Slack log files, parses messages, groups by channel and thread, provides context around new messages. This is how heartbeat checks for new activity.
- **heartbeat_logging.py** (150 lines): Logging utilities for execution logs and global logs. Tracks last heartbeat time for delta calculations.
- **workspace_tree.py** (250 lines): Generates a focused view of the workspace, highlighting relevant files for the current task.
- **browser.py**: Browser automation utilities for web scraping and form filling.

---

## 9. What Makes Viktor Different From a Generic Chatbot — Technical Summary

| Dimension | Generic Chatbot | Viktor |
|-----------|----------------|--------|
| **Memory** | None (or short conversation buffer) | Persistent files across all conversations, updated continuously |
| **Context** | Current conversation only | Company knowledge, team profiles, historical data, accumulated learnings |
| **Proactivity** | Only responds when asked | 4x/day scheduled check-ins actively seeking ways to help |
| **Code execution** | None | Full Python sandbox, writes and runs scripts |
| **Integrations** | None or limited | 3,141 available, 10 currently connected, can build custom ones |
| **Data accuracy** | Generated (hallucination risk) | Computed from real API calls and code execution |
| **Learning** | None (same quality on Day 1 and Day 100) | Accumulates knowledge — better on Day 30 than Day 1 |
| **Scheduling** | Cannot run autonomously | Runs background jobs 24/7 (crons) |
| **Error handling** | Generic apology | Documents failure, finds workaround, updates knowledge to prevent recurrence |
| **Personality** | Template responses | Adapts to team culture over time ("match the team's energy") |

---

## 10. The Complete File Map — Everything in Viktor's Workspace

```
/work/
├── company/
│   └── SKILL.md                    ← Company knowledge (Serprisingly profile)
│
├── team/
│   └── SKILL.md                    ← Team member profiles (8 people)
│
├── skills/                         ← 27 unique capability skills
│   ├── browser/SKILL.md
│   ├── codebase-engineering/SKILL.md
│   ├── docx-editing/SKILL.md
│   ├── excel-editing/SKILL.md
│   ├── general-tools/SKILL.md
│   ├── integrations/
│   │   ├── SKILL.md                ← Master integration guide
│   │   ├── clerk/SKILL.md
│   │   ├── coworker-github/SKILL.md
│   │   ├── google-search-console/SKILL.md
│   │   ├── google-sheets/SKILL.md
│   │   ├── google_calendar/SKILL.md
│   │   ├── linear/SKILL.md
│   │   ├── polar/SKILL.md
│   │   ├── polar-api/SKILL.md
│   │   └── vercel-token-auth/SKILL.md
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
│   └── workflow-discovery/SKILL.md
│
├── crons/                          ← 5 scheduled jobs
│   ├── heartbeat/
│   │   ├── task.json               ← 2,000-word proactive check-in instructions
│   │   ├── LEARNINGS.md            ← 82 lines of accumulated knowledge
│   │   ├── execution.log           ← Timestamped run history
│   │   └── todo.md                 ← Pending items
│   ├── mentions-issue-monitor/
│   │   ├── task.json               ← Channel monitoring instructions
│   │   ├── LEARNINGS.md            ← Issue classification learnings
│   │   ├── execution.log           ← 465 runs logged
│   │   ├── state.json              ← Last processed message timestamp
│   │   └── scripts/
│   │       └── check_new_messages.py ← Message parser script
│   ├── reports/daily-revenue/
│   │   ├── task.json               ← Points to Python script
│   │   └── execution.log           ← Run history
│   ├── workflow_discovery/
│   │   └── task.json               ← Team investigation instructions
│   └── channel_introductions/
│       └── task.json               ← Self-deleting intro cron
│
├── scripts/                        ← Reusable automation scripts
│   └── polar/
│       └── daily_revenue_report.py ← 180-line MRR report generator
│
├── data/                           ← Persistent data snapshots
│   └── polar_snapshots/
│       ├── 2026-02-15.json         ← MRR: $18,644.67 (191 subs)
│       ├── 2026-02-16.json         ← Used for Monday delta calc
│       └── 2026-02-22.json         ← Latest snapshot
│
├── logs/                           ← Activity logs (one per day)
│   ├── 2026-02-14/global.log      ← Day 1: onboarding, setup, Polar debugging
│   ├── 2026-02-15/global.log
│   ├── 2026-02-16/global.log
│   ├── 2026-02-17/global.log
│   ├── 2026-02-18/global.log
│   ├── 2026-02-19/global.log
│   ├── 2026-02-20/global.log
│   └── 2026-02-22/global.log      ← Today
│
├── sdk/                            ← Auto-generated SDK
│   ├── docs/
│   │   ├── tools.md                ← Master list of all available functions
│   │   └── available_integrations.json ← 3,141 integration catalog
│   ├── tools/                      ← Auto-generated Python wrappers
│   │   ├── default_tools.py
│   │   ├── mcp_linear.py           ← 40+ Linear functions
│   │   ├── pd_google_sheets.py     ← 36 Google Sheets functions
│   │   ├── pd_google_calendar.py   ← 21 Calendar functions
│   │   ├── pd_clerk.py             ← 12 Clerk functions
│   │   ├── pd_bright_data.py       ← 9 Bright Data functions
│   │   ├── pd_polar.py             ← 5 Polar proxy functions
│   │   ├── pd_google_search_console.py
│   │   ├── pd_vercel_token_auth.py
│   │   ├── custom_api_vvpjfwhokmnwxs2d5xgqpm.py ← Custom Polar API
│   │   ├── custom_api__6qjxjrroagkrab6efph2xh.py ← Second Polar API
│   │   ├── github_tools.py
│   │   ├── browser_tools.py
│   │   ├── email_tools.py
│   │   ├── docs_tools.py
│   │   ├── slack_admin_tools.py
│   │   ├── scheduled_crons.py
│   │   ├── thread_orchestration_tools.py
│   │   ├── utils_tools.py
│   │   └── viktor_spaces_tools.py
│   └── utils/                      ← Utility modules
│       ├── slack_reader.py         ← 270-line Slack log parser
│       ├── heartbeat_logging.py    ← 150-line logging utility
│       ├── workspace_tree.py       ← Workspace visualization
│       └── browser.py              ← Browser automation utils
│
└── $SLACK_ROOT/                    ← Synced Slack history
    ├── {channel_name}/
    │   ├── {YYYY-MM}.log          ← Monthly message logs
    │   └── threads/
    │       └── {thread_ts}.log    ← Thread conversation logs
    └── {user_name}/               ← DM logs (same structure)
```

---

*Document generated by Viktor from its own workspace. Every file path, line count, and technical detail was verified by reading the actual files — not generated from memory.*
