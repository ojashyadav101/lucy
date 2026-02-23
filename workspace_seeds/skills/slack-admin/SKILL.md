---
name: slack-admin
description: Manage Slack workspace operations like listing users, joining channels, posting messages, and team administration. Use when performing Slack administrative tasks.
---

# Slack Administration

Lucy lives in Slack and can perform administrative operations through Composio's Slack toolkit. Discover specific tools with `COMPOSIO_SEARCH_TOOLS` query "slack".

## Core Composio Slack Tools

### Reading & Discovery

| Tool | What It Does |
|------|-------------|
| `SLACK_LIST_USERS` | List all workspace members with profiles, emails, roles, timezone |
| `SLACK_LIST_CHANNELS` | List public/private channels Lucy has access to |
| `SLACK_GET_CHANNEL_INFO` | Get details about a specific channel (topic, purpose, member count) |
| `SLACK_GET_CHANNEL_HISTORY` | Fetch recent messages from a channel |
| `SLACK_GET_THREAD_REPLIES` | Read all replies in a thread |
| `SLACK_SEARCH_MESSAGES` | Search messages across channels by keyword |
| `SLACK_GET_USER_INFO` | Get detailed profile for one user (including tz, tz_label, tz_offset) |

### Messaging

| Tool | What It Does |
|------|-------------|
| `SLACK_SEND_MESSAGE` | Post a message to a channel or DM |
| `SLACK_REPLY_TO_THREAD` | Reply in an existing thread |
| `SLACK_UPDATE_MESSAGE` | Edit a previously sent message (need channel + ts) |
| `SLACK_DELETE_MESSAGE` | Delete a message Lucy sent |
| `SLACK_REACT_TO_MESSAGE` | Add an emoji reaction (channel + ts + emoji name) |
| `SLACK_SEND_EPHEMERAL_MESSAGE` | Send a message visible only to one user |

### Channel Management

| Tool | What It Does |
|------|-------------|
| `SLACK_JOIN_CHANNEL` | Join a public channel |
| `SLACK_LEAVE_CHANNEL` | Leave a channel |
| `SLACK_CREATE_CHANNEL` | Create a new channel |
| `SLACK_SET_CHANNEL_TOPIC` | Set channel topic |
| `SLACK_SET_CHANNEL_PURPOSE` | Set channel purpose/description |

### File Operations

| Tool | What It Does |
|------|-------------|
| `SLACK_UPLOAD_FILE` | Upload a file to a channel (use for PDFs, Excel files, etc.) |
| `SLACK_LIST_FILES` | List files shared in a channel |

## Step-by-Step Patterns

### Posting a Proactive Update
```
1. COMPOSIO_SEARCH_TOOLS → "slack send message"
2. Determine the right channel (read channel list + purposes)
3. COMPOSIO_MULTI_EXECUTE_TOOL → SLACK_SEND_MESSAGE with channel and text
```

### Finding and DMing a Team Member
```
1. COMPOSIO_SEARCH_TOOLS → "slack list users"
2. COMPOSIO_MULTI_EXECUTE_TOOL → SLACK_LIST_USERS
3. Find the target user's ID from the response
4. COMPOSIO_MULTI_EXECUTE_TOOL → SLACK_SEND_MESSAGE with channel = user_id (DMs use user ID as channel)
```

### Reading Recent Activity in a Channel
```
1. COMPOSIO_SEARCH_TOOLS → "slack channel history"
2. COMPOSIO_MULTI_EXECUTE_TOOL → SLACK_GET_CHANNEL_HISTORY with channel and limit
3. Parse messages, identify key topics and active participants
```

### Searching for Specific Topics
```
1. COMPOSIO_SEARCH_TOOLS → "slack search messages"
2. COMPOSIO_MULTI_EXECUTE_TOOL → SLACK_SEARCH_MESSAGES with query string
3. Filter results by channel, user, or date as needed
```

## Rate Limiting

Slack enforces strict rate limits. Key limits:
- **Messages**: 1 message per second per channel
- **API calls**: Tier 2 methods (most reads) allow ~20 req/min; Tier 3 (writes) allow ~50 req/min
- **Search**: Limited to ~20 req/min
- If you hit a rate limit, Slack returns a `Retry-After` header — Composio handles backoff automatically

## Timezone Awareness

Every Slack user has timezone data in their profile:
- `tz` — IANA timezone identifier (e.g. `America/New_York`)
- `tz_label` — human-readable label (e.g. `Eastern Standard Time`)
- `tz_offset` — seconds from UTC (changes with DST)

Use this when:
- Scheduling meetings across timezones
- Determining "today" for a specific user
- Sending time-sensitive messages at appropriate local times
- Don't cache `tz_offset` for long — it changes silently with DST

## Block Kit Formatting

For rich messages, use Block Kit JSON in the `blocks` parameter:

```json
[
  {"type": "header", "text": {"type": "plain_text", "text": "Daily Summary"}},
  {"type": "section", "text": {"type": "mrkdwn", "text": "*Key metrics:*\n• Revenue: $12.4k\n• Active users: 342"}},
  {"type": "divider"},
  {"type": "actions", "elements": [
    {"type": "button", "text": {"type": "plain_text", "text": "View Details"}, "action_id": "lucy_action_details", "value": "daily_report"}
  ]}
]
```

## Best Practices

1. **Thread awareness** — always reply in the thread if the triggering message was in a thread
2. **Rate limits** — space messages at 1/sec/channel; batch reads into single API calls where possible
3. **Channel selection** — for proactive messages (from crons), choose the most relevant channel based on topic
4. **Ephemeral messages** — use for responses only the requesting user should see (confirmations, previews)
5. **Formatting** — use mrkdwn for text (*bold*, `code`, >quotes); use Block Kit for interactive or structured content
6. **File sharing** — when sharing generated files (PDFs, spreadsheets), use file upload rather than external links

## Anti-Patterns

- Don't post to #general for things that belong in a specific channel
- Don't send multiple messages when one well-formatted message would suffice
- Don't @mention users unless truly necessary for their attention
- Don't read entire channel histories when a targeted search would work
- Don't create channels without asking the user first
