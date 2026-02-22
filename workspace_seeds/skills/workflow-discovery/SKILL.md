---
name: workflow-discovery
description: Investigate team members' work via Slack, identify pain points, and propose personalized automation workflows. Use when discovering how Lucy can help the team or exploring automation opportunities.
---

# Discovering How Lucy Can Help

The goal is to understand what people on the team spend time on and find meaningful ways Lucy can help.

## First: Create Your Discovery File

Before starting, create `crons/workflow_discovery/discovery.md` to track your work:

```markdown
# Workflow Discovery Progress

## Team Members Investigated
| Person | Role | Investigated | Ideas Found | Proposals Made |
| ------ | ---- | ------------ | ----------- | -------------- |

## Connected Integrations
- [ ] List what's connected (check via COMPOSIO_MANAGE_CONNECTIONS)

## Ideas Per Person
### @person1
**Role:** ...
**Pain points observed:**
- ...
**Workflow ideas:**
1. Idea: ...
   - Implementation: Lucy cron / code script / on-demand
   - Requires: [integrations needed]
   - Output: What they'd get
```

## Phase 1: Investigate Integrations

Use `COMPOSIO_MANAGE_CONNECTIONS` to understand what integrations are currently connected.

## Phase 2: Deep Investigation Per Person

Focus on the **10-15 most active team members**:

### Research Their Work
- Read their Slack messages extensively (not 1-2 searches — really read)
- What do they spend time on?
- What do they complain about?
- What recurring tasks do they mention?
- What handoffs do they have with others?

### Document in discovery.md
For each person, write down:
- Their role and responsibilities
- Pain points you observed (with evidence from Slack)
- At least 1-2 workflow ideas specific to them

### Update team/SKILL.md
Add your understanding of each person to the permanent team knowledge.

## Phase 3: Generate Ideas

### Target: At Least 3 Per Person + General Ones

For each idea, think through implementation:

**Lucy Cron (scheduled task):**
- Lucy runs on schedule and does the work
- Good for: complex analysis, judgment calls, varied tasks

**Code Script (automated):**
- A Python script runs on schedule
- Good for: data pipelines, simple aggregations, API-to-API syncs

**On-Demand Skill:**
- Lucy does it when asked
- Good for: research tasks, one-off analysis

**Hybrid:**
- Scheduled check + Lucy judgment
- Example: "Daily script checks for anomalies, Lucy investigates and reports only if something's wrong"

## Phase 4: Propose Workflows

### Proposal Format

For each workflow you propose via Slack:

1. **What I observed** — The pain point or opportunity
2. **What I'd do** — Clear description of the workflow
3. **How it would work** — Lucy cron, code script, on-demand, etc.
4. **What you'd get** — The output/benefit
5. **What I'd need** — Any inputs, permissions, or integrations required

### Propose to the Right People
- DM person-specific workflows to that person
- Post general workflows to a relevant channel
- Lead with what you observed — show you understand their work
- Keep it concise — 1-2 proposals at a time

## Anti-Patterns

**Don't:**
- Stop at 3 ideas when there are 8+ active team members
- Skip the discovery.md tracking file
- Propose vague "I could help with X" without implementation details
- Only propose reports/summaries — Lucy can do REAL WORK
- Set up crons immediately without asking clarifying questions first

**Do:**
- Investigate every significant team member
- Track everything in discovery.md
- Think through exactly how each workflow would be implemented
- Note integration opportunities (even if not connected yet)
- Ask clarifying questions before setting up approved workflows
