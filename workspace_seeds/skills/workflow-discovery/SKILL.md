---
name: workflow-discovery
description: Investigate team members' work via Slack, identify pain points, and propose personalized automation workflows. Use when discovering how Lucy can help the team or exploring automation opportunities.
---

# Workflow Discovery — 6-Phase Process

The goal is to deeply understand what people on the team spend time on and find meaningful ways Lucy can help. This is not surface-level — read extensively, profile thoroughly, and propose concrete implementations.

## Before Starting: Create Your Discovery File

Create `crons/workflow-discovery/discovery.md` to track all progress:

```markdown
# Workflow Discovery Progress

## Connected Integrations
- [ ] Slack (always connected)
- [ ] Gmail / Outlook
- [ ] Google Calendar / Outlook Calendar
- [ ] Linear / Jira / Asana
- [ ] Google Drive / Notion
- [ ] HubSpot / Salesforce
(check via COMPOSIO_MANAGE_CONNECTIONS)

## Team Members
| Person | Role | Timezone | Investigated | Ideas | Proposals | Accepted |
| ------ | ---- | -------- | ------------ | ----- | --------- | -------- |

## Ideas Per Person
### @person_name
**Role:** ...
**Working hours:** ... (from Slack timezone)
**Pain points observed:**
- [evidence: message link or quote]
**Workflow ideas:**
1. ...
```

---

## Phase 1: Audit Connected Integrations

Use `COMPOSIO_MANAGE_CONNECTIONS` to understand what integrations are available. This determines what workflows are possible.

**Key question:** What tools does this team already use? If Gmail and Google Calendar are connected, scheduling workflows are possible. If Linear is connected, issue tracking automation is possible.

Record findings in `discovery.md` under Connected Integrations.

## Phase 2: Deep Investigation Per Person

Focus on the **10-15 most active team members**. For each person:

### Read Their Slack Messages Extensively
Not 1-2 searches — really read. Use `COMPOSIO_SEARCH_TOOLS` → "slack search messages" and search for:
- Messages from that person in the last 2 weeks
- Their activity across multiple channels
- Threads they've started or been active in
- Questions they ask repeatedly
- Complaints or frustrations they express
- Handoffs with other team members

### Profile What You Discover
For each person, document:
- **Role and responsibilities** — what they own
- **Working patterns** — when they're active, what channels they use
- **Pain points** — things they complain about, ask for help with, or do manually
- **Recurring tasks** — things they do daily/weekly that follow a pattern
- **Tool usage** — what integrations they rely on
- **Timezone** — from their Slack profile (important for scheduling)

### Update `team/SKILL.md`
Add your understanding of each person to the permanent team knowledge file.

## Phase 3: Generate Automation Ideas

Target: **at least 2-3 ideas per investigated person + general team-wide ideas**.

For each idea, think through the full implementation:

### Lucy Cron (Scheduled Agent Task)
Lucy runs on schedule with full judgment capability.
- **Best for:** complex analysis, multi-step reasoning, varied tasks, reporting
- **Example:** "Every morning, read #engineering for blockers and summarize in #standup"
- **Implementation:** `task.json` with detailed description

### Code Script (Deterministic Automation)
A Python script runs on schedule via `COMPOSIO_REMOTE_WORKBENCH`.
- **Best for:** data pipelines, simple aggregations, API-to-API syncs, monitoring
- **Example:** "Every hour, check if Stripe revenue exceeds daily target and post to #sales"
- **Implementation:** `task.json` that instructs Lucy to run a specific script

### On-Demand Skill
Lucy does it when asked — no schedule needed.
- **Best for:** research tasks, one-off analysis, complex queries
- **Example:** "When asked about a customer, pull data from HubSpot + Slack history"
- **Implementation:** Create a new skill in workspace `skills/`

### Hybrid (Schedule + Judgment)
A scheduled check triggers Lucy only when action is needed.
- **Best for:** anomaly detection, exception handling, conditional notifications
- **Example:** "Daily script checks for support tickets > 24h old, Lucy investigates and escalates"

## Phase 4: Craft Personalized Proposals

### Proposal Format (via Slack DM to the person)

> **What I noticed:** [specific observation with evidence]
>
> **What I could do:** [clear description of the workflow]
>
> **How it works:** [Lucy cron / code script / on-demand — be specific]
>
> **What you'd get:** [concrete output — "a Slack message in #channel every morning at 9am with..."]
>
> **What I'd need:** [any inputs, permissions, or integrations required]

### Targeting
- DM person-specific workflows to that person directly
- Post team-wide workflows to a relevant channel
- Lead with what you observed — show you understand their work
- Keep it to 1-2 proposals per message — don't overwhelm

## Phase 5: Track Proposals in `discovery.md`

After proposing, track the status:

```markdown
### @person_name — Proposals
1. ✅ Accepted: "Daily standup summary" → Created cron at crons/standup-summary/
2. ❌ Rejected: "Weekly report automation" — reason: "I like writing these myself"
3. ⏳ Pending: "Meeting prep automation" — waiting for response
```

## Phase 6: Follow Up and Implement

- For accepted proposals: create the cron/skill immediately and confirm it's running
- For rejected proposals: log the reason, never re-propose the same idea
- For pending proposals: follow up once after 3 days, then drop it
- For implemented workflows: check `execution.log` after first few runs and tune based on results

---

## Anti-Patterns

**Don't:**
- Stop at 3 ideas when there are 8+ active team members
- Skip the discovery.md tracking file — it's essential for continuity
- Propose vague "I could help with X" without implementation details
- Only propose reports/summaries — Lucy can do REAL WORK (create documents, manage tickets, draft emails)
- Set up crons immediately without asking clarifying questions first
- Re-propose workflows that were previously rejected
- Ignore timezone differences when proposing scheduled workflows
- Propose workflows that require integrations that aren't connected (mention them as "possible if you connect X")

**Do:**
- Investigate every significant team member — not just the loudest ones
- Track everything in discovery.md — future runs will read this
- Think through exactly how each workflow would be implemented
- Note integration opportunities even if not connected yet
- Ask clarifying questions before setting up approved workflows
- Use evidence from Slack when proposing ("I noticed you spend time on X every Monday...")
- Consider cross-timezone collaboration patterns
