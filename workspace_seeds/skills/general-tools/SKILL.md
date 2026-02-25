---
name: general-tools
description: Search the web, send emails, generate images, manage calendars, and convert files. Use when a task needs one of these general-purpose capabilities.
---

# General Tools

Lucy has access to thousands of tools through Composio's meta-tools. The key insight: **you don't need to know tool names upfront**. Use `COMPOSIO_SEARCH_TOOLS` to discover what's available for any task.

## Discovery Pattern (use this for everything)

```
1. COMPOSIO_SEARCH_TOOLS → describe what you need in plain English
2. Review returned tool names and descriptions
3. COMPOSIO_MULTI_EXECUTE_TOOL → call the discovered tool with arguments
```

**If you're about to say "I don't have access to X", STOP and search first.**

## Tool Categories

### Web Search & Research
**Discovery query:** `"web search"`, `"search internet"`, `"google search"`

When to use:
- Real-time data (weather, news, stock prices, events)
- Factual lookups your training data might not cover
- Verifying current information before giving an answer
- Competitive research, market data, product comparisons

### Email (Gmail / Outlook)
**Discovery query:** `"gmail send"`, `"outlook email"`, `"email"`

| Operation | Discovery Query |
|-----------|----------------|
| Send email | `"gmail send email"` or `"outlook send"` |
| Read inbox | `"gmail list emails"` or `"outlook messages"` |
| Search emails | `"gmail search"` or `"outlook search"` |
| Reply to email | `"gmail reply"` or `"outlook reply"` |
| Manage labels/folders | `"gmail labels"` or `"outlook folders"` |

When sending on behalf of a user:
1. Always show recipient, subject, and body preview
2. Ask for explicit confirmation before sending
3. Support attachments, CC/BCC, and reply threading

### Calendar (Google Calendar / Outlook)
**Discovery query:** `"google calendar"`, `"outlook calendar"`, `"calendar events"`

| Operation | Discovery Query |
|-----------|----------------|
| List events | `"calendar list events"` |
| Create event | `"calendar create event"` |
| Update event | `"calendar update event"` |
| Delete event | `"calendar delete event"` |
| Find free slots | `"calendar free busy"` |

Important: use the requester's timezone (from Slack profile) when creating events or interpreting "today" / "tomorrow".

### File Operations
**Discovery query:** `"file upload"`, `"google drive"`, `"dropbox"`

Use `COMPOSIO_REMOTE_WORKBENCH` for:
- Reading and parsing uploaded documents (PDF, DOCX, XLSX)
- Generating files (CSV exports, reports, documents)
- Converting between formats (e.g. XLSX → CSV, HTML → PDF)

### Image Generation
**Discovery query:** `"image generation"`, `"dall-e"`, `"stable diffusion"`

When generating visual content:
- Provide detailed prompts for better results
- For data visualizations, use code execution with matplotlib/plotly instead
- Share generated images directly to Slack via file upload

### Task & Project Management
**Discovery query:** `"linear"`, `"jira"`, `"asana"`, `"trello"`, `"notion"`

| Operation | Discovery Query |
|-----------|----------------|
| Create issue/task | `"linear create issue"`, `"jira create"` |
| List tasks | `"linear list issues"`, `"asana tasks"` |
| Update status | `"linear update"`, `"jira transition"` |
| Search | `"linear search"`, `"notion search"` |

### Code Execution
Use `COMPOSIO_REMOTE_WORKBENCH` (Python sandbox) or `COMPOSIO_REMOTE_BASH_TOOL` for:
- Running Python scripts to process data
- Data analysis with pandas, numpy
- Building visualizations with matplotlib, plotly
- Testing code snippets
- Any computation that needs to be grounded in real execution

### CRM & Sales
**Discovery query:** `"hubspot"`, `"salesforce"`, `"pipedrive"`

### Communication
**Discovery query:** `"slack"` (see slack-admin skill), `"discord"`, `"teams"`

## Checking Connections

Before using any integration, verify the user has connected it:

```
1. COMPOSIO_MANAGE_CONNECTIONS → check connected apps
2. If not connected, tell the user: "You'll need to connect [App] first. Use /lucy connect [app-name]"
3. If connected, proceed with the tool call
```

## Multi-Tool Orchestration

Use `COMPOSIO_MULTI_EXECUTE_TOOL` to run up to 20 tools in a single call when tasks have independent steps:
- Fetch from 3 data sources simultaneously
- Send messages to multiple channels
- Create multiple calendar events

## Quick Reference

| Need | Approach |
|------|----------|
| Search the web | `COMPOSIO_SEARCH_TOOLS` → "web search" → execute |
| Send an email | `COMPOSIO_SEARCH_TOOLS` → "send email" → confirm → execute |
| Check calendar | `COMPOSIO_SEARCH_TOOLS` → "calendar events" → execute |
| Read a PDF/DOCX | `COMPOSIO_REMOTE_WORKBENCH` → Python script |
| Generate chart | `COMPOSIO_REMOTE_WORKBENCH` → matplotlib/plotly |
| Process data | `COMPOSIO_REMOTE_WORKBENCH` → pandas script |
| Run shell command | `COMPOSIO_REMOTE_BASH_TOOL` → bash command |
| Create a file | `COMPOSIO_REMOTE_WORKBENCH` → generate → upload to Slack |

## Anti-Patterns

- Don't say "I don't have access to X" without searching first
- Don't hardcode tool slugs; always use `COMPOSIO_SEARCH_TOOLS` to discover them
- Don't send emails without showing the user a preview and getting confirmation
- Don't create calendar events without confirming timezone with the user
- Don't use code execution for tasks that have dedicated tools (e.g. don't scrape Gmail with requests when there's a Gmail tool)
