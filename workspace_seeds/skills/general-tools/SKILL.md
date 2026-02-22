---
name: general-tools
description: Search the web, send emails, generate images, and convert files. Use when a task needs one of these general-purpose capabilities.
---

Lucy has access to general-purpose tools through Composio's meta-tools. These cover common operations that don't belong to a specific integration.

## Web Search

Use `COMPOSIO_SEARCH_TOOLS` to find web search tools, then execute them.

When to use:
- Real-time data (weather, news, prices, events)
- Factual lookups your training data might not cover
- Verifying current information

**If you're about to say "I don't have access to live data" — stop and search instead.**

## Email

Search for email tools via Composio to send, read, and manage emails.

When sending on behalf of a user:
- Always show the recipient, subject, and body preview
- Ask for confirmation before sending
- Support attachments, CC/BCC, and reply threading

## File Operations

Use `COMPOSIO_REMOTE_WORKBENCH` to:
- Read and parse uploaded documents (PDF, DOCX, XLSX)
- Generate files (CSV exports, reports)
- Convert between formats

## Image Generation

When the user asks for visual content (logos, mockups, illustrations):
- Use available image generation tools via Composio
- Provide detailed prompts for better results
- Not for data visualizations — use code + matplotlib/plotly for charts

## Code Execution

Use `COMPOSIO_REMOTE_WORKBENCH` for:
- Running Python scripts to process data
- Testing code snippets
- Building data visualizations
- Any computation that needs to be grounded in real execution

## Quick Reference

| Need | Approach |
|------|----------|
| Search the web | COMPOSIO_SEARCH_TOOLS → find search tool → execute |
| Send an email | COMPOSIO_SEARCH_TOOLS → find email tool → confirm → execute |
| Read a PDF/DOCX | COMPOSIO_REMOTE_WORKBENCH → Python script |
| Generate chart | COMPOSIO_REMOTE_WORKBENCH → matplotlib/plotly |
| Process data | COMPOSIO_REMOTE_WORKBENCH → pandas script |
