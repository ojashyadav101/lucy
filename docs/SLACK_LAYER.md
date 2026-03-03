# Slack Layer — Deep Dive

> How Lucy receives Slack events, handles them, formats responses, manages
> emoji reactions, and implements Human-in-the-Loop approvals.

---

## Event Registration

**File:** `src/lucy/slack/handlers.py`

Lucy registers 5 event patterns with the Slack Bolt framework:

| Pattern | Type | Handler |
|---------|------|---------|
| `app_mention` | Event | `_handle_message()` |
| `message` | Event | `_handle_message()` |
| `/lucy` | Command | Slash command handler |
| `lucy_action_approve_.*` | Action | `handle_approve_action()` |
| `lucy_action_cancel_.*` | Action | `handle_cancel_action()` |

---

## Full Message Handling Flow

When a message arrives, `_handle_message()` runs through these stages:

```
Slack event arrives
    │
    ├── 1. EVENT DEDUPLICATION
    │     30-second TTL cache by (channel, ts)
    │     Prevents duplicate processing from Slack retries
    │
    ├── 2. WORKSPACE RESOLUTION
    │     Already resolved by middleware (see below)
    │     workspace_id, user_id, channel_id in context
    │
    ├── 3. CONTEXTUAL EMOJI REACTION
    │     classify_reaction(message) → emoji + react_only flag
    │     If react_only=True → add reaction, return (no response)
    │     Examples: "thanks" → 🫡, "got it" → ✅, "lgtm" → 👍
    │
    ├── 4. FAST PATH CHECK
    │     evaluate_fast_path(message, thread_depth)
    │     If fast → post pool response, return
    │
    ├── 5. EDGE CASE HANDLING
    │     ├── Status query → format_task_status() → reply
    │     ├── Cancellation → handle_task_cancellation() → reply
    │     └── Thread interrupt → decide behavior
    │
    ├── 6. THREAD LOCK
    │     Per-thread asyncio.Lock prevents concurrent agents
    │     in the same Slack thread
    │
    ├── 7. WORKING EMOJI
    │     get_working_emoji(message) → add reaction
    │     Research → 🔍, Create → 🔨, Deploy → 🚀
    │     Default → ⏳
    │
    ├── 8. PRIORITY CLASSIFICATION
    │     classify_priority(message, route_tier)
    │     HIGH (fast tier) / NORMAL / LOW (frontier tier)
    │     If queue busy + LOW priority → ⌛ reaction
    │
    ├── 9. BACKGROUND TASK CHECK
    │     should_run_as_background_task(message, tier)
    │     If yes → task_mgr.start_task() → ack message → return
    │
    ├── 10. AGENT EXECUTION
    │      _run_with_recovery(message, ctx, slack_client)
    │      ├── Attempt 1: normal run
    │      └── On failure: wait 2s, retry with failure_context
    │
    ├── 11. RESPONSE PROCESSING
    │      process_output(response)  → sanitize, convert, de-AI
    │      format_links(response)    → raw URLs → Slack links
    │      If >3000 chars:
    │        split_response() → multiple messages
    │      Else:
    │        text_to_blocks() → Block Kit blocks
    │        enhance_blocks() → emoji headers, formatted links
    │        chat_postMessage(blocks=..., text=fallback)
    │
    └── 12. CLEANUP (finally block)
           Remove working emoji reaction
```

### Error Handling

When both attempts in `_run_with_recovery()` fail:

```
Exception caught
    │
    ├── error_strategy.classify_error(error)
    │     → rate_limited / tool_timeout / service_unavailable / ...
    │
    ├── error_strategy.get_recovery_action(error_type)
    │     → User-friendly message from humanize pool
    │
    └── Post error message with task hint:
        "I ran into an issue while working on {task_hint}.
         {degradation_message}"
```

The task hint is extracted from the first 60 characters of the user's
message, providing context without exposing internals.

---

## Middleware Stack

**File:** `src/lucy/slack/middleware.py`

Three middleware functions run before every handler, resolving context:

### 1. `resolve_workspace_middleware`

```
Extract team_id from request
    │
    ├── Query Workspace table by slack_team_id
    ├── If found → attach workspace_id to context
    └── If not found:
        ├── Fetch team name via client.team_info()
        ├── Create Workspace row
        ├── Handle race condition (IntegrityError → retry)
        └── Attach workspace_id
```

### 2. `resolve_user_middleware`

```
Extract slack_user_id from event
    │
    ├── Skip bot users (ID starts with "B")
    ├── Requires workspace_id in context
    ├── Query User table by workspace_id + slack_user_id
    ├── If found → update last_seen_at, attach user_id
    └── If not found:
        ├── Fetch display_name, email, avatar via users_info()
        ├── Create User row
        └── Attach user_id + slack_user_id
```

### 3. `resolve_channel_middleware`

```
Extract slack_channel_id from event
    │
    ├── Requires workspace_id in context
    ├── Query Channel table by workspace_id + slack_channel_id
    ├── If found → attach channel_id
    └── If not found:
        ├── Create Channel with default name
        ├── Set memory_scope_key = "ch:{slack_channel_id}"
        └── Attach channel_id
```

All three use `AsyncSessionLocal()` for database access and handle race
conditions for concurrent workspace joins.

---

## Block Kit Formatting

**File:** `src/lucy/slack/blockkit.py`

### `text_to_blocks(text)`

Converts processed text into Slack Block Kit blocks.

**Qualification check** — returns `None` (use plain text) if:
- Text < 80 characters
- Too simple (few newlines, no bullets)

**Block types generated:**

| Detected Pattern | Block Type |
|-----------------|------------|
| `*Header Text*` | `header` block (plain_text, max 150 chars) |
| `---` | `divider` block |
| Everything else | `section` block (mrkdwn, max 3000 chars) |

**Limits:**
- Max 50 blocks (truncates with "...continued")
- Sections split at 2800 chars to stay under 3000 limit
- Returns `None` if ≤ 1 block (falls back to plain text)

Button action IDs: `lucy_action_approve_{action_id}`,
`lucy_action_cancel_{action_id}`

---

## Rich Output Enhancement

**File:** `src/lucy/slack/rich_output.py`

### `enhance_blocks(blocks)`

Post-processes Block Kit blocks:
- Header blocks → adds emoji via `add_section_emoji()`
- Section blocks → formats links via `format_links()`

### `format_links(text)`

Converts raw URLs to Slack anchor-text links:

| URL Pattern | Display |
|------------|---------|
| `github.com/.../pull/123` | "GitHub PR #123" |
| `github.com/.../issues/456` | "GitHub Issue #456" |
| `github.com/org/repo` | "repo on GitHub" |
| `linear.app/.../ISSUE-123` | "ISSUE-123 on Linear" |
| `notion.so/...` | "Page on Notion" |
| `connect.composio.dev/...` | "Connect here" |
| Other URLs | Domain name or full URL |

### `add_section_emoji(header_text)`

Adds emoji prefix to headers based on keywords:

| Keyword | Emoji |
|---------|-------|
| summary, overview | 📋 |
| result, finding | 📊 |
| warning, error | ⚠️ |
| success | ✅ |
| next step, action, recommendation | 🎯 |
| tip | 💡 |
| update, change | 🔄 |
| note, detail | 📝 |

Skips if header already contains an emoji (Unicode > 0x1F000).

### `split_response(text)`

Splits responses >3000 chars at natural break points:

| Priority | Break Point | Min Position |
|----------|------------|-------------|
| 1 | `\n*` (header boundary) | 30% from start |
| 2 | `\n---` (divider) | 30% |
| 3 | `\n\n` (paragraph) | 30% |
| 4 | `\n` (line break) | 20% |
| 5 | Hard cut at 3000 | — |

Never splits inside code blocks.

---

## Emoji Reactions

**File:** `src/lucy/slack/reactions.py`

### `classify_reaction(message)`

Selects an emoji reaction based on message intent.

**React-only (no reply needed):**

| Pattern | Emoji | Example |
|---------|-------|---------|
| Thanks/acknowledgment | 🫡 `saluting_face` | "thanks!", "appreciate it" |
| Confirmation | ✅ `white_check_mark` | "got it", "perfect", "done" |
| Approval | 👍 `thumbsup` | "lgtm", "ship it", "approved" |
| FYI/informational | 📝 `memo` | Messages tagged as FYI |

**React + reply:**

| Pattern | Emoji | Example |
|---------|-------|---------|
| Urgent | ⚡ `zap` | "urgent", "asap", "emergency" |
| Bug/error | 🔍 `mag` | "bug", "error", "broken" |
| Question/investigation | 👀 `eyes` | General questions |
| Create/build | 🔨 `hammer_and_wrench` | "create", "build", "make" |
| Analyze/research | 📊 `bar_chart` | "analyze", "research" |
| Deploy/ship | 🚀 `rocket` | "deploy", "ship", "release" |

**Special rule:** react-only patterns with >8 words become react+reply
(the message is substantial enough to warrant a response).

**Default:** 👀 `eyes` (react + reply)

### `get_working_emoji(message)`

Selects the "working on it" indicator emoji:

| Content | Emoji |
|---------|-------|
| Research/analyze | 🔍 `mag` |
| Create/build | 🔨 `hammer_and_wrench` |
| Deploy/ship | 🚀 `rocket` |
| Default | ⏳ `hourglass_flowing_sand` |

---

## Human-in-the-Loop (HITL)

**File:** `src/lucy/slack/hitl.py`

HITL prevents Lucy from executing destructive actions without user approval.

### Flow

```
LLM wants to call destructive tool
    │
    ├── is_destructive_tool_call(tool_name)?
    │     Matches: DELETE, REMOVE, CANCEL, SEND, FORWARD,
    │     ARCHIVE, DESTROY, REVOKE, UNSUBSCRIBE
    │
    ├── create_pending_action(tool_name, params, description, ws_id)
    │     Generates 12-char hex action_id
    │     Stores in _pending_actions dict
    │     TTL: 300 seconds (5 minutes)
    │
    ├── Post HITL approval blocks to Slack
    │     [Approve] [Cancel] buttons
    │
    ├── User clicks button...
    │
    └── Action handler fires:
          ├── Approve → resolve_pending_action(id, approved=True)
          │     → execute the tool → post confirmation
          └── Cancel → resolve_pending_action(id, approved=False)
                → post cancellation message
```

### Pending Action Storage

```python
_pending_actions = {
    "a1b2c3d4e5f6": {
        "tool_name": "GMAIL_SEND_EMAIL",
        "parameters": {"to": "user@example.com", "subject": "..."},
        "description": "Send email to user@example.com",
        "workspace_id": "ws_123",
        "created_at": 1234567890.0    # time.monotonic()
    }
}
```

Actions auto-expire after 300 seconds. Cleanup runs on every access.

---

## Slash Command: `/lucy`

The `/lucy` slash command supports:

| Subcommand | Action |
|-----------|--------|
| `help` | Show help text |
| `status` | Show current task status |
| `connect <service>` | Generate OAuth connection link |
| (anything else) | Process as a regular message |

---

## Progress Messages

During the agent loop, progress messages are posted to the Slack thread
to keep the user informed:

| Turn | Message Source |
|------|--------------|
| 3 | `pick("progress_early")` + task hint |
| 8 | `pick("progress_mid")` + task hint |
| 13 | `pick("progress_mid")` + task hint |
| 18+ | `pick("progress_late")` + task hint |

Each message is a `chat_postMessage` (new message in thread, not an edit).

The `task_hint` is the first 60 characters of the user's original message,
woven into the progress status: "working on *pull SEO data for MacBook
Journal*"

---

## Cross-System Effects

| If You Change... | Also Check... |
|-----------------|---------------|
| `classify_reaction()` patterns | `get_working_emoji()` (should be consistent) |
| HITL destructive patterns | Agent tool execution flow |
| `text_to_blocks()` logic | `split_response()` thresholds |
| Middleware resolution | Database models (Workspace, User, Channel) |
| Progress message timing | `_describe_progress()` in `core/agent.py` |
| Block Kit block types | Slack API limits (50 blocks max) |

---

## Block Kit Conversion (blockkit.py)

**File:** `src/lucy/slack/blockkit.py`

### `text_to_blocks(text)` — Text → Block Kit

Converts processed Slack mrkdwn text into structured Block Kit blocks.
Returns `None` if the text is too short (`< 80 chars`) or too simple
to benefit from Block Kit formatting.

#### Detection Rules

The parser scans the text and builds blocks based on patterns:

| Pattern | Block Type |
|---------|-----------|
| Lines starting with `*Header*` | `header` block |
| Lines starting with `• ` or `- ` | `section` block (bullet list) |
| Lines starting with `1. `, `2. ` | `section` block (numbered list) |
| Code blocks (``` ... ```) | `section` block with code formatting |
| `---` or `===` | `divider` block |
| Regular text paragraphs | `section` block |

#### Truncation

`_truncate(text, max_len)` ensures no block exceeds Slack's 3000-character
limit per block. Truncated text gets a `...truncated` suffix.

---

## Rich Output Enhancement (rich_output.py)

**File:** `src/lucy/slack/rich_output.py`

### `enhance_blocks(blocks)` — Post-Process Block Kit

Runs after `text_to_blocks()` to add visual polish:

1. Adds emoji prefixes to header blocks via `add_section_emoji()`
2. Converts raw URLs to anchor-text links via `format_links()`

### `add_section_emoji(header)` — Section Emoji Mapping

Maps section keywords to relevant emojis:

| Keyword | Emoji |
|---------|-------|
| summary, overview | :bar_chart: |
| next steps, action | :dart: |
| setup, install | :wrench: |
| warning, caution | :warning: |
| results, findings | :mag: |
| recommendation | :bulb: |

If no keyword matches, no emoji is added (avoids random decoration).

### `format_links(text)` — URL Humanization

Converts raw URLs to Slack anchor-text links with friendly names:

| Domain | Display Name |
|--------|-------------|
| `github.com` | "GitHub" |
| `docs.google.com` | "Google Docs" |
| `drive.google.com` | "Google Drive" |
| `notion.so` | "Notion" |
| `linear.app` | "Linear" |
| `figma.com` | "Figma" |
| Unknown domains | Domain name extracted |

Example: `https://github.com/user/repo` → `<https://github.com/user/repo|GitHub>`

### `split_response(text)` — Long Message Splitting

When a response exceeds `MAX_SINGLE_MESSAGE_CHARS = 3000`:

1. Finds natural break points (paragraph breaks, section headers)
2. Avoids splitting inside code blocks (`_is_inside_code_block`)
3. Returns `list[str]` of chunks, each posted as a separate Slack message

### `should_split_response(text)` — Split Check

Returns True if `len(text) > MAX_SINGLE_MESSAGE_CHARS`.

---

## Reaction System (reactions.py)

**File:** `src/lucy/slack/reactions.py`

### `classify_reaction(message)` — Emoji Selection

Classifies the incoming message to determine:
1. Which emoji to react with
2. Whether to react only (no reply needed) or react + reply

Returns `ReactionDecision(emoji, react_only, should_react)`.

#### Reaction Rules

| Pattern | Emoji | React Only? |
|---------|-------|-------------|
| "thanks", "thank you" | `pray` | Yes |
| "got it", "understood" | `white_check_mark` | Yes |
| "sounds good", "perfect" | `thumbsup` | Yes |
| "lol", "haha" | `joy` | Yes |
| Default (needs response) | `eyes` | No |

#### `react_only` Behavior

When `react_only=True`, the handler adds the emoji reaction but skips
the full agent loop. This handles acknowledgments and gratitude without
wasting an LLM call.

### `get_working_emoji(message)` — Working Indicator

Selects the appropriate "typing" emoji based on what the agent is doing:

| Message Content | Emoji |
|----------------|-------|
| Search/lookup/find | `mag` |
| Build/code/deploy | `hammer_and_wrench` |
| Deploy/launch | `rocket` |
| Default | `hourglass_flowing_sand` |

This emoji is added when the agent starts processing and removed
when the response is posted.

---

## Middleware Internals (middleware.py)

**File:** `src/lucy/slack/middleware.py`

Three middleware functions run in sequence for every incoming event:

### 1. `resolve_workspace_middleware`

- Extracts `team_id` from Slack event
- Looks up Workspace in database (by `slack_team_id`)
- If not found: creates Workspace record + triggers `onboard_workspace()`
- Attaches `workspace_id` and `workspace` to Bolt context

### 2. `resolve_user_middleware`

- Extracts `user_id` from Slack event
- Looks up User in database (by `slack_user_id` + `workspace_id`)
- If not found: creates User record with Slack profile data
- Updates `last_seen_at` timestamp
- Attaches `user` to Bolt context

### 3. `resolve_channel_middleware`

- Extracts `channel_id` from Slack event
- Looks up Channel in database
- If not found: creates Channel record
- Attaches `channel` to Bolt context

### Race Condition Handling

All middleware uses `INSERT ... ON CONFLICT DO NOTHING` patterns
to handle concurrent first-messages from the same workspace gracefully.
If two messages arrive simultaneously for a new workspace, only one
onboarding runs.

---

## Human-in-the-Loop (hitl.py)

**File:** `src/lucy/slack/hitl.py`

### Destructive Action Detection

`is_destructive_tool_call(tool_name)` checks if a tool name contains
any of these keywords:

```
DELETE, REMOVE, CANCEL, SEND, FORWARD, ARCHIVE,
DESTROY, REVOKE, UNSUBSCRIBE
```

When detected, Lucy pauses and asks for approval before executing.

### Pending Action Flow

```
Agent wants to execute destructive tool
    │
    ├── create_pending_action(tool_name, params, description, workspace_id)
    │     ├── Generates unique action_id
    │     ├── Stores action data in memory dict
    │     └── Returns action_id
    │
    ├── Post HITL approval blocks to Slack with [Approve] [Cancel]
    │
    ├── User clicks button
    │     ├── Approve → resolve_pending_action(id, approved=True)
    │     │     └── Returns action data → agent executes the tool
    │     └── Cancel → resolve_pending_action(id, approved=False)
    │           └── Returns None → agent skips execution
    │
    └── No click within 300 seconds (PENDING_TTL_SECONDS)
          └── _cleanup_expired() removes stale actions
              └── Agent receives pick("hitl_expired") message
```

### Storage

Pending actions are stored in an in-memory `dict[str, dict]`. This is
intentional — pending actions are ephemeral and should not persist
across restarts. If Lucy restarts while an action is pending, it
expires naturally.
