---
name: integrations
description: Check, connect, and configure third-party integrations via Composio. Use when managing integrations, connecting new services, or troubleshooting tool access.
---

Lucy uses Composio's meta-tools to access 10,000+ third-party integrations. You never call individual API tools directly; instead, you use 5 meta-tools that handle discovery, authentication, and execution.

## Available Meta-Tools

| Meta-Tool | Purpose |
|-----------|---------|
| `COMPOSIO_SEARCH_TOOLS` | Find specific tools by use-case description |
| `COMPOSIO_MANAGE_CONNECTIONS` | Generate OAuth links, check connection status |
| `COMPOSIO_MULTI_EXECUTE_TOOL` | Execute up to 20 tools in parallel |
| `COMPOSIO_REMOTE_WORKBENCH` | Run Python in a persistent sandbox |
| `COMPOSIO_REMOTE_BASH_TOOL` | Run bash commands in a sandbox |

## Listing Connected Integrations

When a user asks what integrations are connected, **always answer from the `<current_environment>` block** in your system prompt. That list is the single source of truth — it includes both Composio OAuth connections AND custom API wrappers (Polar.sh, Clerk, etc.). Do NOT call `COMPOSIO_MANAGE_CONNECTIONS` to list integrations.

## Workflow: Using an Integration

1. **Check the `<current_environment>` block** for whether the service is listed
2. **If connected via custom wrapper** (Polar.sh, Clerk): use `lucy_custom_*` tools directly
3. **If connected via Composio**: Use `COMPOSIO_SEARCH_TOOLS` to find the right tool, then execute with `COMPOSIO_MULTI_EXECUTE_TOOL`
4. **If not connected**: Use `COMPOSIO_MANAGE_CONNECTIONS` to generate an OAuth link

## When a User Needs to Connect

If a tool call fails because the integration isn't connected:

1. Explain what you're trying to do and why the integration is needed
2. Use `COMPOSIO_MANAGE_CONNECTIONS` to generate an OAuth link
3. Share the link with the user in Slack
4. Wait for the user to confirm they've connected it
5. Retry the original action

Example response:
```
I'd love to help you create that issue in Linear! However, Linear isn't connected yet.

Please connect it here: [OAuth link]

Let me know once you've connected it and I'll create the issue right away.
```

## Integration-Specific Skills

When Lucy discovers a new connected integration, she creates a sub-skill at:
`skills/integrations/{service-name}/SKILL.md`

These sub-skills document:
- What the integration can do
- Common use cases
- Known quirks or limitations
- Workarounds for common issues

## Two Types of Integrations

Lucy has two categories of integrations:

1. **Composio-managed** (Gmail, GitHub, Google Sheets, etc.): OAuth-based, managed through Composio meta-tools. Use `COMPOSIO_SEARCH_TOOLS` → `COMPOSIO_MULTI_EXECUTE_TOOL`.
2. **Custom wrappers** (Polar.sh, Clerk, etc.): API-key-based, built by Lucy. Use `lucy_custom_*` tools directly — NEVER route through `COMPOSIO_MULTI_EXECUTE_TOOL`.

Both types are listed in `<current_environment>`. When listing integrations, include ALL of them.

## Important Notes

- The `<current_environment>` list is authoritative for what's connected
- Some integrations require additional scopes; check connection status after auth
- Per-user OAuth: each team member has their own connections
- If a tool returns an error, check if the connection has expired before retrying
