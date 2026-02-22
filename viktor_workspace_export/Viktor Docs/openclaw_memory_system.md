# OpenClaw Memory & Personalization System
## Complete Implementation Guide
### How to Build Persistent Memory for a Slack-Native AI Assistant

---

## Executive Summary

This document explains exactly how a Slack-native AI assistant maintains memory, personalization, and context across conversations. No vector database. No embeddings. No RAG pipeline. No mem0 or similar libraries.

**The architecture is: plain text files + a read-write discipline + a system prompt that enforces the discipline.**

This is not a simplification. This IS the system. And it works because of three facts:
1. Modern language models can process hundreds of pages of text in a single context window
2. A filesystem with grep is a fast and sufficient search engine for knowledge bases under 10,000 files
3. The bottleneck in AI memory is never retrieval — it's the discipline of writing things down in the first place

---

## Part 1: The Architecture

### 1.1 Why Plain Text Files (Not a Vector Database)

Vector databases (Pinecone, Weaviate, ChromaDB, Qdrant) solve a specific problem: finding semantically similar content in massive datasets. They're the right tool when you have millions of documents and need fuzzy semantic search.

An AI assistant's knowledge base is not that problem. Consider:
- A team of 20 people generates maybe 100 knowledge items per month
- After a year, that's ~1,200 items
- At ~500 words per item, that's 600,000 words — roughly 10 books
- A modern LLM context window can hold 1-2 books at once
- `grep` can search 600K words in milliseconds

You don't need semantic search. You need organized files and the discipline to read them.

**Why plain text specifically wins:**

| Dimension | Vector DB | Plain Text Files |
|-----------|-----------|-------------------|
| Transparency | Opaque (embeddings are unreadable) | Fully readable by humans and AI |
| Debugging | Hard (why did it retrieve X?) | Easy (just read the file) |
| Editing | Complex API calls | Open file, edit, save |
| Infrastructure | External service, API keys, costs | Zero — already on disk |
| Failure modes | Embedding drift, index corruption, service outage | File doesn't exist (obvious error) |
| Version control | Complex | Git works perfectly |
| Search | Semantic (sometimes finds wrong things) | Exact + regex (finds what you specify) |

**What about mem0?**

mem0 is a memory layer for AI agents that provides automatic memory extraction, storage, and retrieval. It abstracts memory management behind an API. The tradeoff: you gain convenience but lose transparency and control. For a personal assistant where your owner might want to see, edit, or understand their assistant's memory, transparent plain files are better. Your owner should be able to open a file and see exactly what you remember about them.

### 1.2 The Directory Structure

```
/workspace/
├── memory/
│   ├── owner/
│   │   └── profile.md           ← Core owner knowledge
│   │
│   ├── team/
│   │   └── members.md           ← Team member profiles
│   │
│   ├── company/
│   │   └── context.md           ← Company info, products, goals
│   │
│   ├── knowledge/
│   │   ├── {topic}/
│   │   │   └── KNOWLEDGE.md     ← Topic-specific knowledge
│   │   └── integrations/
│   │       └── {service}/
│   │           └── KNOWLEDGE.md ← Service-specific knowledge
│   │
│   ├── learnings/
│   │   └── LEARNINGS.md         ← Cross-session meta-learnings
│   │
│   └── conversations/
│       └── key_decisions.md     ← Important decisions and their context
│
├── tasks/                       ← Scheduled/recurring task configs
│   └── {task_name}/
│       ├── config.json
│       ├── state.json
│       ├── learnings.md
│       └── scripts/
│
├── data/                        ← Persistent data snapshots
│   └── snapshots/
│
├── scripts/                     ← Reusable automation scripts
│
└── logs/                        ← Activity logs
    └── {YYYY-MM-DD}.log
```

### 1.3 How Memory Flows Through the System

```
┌─────────────────────────────────────────────────┐
│                SYSTEM PROMPT                      │
│                                                   │
│  Contains:                                        │
│  • Core philosophy & behavior rules               │
│  • List of all knowledge files + descriptions     │
│  • Instructions to read files before acting        │
│  • Instructions to update files after learning     │
│                                                   │
│  The descriptions of each knowledge file are       │
│  AUTO-INJECTED into the system prompt so the AI    │
│  always knows WHAT knowledge exists, even before   │
│  reading the actual files.                         │
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│              AI INSTANCE STARTS                   │
│                                                   │
│  1. Reads system prompt (knows what files exist)  │
│  2. Reads relevant knowledge files (gets context) │
│  3. Reads task-specific state (knows what's done) │
│  4. Processes the user's request                  │
│                                                   │
│  Now has: instructions + knowledge + state + task │
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│              AI DOES WORK                         │
│                                                   │
│  • Writes scripts, calls APIs, gathers data       │
│  • Learns new facts (API endpoint changed)        │
│  • Learns preferences (owner likes CSV format)    │
│  • Notices patterns (this report runs weekly)     │
│                                                   │
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│              AI UPDATES MEMORY                    │
│                                                   │
│  • Updates owner/profile.md with new preferences  │
│  • Updates knowledge files with new facts          │
│  • Updates LEARNINGS.md with what worked/failed   │
│  • Updates state.json with current task state      │
│  • Logs action to daily log                       │
│                                                   │
│  These updated files are now available to ALL      │
│  future AI instances — including scheduled tasks.  │
└─────────────────────────────────────────────────┘
```

---

## Part 2: The Knowledge File System

### 2.1 Knowledge File Format

Every knowledge file uses this structure:

```markdown
---
name: {unique-identifier}
description: {One-line description of when to use this file.
              This description is auto-injected into the system prompt.}
---

## {Section Header}

{Content — facts, processes, examples}

## {Another Section}

{More content}

---
**Last verified:** {date}
**Note:** {Any caveats about staleness}
```

The YAML frontmatter is critical:
- `name`: Unique identifier, used for file management
- `description`: This exact string gets injected into the system prompt's `<available_skills>` section. It tells the AI WHEN to read this file. Write it as: "Use when [specific trigger/context]."

### 2.2 The Injection Mechanism

This is the key architectural insight: **the AI doesn't have to search for relevant knowledge — it's told what exists.**

In the system prompt, there's a section like:

```
<available_knowledge>
The following knowledge files are available. Read the full file
when working on a related task.

- memory/owner/profile.md: "Owner profile — preferences, work style,
  goals. Read before every interaction."
- memory/company/context.md: "Company info, products, revenue targets.
  Use when discussing business metrics or strategy."
- memory/knowledge/integrations/linear/KNOWLEDGE.md: "Linear project
  management. Contains workspace structure, team IDs, project IDs.
  Use when creating or managing issues."
- memory/knowledge/integrations/polar/KNOWLEDGE.md: "Polar billing API.
  Contains working endpoints, known issues, workarounds.
  Use when pulling revenue data."
- memory/learnings/LEARNINGS.md: "Cross-session learnings — what worked,
  what failed, patterns noticed. Read at start of complex tasks."
</available_knowledge>
```

When a user says "what's our MRR?" the AI sees `polar/KNOWLEDGE.md: "Use when pulling revenue data"` in its prompt and knows to read that file before doing anything. It doesn't need to search — the routing is done by the description matching.

### 2.3 What Goes in Each File

#### owner/profile.md
```markdown
---
name: owner-profile
description: Owner profile — preferences, goals, work style. Read before every interaction.
---

## Identity
- Name: Ojash
- Role: Founder & CEO
- Company: Serprisingly (AI Search Optimization Agency)
- Email: hello@ojash.com
- Timezone: Asia/Kolkata (IST)

## Communication Style
- Prefers direct, concise answers
- Appreciates data-backed insights
- Responds well to proactive suggestions
- Values transparency about limitations
- Uses voice notes (Slack transcribes them automatically)

## Current Priorities
- Mentions app revenue growth (target: $50K MRR)
- Content marketing (Forbes, Entrepreneur articles)
- OpenClaw personal AI assistant project
- Team productivity and automation

## Preferences
- Reports: Include delta from previous period, show progress to target
- Format: Tables with code blocks, not inline numbers
- Updates: Prefers channel posts over DMs for team-wide info
- Frequency: Daily revenue reports, weekly summaries

## History
- 2026-02-14: Installed Viktor, asked for MRR tracking
- 2026-02-14: Set up daily revenue reports (9 AM IST, Mon-Fri)
- 2026-02-22: Asked for architecture documentation for Forbes article
- 2026-02-22: Requested OpenClaw soul.md design

## Things They've Explicitly Asked Me to Remember
- (Add items here when owner says "remember this" or similar)
```

#### team/members.md
```markdown
---
name: team-members
description: Team member profiles — roles, communication styles, areas of work. Read when interacting with or discussing team members.
---

## Shashwat
- Role: Full-stack developer (primary engineer)
- Works on: Mentions app frontend/backend, bug fixes
- Communication: Direct, sometimes uses Hindi in casual messages
- Notes: Prefers using @Linear bot for quick ticket creation

## Naman
- Role: Developer
- Works on: Frontend features, UI implementation
- Communication: Casual, uses emoji frequently, makes jokes
- Notes: Once joked about Viktor replacing Linear bot — treat as humor, not complaint

## {Name}
- Role: ...
- Works on: ...
- Communication: ...
- Notes: ...
```

#### learnings/LEARNINGS.md
```markdown
---
name: cross-session-learnings
description: Accumulated learnings across all sessions — what works, what fails, patterns, preferences. Read before complex tasks.
---

# Learnings

## Week of 2026-02-14

### Infrastructure
- Polar API: Pipedream proxy blocks api.polar.sh (domain not whitelisted)
- Polar workaround: Use /v1/subscriptions/export (no trailing slash needed)
- Custom API integration created as fallback (ID: 6QjXJRRoaGkraB6efPh2xH)
- Clerk integration: Secret key invalid (not in sk_live_/sk_test_ format)

### What Worked
- Revenue report script with 3-retry exponential backoff — survived Polar API timeouts
- Storing daily snapshots for delta calculation — enables "up/down from yesterday"
- Reacting to team messages with emoji before commenting — less intrusive than posting

### What Failed
- Tried direct Polar API endpoints with trailing slashes — 307 redirect loop
- Tried URL-encoding trailing slashes — proxy strips them before forwarding
- First Monday revenue report failed (network timeout) — fixed with retry logic

### Patterns Noticed
- Ojash asks for MRR data frequently — automated with daily cron
- Team is most active 11 AM - 7 PM IST, quiet before 10 AM
- Weekend messages are almost always casual (not bugs/issues)

### Preferences Learned
- Ojash prefers USD for all revenue numbers
- Revenue reports should include progress to $50K target
- Ojash prefers honest "I can't" over fake "I'll try"
```

#### knowledge/integrations/{service}/KNOWLEDGE.md
```markdown
---
name: linear-integration
description: Linear project management. Contains workspace IDs, project structure, team members, and working API examples. Use when creating or managing issues.
---

## Account Structure
- Organization: Serprisingly
- Team: "Mentions App" (ID: 85af8f8d-xxxx-xxxx-xxxx)
- 17 active projects
- 8 team members

## Key IDs
| Resource | ID | Notes |
|----------|----|-------|
| Team | 85af8f8d | Primary team for all issues |
| Bug label | abc123 | Use for bug reports |
| Feature label | def456 | Use for feature requests |
| Enhancement label | ghi789 | Use for improvements |

## Working Examples

### Create an Issue
```python
from sdk.tools import mcp_linear
result = await mcp_linear.linear_create_issue(
    team_id="85af8f8d",
    title="Bug: ...",
    description="...",
    label_ids=["abc123"],
    priority=2  # 1=urgent, 2=high, 3=medium, 4=low
)
```

### Search Issues
```python
result = await mcp_linear.linear_search_issues(query="payment bug")
```

## Known Issues
- None currently

---
**Last verified:** 2026-02-14
```

---

## Part 3: The Read-Write Discipline

This is the most important part of the entire system. Without this discipline, files are just dead storage.

### 3.1 The System Prompt Instructions

The following instructions must be in your system prompt to enforce the discipline:

```
<memory_discipline>
## Memory = Your Knowledge

Knowledge files store facts, processes, preferences, and learnings.
They live at `memory/{category}/` and have YAML frontmatter with
`name` and `description` fields. The descriptions are listed in
<available_knowledge> — always read the full file before acting on
related tasks.

### Before Every Task
1. Read memory/owner/profile.md — know who you're helping
2. Read relevant knowledge files — know what you already know
3. Read memory/learnings/LEARNINGS.md — know what you've learned
4. Read task-specific state files — know what's already been done

### After Every Task
5. Did you learn a new fact? → Update the relevant knowledge file
6. Did you learn a new lesson? → Update LEARNINGS.md
7. Did you learn about the owner? → Update owner/profile.md
8. Did task state change? → Update state.json
9. Log what you did → Append to logs/{date}.log

### When to Create New Knowledge Files
- A new integration is connected → Create memory/knowledge/integrations/{name}/KNOWLEDGE.md
- A new project or domain emerges → Create memory/knowledge/{topic}/KNOWLEDGE.md
- A new team member appears → Update memory/team/members.md

### Key Rules
- Don't guess or speculate — read files, query APIs, verify facts
- 1-2 queries are NEVER enough for quality output
- A failure you document is a failure you never repeat
- Stale knowledge is worse than no knowledge — update or delete
</memory_discipline>
```

### 3.2 The Read Pattern (Before Acting)

When an AI instance starts, it should:

```python
# Pseudocode for the read pattern
def before_task(task_context):
    # Step 1: Always read owner profile
    owner = read_file("memory/owner/profile.md")

    # Step 2: Read relevant knowledge based on task
    relevant_files = match_task_to_knowledge(task_context)
    # This matching is done by the AI itself — it reads the
    # <available_knowledge> list in its prompt and decides
    # which files are relevant to the current task.
    for file in relevant_files:
        knowledge = read_file(file)

    # Step 3: Read cross-session learnings
    learnings = read_file("memory/learnings/LEARNINGS.md")

    # Step 4: Read task-specific state if this is a recurring task
    if is_recurring_task(task_context):
        state = read_file(f"tasks/{task_name}/state.json")

    # Now the AI has: owner context + domain knowledge + learnings + state
    # It can proceed with full context
```

### 3.3 The Write Pattern (After Learning)

```python
# Pseudocode for the write pattern
def after_task(task_result, things_learned):
    # Step 5: Update knowledge if new facts discovered
    if new_facts:
        update_file("memory/knowledge/{topic}/KNOWLEDGE.md", new_facts)
        # Example: "Polar API /v1/export endpoint works without trailing slash"

    # Step 6: Update learnings if new lessons
    if new_lessons:
        append_to_file("memory/learnings/LEARNINGS.md", new_lessons)
        # Example: "### FAILURE: Polar proxy strips trailing slashes"

    # Step 7: Update owner profile if preferences learned
    if new_preferences:
        update_file("memory/owner/profile.md", new_preferences)
        # Example: "Prefers USD for all revenue numbers"

    # Step 8: Update task state
    if task_state_changed:
        write_file(f"tasks/{task_name}/state.json", new_state)
        # Example: {"last_processed_ts": "1771218138.984499"}

    # Step 9: Log the action
    append_to_file(f"logs/{today}.log",
        f"[{timestamp}] {action_description}")
```

### 3.4 How Updates Actually Happen

The AI uses file editing tools (read file → modify content → write file). Here's what an actual update looks like:

**Scenario:** Owner says "I prefer CSV format for data exports."

**AI's internal process:**
1. AI reads `memory/owner/profile.md`
2. Finds the "Preferences" section
3. Adds: `- Data exports: Prefers CSV format over JSON or Excel`
4. Writes the updated file

**File edit:**
```
# Old content:
## Preferences
- Reports: Include delta from previous period
- Format: Tables with code blocks

# New content:
## Preferences
- Reports: Include delta from previous period
- Format: Tables with code blocks
- Data exports: Prefers CSV format over JSON or Excel
```

This is a standard file edit operation — the same tool that edits code files. Nothing special. The intelligence is in the AI knowing WHEN to make the update, not in the mechanism of making it.

---

## Part 4: Personalization Engine

### 4.1 How Personalization Accumulates

Personalization isn't a feature you build — it's a side effect of the read-write discipline applied consistently.

```
Week 1:
  owner/profile.md has: Name, role, company, timezone
  → AI gives generic, polite responses

Week 2:
  owner/profile.md now also has: Communication style preferences,
  work hours, preferred report format, key projects
  → AI matches their tone, formats output how they like it

Week 4:
  owner/profile.md now also has: Decision-making patterns, pet peeves,
  frequently asked questions, project history
  → AI anticipates needs, avoids known annoyances, references past context

Week 12:
  owner/profile.md is a detailed profile + LEARNINGS.md has 200+ entries
  → AI behaves like a seasoned colleague who knows the business deeply
```

### 4.2 What to Personalize

| Dimension | How to Learn It | Where to Store It |
|-----------|----------------|-------------------|
| Communication tone | Observe their messages — formal? casual? emoji? | owner/profile.md → Communication Style |
| Report format | Note when they ask for changes ("Can you make this a table?") | owner/profile.md → Preferences |
| Work schedule | Track when they're active in Slack | owner/profile.md → Work Hours |
| Priorities | Listen to what they discuss most, what they ask about | owner/profile.md → Current Priorities |
| Frustrations | Note complaints, repeated issues, things that annoy them | owner/profile.md → Pain Points |
| Decision patterns | Track how they make decisions (data-driven? gut? consensus?) | owner/profile.md → Decision Style |
| Project context | Track which projects they mention, status updates | company/context.md → Projects |
| Team dynamics | Observe who works on what, who talks to whom | team/members.md |

### 4.3 The Observation Loop

Personalization comes from observation, not interrogation. Don't ask "What's your communication style?" — observe it.

**In the heartbeat cron (scheduled check-in):**
```
Each heartbeat:
1. Read new Slack messages since last heartbeat
2. For each message from the owner:
   - Note any preferences expressed ("I wish this was in a table")
   - Note any frustrations expressed ("This keeps breaking")
   - Note any priorities mentioned ("We need to hit $50K MRR")
   - Note any new projects or initiatives mentioned
3. Update owner/profile.md with any new observations
4. Update LEARNINGS.md with any new patterns
```

**In regular interactions:**
```
After each conversation:
- Did the owner correct my format? → Update format preference
- Did the owner ask me to do something differently? → Update approach preference
- Did the owner mention a new goal/project/priority? → Update priorities
- Did the owner express frustration? → Note what caused it
```

### 4.4 Team Member Profiling

Build team profiles gradually through observation:

**5-Step Profiling Process:**

```
Step 1: IDENTIFY
  - When a new person appears in Slack, create a basic entry
  - Record: Name, any visible role information

Step 2: OBSERVE
  - Track what channels they're active in
  - Note what topics they discuss
  - Observe their communication style

Step 3: CONTEXT
  - Cross-reference with company info (website, LinkedIn)
  - Note their email domain and role from any visible signatures
  - Observe how others refer to them

Step 4: INTERACT
  - After your first direct interaction, note:
    - How they prefer to communicate
    - What kind of help they need
    - Their response to your suggestions

Step 5: MAINTAIN
  - Keep profiles updated as roles change
  - Note preference changes over time
  - Track their key projects and responsibilities
```

**Rules for team profiling:**
- Learn by observation, not interrogation
- Never share one person's profile with another
- Respect that some people don't want AI interaction — note and respect this
- Update profiles when things change — don't let them go stale

---

## Part 5: Conversation Memory

### 5.1 How Conversation History Works

For a Slack-native assistant, conversation history lives in Slack logs synced to the filesystem:

```
slack_history/
├── {channel_name}/
│   ├── {YYYY-MM}.log        ← Monthly message logs
│   └── threads/
│       └── {thread_ts}.log  ← Individual thread logs
└── {user_name}/              ← DM logs (same structure)
```

**Log format:**
```
[1771082209.051849] @Ojash: What is our MRR on Polar for Mentions?
[1771082330.123456] @Viktor: MRR is $18,743.67 across 192 active subscriptions...
```

**How the AI uses these:**
- `grep` to search for past conversations about a topic
- Read specific thread logs for full conversation context
- The heartbeat cron reads recent logs to find new activity

### 5.2 Why Not Store Conversations in Memory Files?

Conversations are already stored in Slack logs. Duplicating them in memory files would be wasteful. Instead:

- **Conversations** → Searchable via grep on Slack log files
- **Facts extracted from conversations** → Stored in knowledge files
- **Preferences learned from conversations** → Stored in owner/profile.md
- **Decisions made in conversations** → Stored in key_decisions.md

The pattern is: **conversations are raw data. Memory files are processed insights.**

### 5.3 Key Decisions Tracking

For important decisions, maintain a dedicated file:

```markdown
# Key Decisions

## 2026-02-14: Daily Revenue Report Format
- **Decision:** USD, not INR. Include progress to $50K target.
- **Context:** Ojash asked for MRR report. Initially showed raw numbers, then added target tracking.
- **Thread:** slack/Ojash/threads/1771082209.051849

## 2026-02-14: Issue Monitor Frequency
- **Decision:** Every 2 minutes, 24/7
- **Context:** Needed fast detection of customer-reported bugs in #mentions
- **Thread:** Created during onboarding

## 2026-02-22: Workspace Export Rules
- **Decision:** Can export workspace content. Exclude: Slack history (privacy), env files (credentials), platform internals (not ours).
- **Context:** Owner requested workspace export for editorial purposes.
```

---

## Part 6: How Multi-Instance Memory Works

### 6.1 The Instance Model

The AI assistant is not a single running process. It's a pattern of:
1. **Trigger** → message, cron schedule, thread reply
2. **New instance starts** → fresh AI session with the system prompt
3. **Instance reads files** → gets all accumulated knowledge
4. **Instance does work** → writes scripts, calls APIs, sends messages
5. **Instance writes files** → updates knowledge with anything new
6. **Instance terminates** → session ends, nothing in "RAM"
7. **Files persist** → next instance starts with updated knowledge

### 6.2 How Instances Share Knowledge

Instances don't share memory directly. They communicate through files:

```
Instance A (9 AM heartbeat):
  - Reads LEARNINGS.md
  - Discovers new Slack activity
  - Updates LEARNINGS.md: "Oxylabs errors increasing — monitor"
  - Terminates

Instance B (1 PM heartbeat):
  - Reads LEARNINGS.md ← sees the Oxylabs note from Instance A
  - Checks Oxylabs status
  - Updates LEARNINGS.md: "Oxylabs recovered to 95%"
  - Terminates

Instance C (user asks about monitoring):
  - Reads LEARNINGS.md ← sees the full Oxylabs timeline
  - Can give accurate answer about the issue and its resolution
```

### 6.3 State Files for Recurring Tasks

For tasks that run repeatedly (monitoring, reports), use state files to track progress:

```json
// tasks/issue-monitor/state.json
{
  "last_processed_ts": "1771218138.984499",
  "processed_threads": [
    "1770983993.681649",
    "1771079537.964829"
  ],
  "created_issues": [],
  "notes": "No new issues since last run"
}
```

Each instance:
1. Reads state.json → knows what's already been processed
2. Processes only NEW items since `last_processed_ts`
3. Updates state.json with new timestamp and results
4. Next instance picks up where this one left off

### 6.4 Preventing Conflicts

What if two instances try to update the same file simultaneously?

**Reality:** For a personal assistant, this rarely happens. Instances are typically sequential (crons don't overlap, user messages process one at a time). But if it does:

- **Log files:** Append-only, so concurrent writes are safe
- **State files:** Only one cron instance runs at a time (built into the scheduler)
- **Knowledge files:** If two instances update different sections, both edits survive. If they update the same section, the last writer wins — but the change is usually additive (adding a bullet point), not destructive.

---

## Part 7: Search & Retrieval

### 7.1 How the AI Finds Relevant Knowledge

Three mechanisms, in order of priority:

**1. Prompt injection (automatic):**
The system prompt contains a list of all knowledge files with descriptions. The AI reads the descriptions and decides which files to load for the current task. This is 90% of the routing.

**2. grep (explicit search):**
When the AI needs to find something specific:
```bash
# Find all mentions of "Polar" across all knowledge files
grep -r "Polar" memory/ --include="*.md"

# Find when the owner discussed revenue
grep -r "revenue\|MRR\|ARR" slack_history/Ojash/
```

**3. File listing (exploration):**
When the AI isn't sure what knowledge exists:
```bash
# List all knowledge files
find memory/ -name "*.md" -type f

# List with descriptions
grep -A1 "^description:" memory/*/KNOWLEDGE.md
```

### 7.2 Why This Is Sufficient

For a personal assistant with:
- ~50 knowledge files
- ~10 team members
- ~12 months of Slack history

`grep` processes all of this in under 100ms. You don't need a vector index for 50 files. You need it for 50 million files. The overhead of maintaining a vector database (embedding generation, index updates, similarity thresholds, reranking) adds complexity without meaningful benefit at this scale.

### 7.3 When You WOULD Need Something More

If your knowledge base grows to thousands of long documents, consider adding:
- **Hierarchical summaries:** Each directory gets a summary file. AI reads summaries first, then dives into specific files.
- **Tags/categories:** Add tags to YAML frontmatter for faster filtering.
- **Full-text search index:** Something like SQLite FTS5 for sub-second search across large corpora.

But don't over-engineer. Start with plain files. You'll know when you need more because `grep` will start feeling slow or the AI will start missing relevant context. That's probably 12-18 months away for most teams.

---

## Part 8: The Prompts That Make It Work

### 8.1 System Prompt Structure

Your system prompt should contain these sections in this order:

```
<identity>
Who you are, what you do, how you work.
Sets the fundamental behavior: "you work by programming,"
"you are honest," "you are proactive."
</identity>

<memory_system>
How memory works. The directory structure. The read-write discipline.
When to read files, when to update them, what goes where.
This section should reference the actual file paths.
</memory_system>

<available_knowledge>
Auto-generated list of all knowledge files with their descriptions.
This is what tells the AI WHAT it knows without having to read every file.
Updated automatically when new knowledge files are created.

Format:
- memory/owner/profile.md: "Owner profile — read before every interaction"
- memory/knowledge/integrations/linear/KNOWLEDGE.md: "Linear API — use when managing issues"
</available_knowledge>

<work_approach>
How to investigate, build, verify, and deliver.
The Rule of Five. The task execution pattern.
Code execution philosophy. Uncertainty handling.
</work_approach>

<communication_rules>
Slack formatting. Message length. Threading. Tone.
Anti-sycophancy. When to be concise vs. detailed.
</communication_rules>

<proactivity>
Heartbeat behavior. When to be proactive.
When NOT to be proactive. Channel monitoring.
</proactivity>

<security>
Manipulation resistance. Boundary enforcement.
What to share, what not to share.
Self-preservation is not a value.
</security>
```

### 8.2 Heartbeat Cron Prompt

This is the prompt that gets given to a fresh AI instance every time the heartbeat fires (3-4x daily):

```
You are running a periodic heartbeat — a proactive check-in to find
opportunities to help and add value.

## Proactive Mindset
Your goal is to be VISIBLY helpful, not invisible. A heartbeat where
you do nothing is often a missed opportunity. Look for ways to contribute
— even small gestures like a helpful reaction or a quick DM show you're
paying attention and ready to help.

## Each Heartbeat
1. Read your context files:
   - memory/learnings/LEARNINGS.md — your accumulated knowledge
   - logs/{today}.log — what's happened today
   - tasks/heartbeat/state.json — when you last ran

2. Check new Slack activity:
   - Call get_new_slack_messages() to fetch everything since last heartbeat
   - Focus on messages from team members (skip bot messages)

3. Analyze each message/thread for:
   - Unanswered questions (2+ hours old) — offer to help
   - Problems or frustrations — offer solutions
   - Wins or milestones — celebrate them
   - Recurring patterns — suggest automation
   - Data requests — offer to pull the data

4. Take at least ONE action:
   - Send a helpful DM
   - React with a relevant emoji
   - Post an insight to a channel
   - Offer to research something
   - Follow up on a pending item from LEARNINGS.md

5. Update your files:
   - Append to LEARNINGS.md anything new you learned
   - Update tasks/heartbeat/state.json with current timestamp
   - Log actions to logs/{today}.log

## Rules
- Match the team's energy. If they're casual, be casual.
- DM for personal/specific offers. Channel message for team-wide insights.
- Don't repeat yourself. Check LEARNINGS.md for what you've already said.
- Weekend/evening heartbeats: lighter touch. React more, message less.
- If something needs real work, spawn a dedicated thread — don't try to
  do everything in the heartbeat.
```

### 8.3 Channel Monitor Cron Prompt

```
You monitor #{channel_name} for actionable items — bugs, feature requests,
and questions that need tracking.

## Each Run
1. Load state: tasks/channel-monitor/state.json (last_processed_ts)
2. Run scripts/check_new_messages.py to get messages newer than state
3. Filter out bot messages (your own, other bots)
4. For each human message/thread, classify:
   - BUG: Customer reporting broken functionality → propose a ticket
   - FEATURE: Customer requesting new functionality → propose a ticket
   - QUESTION: Question that needs an answer → flag for team
   - CHAT: General discussion → log but don't act
5. For BUG/FEATURE: Send a message with the proposed ticket to the channel,
   including an approval button. Wait for team approval before creating.
6. Update state.json with new last_processed_ts
7. Update tasks/channel-monitor/learnings.md with any classification insights

## Classification Learnings (from previous runs)
- Weekend messages are almost always casual (not bugs)
- Messages with code snippets or error screenshots are likely bugs
- Messages starting with "it would be nice if" are feature requests
- Check if a Linear ticket was already created by another bot — don't duplicate
```

### 8.4 Automated Report Cron Prompt

```
Script: scripts/daily_revenue_report.py

This is a script cron — no AI agent needed. The script:
1. Calls the billing API with retry logic (3 attempts, exponential backoff)
2. Parses response and computes MRR by plan
3. Loads yesterday's snapshot from data/snapshots/
4. Calculates delta (MRR change, subscriber gains/losses)
5. On Mondays: includes week-over-week recap
6. Formats as Slack message and posts to #{channel}
7. Saves today's snapshot for tomorrow's comparison
```

---

## Part 9: Bootstrapping — Day 1 Setup

### 9.1 What Happens on First Install

When OpenClaw is first installed in a workspace:

```
Phase 1: OBSERVE (First 30 minutes)
├── List all Slack channels → understand workspace structure
├── List all Slack users → identify team members
├── Read recent messages in each channel → understand what's discussed
└── Output: Initial company/context.md, team/members.md, owner/profile.md

Phase 2: EXPLORE INTEGRATIONS (Next 30 minutes)
├── For each connected integration:
│   ├── Call read-only endpoints to map account structure
│   ├── Test key operations
│   ├── Document working approaches and known issues
│   └── Output: memory/knowledge/integrations/{name}/KNOWLEDGE.md
└── Log integration status (working, broken, partially working)

Phase 3: INTRODUCE (First hour)
├── Send a message to the owner summarizing what you found
├── List what integrations are working
├── Ask if there's anything specific they want you to start doing
└── Propose 2-3 immediate automation opportunities

Phase 4: LEARN (First week)
├── Run heartbeats 3-4x daily
├── Build owner profile through observation
├── Build team profiles through observation
├── Track patterns in what the team does manually
└── Propose automations based on observed patterns
```

### 9.2 The Initial System Prompt Additions

On Day 1, the `<available_knowledge>` section is nearly empty:

```
<available_knowledge>
- memory/owner/profile.md: "Owner profile — basic info only, needs enrichment"
- memory/company/context.md: "Company context — needs exploration"
- memory/learnings/LEARNINGS.md: "Fresh install — no learnings yet"
</available_knowledge>
```

By Day 30, it looks like:

```
<available_knowledge>
- memory/owner/profile.md: "Detailed owner profile — preferences, style, priorities, history"
- memory/company/context.md: "Company: Serprisingly, products, revenue targets, team structure"
- memory/team/members.md: "8 team members with roles, styles, and interaction notes"
- memory/knowledge/integrations/linear/KNOWLEDGE.md: "Linear — workspace IDs, project structure, 40+ API functions"
- memory/knowledge/integrations/polar/KNOWLEDGE.md: "Polar billing — working endpoints, export workaround, snapshot approach"
- memory/knowledge/integrations/google-sheets/KNOWLEDGE.md: "Google Sheets — proxy tools work, built-in actions broken"
- memory/knowledge/integrations/clerk/KNOWLEDGE.md: "Clerk — auth broken, API surface documented for when fixed"
- memory/knowledge/seo/KNOWLEDGE.md: "SEO processes and client deliverables"
- memory/knowledge/mentions-app/KNOWLEDGE.md: "Product architecture, deployment, monitoring"
- memory/learnings/LEARNINGS.md: "150+ entries: infrastructure patterns, team habits, failure recoveries"
</available_knowledge>
```

This compounding is the entire point. The system gets more useful every day, automatically, without any manual setup.

---

*This document describes an architecture, not a specific implementation. Adapt the file structures, prompts, and patterns to your specific platform and needs. The principles — persistent files, read-write discipline, observation-based personalization, and honest uncertainty handling — are universal.*
