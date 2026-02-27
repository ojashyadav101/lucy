# Archived Code — Phase 2 Cleanup

Code removed in the Phase 2 dead-code cleanup. Everything below was
verified unused via full cross-reference of import chains, call sites,
and dynamic references across `src/lucy/`, `tests/`, and `scripts/`.

**Pre-cleanup commit:** `74043887a2b724dc643ce21495cc4b1dc26218fa`

To restore any item, check out that commit and copy the relevant code.

---

## Deleted Files

### `src/lucy/core/types.py`

Duplicate `AgentContext` dataclass. The canonical definition lives in
`src/lucy/core/agent.py`. This file was never imported anywhere.

```python
@dataclass
class AgentContext:
    workspace_id: str
    channel_id: str | None = None
    thread_ts: str | None = None
    user_name: str | None = None
```

### `src/lucy/slack/typing_indicator.py`

No-op `TypingIndicator` async context manager. Was intended to send
"Lucy is typing..." indicators but the Slack bot API doesn't support
it natively. Never imported or used.

```python
class TypingIndicator:
    async def __aenter__(self) -> "TypingIndicator": ...
    async def __aexit__(self, *exc) -> None: ...
    async def _loop(self) -> None: ...  # sends nothing
```

### `assets/prompt_modules/proactive.md`

Prompt module for proactive intelligence tips. Never loaded by the
prompt builder (`_COMMON_MODULES` and `INTENT_MODULES` do not reference
`"proactive"`). The cron scheduler has its own inline proactive
instructions in `_build_cron_instruction()`.

Content was:

> **Don't just respond. Anticipate.**
>
> Pattern recognition, follow-up awareness, contextual suggestions.
> One proactive observation per conversation is enough.

---

## Removed Functions

### `reset_caches()` — `src/lucy/core/prompt.py`

No-op function. Docstring: "Prompt files are re-read on every call now."
Was exported in `core/__init__.py` but never called.

```python
def reset_caches() -> None:
    """No-op. Prompt files are re-read on every call now."""
```

### `get_all_middleware()` — `src/lucy/slack/middleware.py`

Convenience function returning all 3 middleware functions as a list.
Never called; each middleware is registered individually in `handlers.py`.

```python
def get_all_middleware() -> list:
    return [
        resolve_workspace_middleware,
        resolve_user_middleware,
        resolve_channel_middleware,
    ]
```

### `get_pending_action()` — `src/lucy/slack/hitl.py`

Read a pending HITL action by key without resolving it. Never called;
`resolve_pending_action()` handles its own lookup internally.

```python
def get_pending_action(action_id: str) -> dict[str, Any] | None:
    _cleanup_expired()
    return _pending_actions.get(action_id)
```

### `get_last_heartbeat_time()` — `src/lucy/workspace/activity_log.py`

Read last heartbeat timestamp from workspace state. Never called.

```python
async def get_last_heartbeat_time(ws: WorkspaceFS) -> str | None:
    state = await ws.read_state()
    return state.get("last_heartbeat_at")
```

### `set_last_heartbeat_time()` — `src/lucy/workspace/activity_log.py`

Record that a heartbeat just ran. Never called.

```python
async def set_last_heartbeat_time(ws: WorkspaceFS) -> None:
    await ws.update_state({
        "last_heartbeat_at": datetime.now(timezone.utc).isoformat(),
    })
```

### `list_snapshots()` — `src/lucy/workspace/snapshots.py`

List available snapshot dates for a category. Never called (other
snapshot functions `save_snapshot`, `load_latest`, `compute_delta`,
`list_categories` are all used).

```python
async def list_snapshots(
    ws: WorkspaceFS, category: str, limit: int = 30,
) -> list[str]:
    entries = await ws.list_dir(f"data/{category}")
    json_files = sorted(
        [e for e in entries if e.endswith(".json")], reverse=True
    )
    return json_files[:limit]
```

### `get_new_messages()` — `src/lucy/workspace/slack_reader.py`

Fetch new Slack messages across channels via Slack API. No external
caller; `slack_sync.py` uses `get_lucy_channels()` instead.

### `get_local_messages()` — `src/lucy/workspace/slack_reader.py`

Read synchronized Slack messages from local filesystem. Never called
externally.

### `SlackMessage` dataclass — `src/lucy/workspace/slack_reader.py`

Dataclass used only by `get_new_messages()` (also removed).

```python
@dataclass
class SlackMessage:
    channel_id: str
    channel_name: str
    user_id: str
    user_name: str
    text: str
    timestamp: str
    thread_ts: str | None = None
    reply_count: int = 0
```

---

## Cleaned Exports

### `src/lucy/core/__init__.py`

Removed: `reset_caches` (function deleted).

### `src/lucy/workspace/__init__.py`

Removed exports (file kept, just not re-exported):
`ExecutionResult`, `execute_python`, `execute_bash`,
`execute_workspace_script`.
