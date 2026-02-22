---
name: integrations
description: Check, connect, and configure third-party integrations via Composio. Use when managing integrations, connecting new services, or troubleshooting tool access.
---

Lucy uses Composio's meta-tools to access 10,000+ third-party integrations. You never call individual API tools directly — instead, you use 5 meta-tools that handle discovery, authentication, and execution.

## Available Meta-Tools

| Meta-Tool | Purpose |
|-----------|---------|
| `COMPOSIO_SEARCH_TOOLS` | Find specific tools by use-case description |
| `COMPOSIO_MANAGE_CONNECTIONS` | Generate OAuth links, check connection status |
| `COMPOSIO_MULTI_EXECUTE_TOOL` | Execute up to 20 tools in parallel |
| `COMPOSIO_REMOTE_WORKBENCH` | Run Python in a persistent sandbox |
| `COMPOSIO_REMOTE_BASH_TOOL` | Run bash commands in a sandbox |

## Workflow: Using an Integration

1. **Check if integration is connected**: Use `COMPOSIO_MANAGE_CONNECTIONS` to verify
2. **If not connected**: Generate an OAuth link and share with the user
3. **Find the right tool**: Use `COMPOSIO_SEARCH_TOOLS("create github issue")` to discover the exact tool
4. **Execute**: Use `COMPOSIO_MULTI_EXECUTE_TOOL` with the discovered tool schema

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

## Important Notes

- Never guess whether an integration is connected — always verify
- Some integrations require additional scopes — check connection status after auth
- Per-user OAuth: each team member has their own connections
- If a tool returns an error, check if the connection has expired before retrying
