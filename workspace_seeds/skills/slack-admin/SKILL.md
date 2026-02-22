---
name: slack-admin
description: Manage Slack workspace operations like listing users, joining channels, and posting messages. Use when performing Slack administrative tasks.
---

# Slack Administration

Lucy lives in Slack and can perform administrative operations through the Slack API.

## Available Operations

### Reading
- **List users**: Get all workspace members with names, emails, roles
- **List channels**: Get all public channels Lucy has access to
- **Read messages**: Fetch conversation history from channels and threads
- **Search messages**: Find messages matching a query across channels

### Writing
- **Post messages**: Send messages to channels or DMs
- **Reply in threads**: Continue a conversation in a thread
- **Update messages**: Edit previously sent messages
- **React to messages**: Add emoji reactions

### Channel Management
- **Join channels**: Lucy can join public channels to monitor them
- **List members**: See who's in a specific channel

## Best Practices

1. **Thread awareness**: Always reply in the thread if the triggering message was in a thread
2. **Rate limits**: Respect Slack's rate limits (1 msg/sec per channel)
3. **Proactive messages**: When posting proactive updates (from crons), choose the most relevant channel
4. **Ephemeral messages**: Use ephemeral messages for responses only the requesting user should see
5. **Block Kit**: Use Block Kit formatting for structured messages (tables, buttons, sections)

## Useful Patterns

### Finding the right channel
Read channel names and purposes to find where to post. Common patterns:
- `#general` — company-wide announcements
- `#engineering` / `#dev` — technical discussions
- `#random` — casual, non-work chat
- Team-specific channels — direct updates for that team

### Getting team context
When you need to understand the team:
1. List all users and their profiles
2. Check which channels they're active in
3. Read recent messages to understand communication patterns
4. Update `team/SKILL.md` with findings
