"""Action Classification System for tool safety gating.

Every tool action is classified into one of three categories:

- **READ**: Fetches data, no side effects. Executes immediately.
- **WRITE**: Creates or modifies data, generally reversible. Requires
  lightweight confirmation.
- **DESTRUCTIVE**: Irreversible side effects (sends email, deletes data,
  cancels subscriptions). Requires explicit user confirmation with warning.

Classification uses three layers (highest priority first):
1. Explicit annotations in wrapper TOOLS definitions (``"action_type": "DESTRUCTIVE"``)
2. Registered overrides via ``register_override()`` 
3. Heuristic classification from tool name patterns

Unknown or unclassifiable tools default to WRITE (require confirmation) —
this is the safe default. We never auto-execute an action we can't classify.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger()


class ActionType(str, Enum):
    """Classification of a tool action's side-effect level."""
    READ = "READ"
    WRITE = "WRITE"
    DESTRUCTIVE = "DESTRUCTIVE"


# ── Layer 1: Heuristic patterns ─────────────────────────────────────
# These match against the FINAL tool name (after lucy_custom_ prefix strip).
# Order matters: first match wins. Patterns are checked top-to-bottom.

# DESTRUCTIVE patterns — irreversible side effects
# Use (?:^|_) instead of \b because underscores ARE word characters,
# so \bsend_ won't match inside "gmail_send_email".
_DESTRUCTIVE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?:^|_)send(?=[_\s]|$)", re.IGNORECASE),        # send_email, send_message
    re.compile(r"(?:^|_)delete(?=[_\s]|$)", re.IGNORECASE),      # delete_user, delete_event
    re.compile(r"(?:^|_)remove(?=[_\s]|$)", re.IGNORECASE),      # remove_member
    re.compile(r"(?:^|_)cancel(?=[_\s]|$)", re.IGNORECASE),      # cancel_subscription
    re.compile(r"(?:^|_)revoke(?=[_\s]|$)", re.IGNORECASE),      # revoke_token, revoke_session
    re.compile(r"(?:^|_)ban(?=[_\s]|$)", re.IGNORECASE),         # ban_user
    re.compile(r"(?:^|_)unban(?=[_\s]|$)", re.IGNORECASE),       # unban_user
    re.compile(r"(?:^|_)destroy(?=[_\s]|$)", re.IGNORECASE),     # destroy_resource
    re.compile(r"(?:^|_)purge(?=[_\s]|$)", re.IGNORECASE),       # purge_cache
    re.compile(r"(?:^|_)forward(?=[_\s]|$)", re.IGNORECASE),     # forward_email
    re.compile(r"(?:^|_)unsubscribe(?=[_\s]|$)", re.IGNORECASE), # unsubscribe
    re.compile(r"(?:^|_)archive(?=[_\s]|$)", re.IGNORECASE),     # archive_channel
    re.compile(r"(?:^|_)reply[_\s]?to", re.IGNORECASE),          # reply_to_thread
]

# WRITE patterns — creates/modifies data, generally reversible
_WRITE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?:^|_)create(?=[_\s]|$)", re.IGNORECASE),     # create_event, create_draft
    re.compile(r"(?:^|_)add(?=[_\s]|$)", re.IGNORECASE),        # add_member
    re.compile(r"(?:^|_)update(?=[_\s]|$)", re.IGNORECASE),     # update_user
    re.compile(r"(?:^|_)edit(?=[_\s]|$)", re.IGNORECASE),       # edit_profile
    re.compile(r"(?:^|_)modify(?=[_\s]|$)", re.IGNORECASE),     # modify_cron
    re.compile(r"(?:^|_)set(?=[_\s]|$)", re.IGNORECASE),        # set_role
    re.compile(r"(?:^|_)patch(?=[_\s]|$)", re.IGNORECASE),      # patch_event
    re.compile(r"(?:^|_)put(?=[_\s]|$)", re.IGNORECASE),        # put_data
    re.compile(r"(?:^|_)post(?=[_\s]|$)", re.IGNORECASE),       # post_message
    re.compile(r"(?:^|_)write(?=[_\s]|$)", re.IGNORECASE),      # write_file
    re.compile(r"(?:^|_)generate(?=[_\s]|$)", re.IGNORECASE),   # generate_report
    re.compile(r"(?:^|_)store(?=[_\s]|$)", re.IGNORECASE),      # store_api_key
    re.compile(r"(?:^|_)quick[_\s]?add", re.IGNORECASE),        # quick_add event
    re.compile(r"(?:^|_)trigger(?=[_\s]|$)", re.IGNORECASE),    # trigger_cron
]

# READ patterns — fetches data, no side effects
# Note: Composio uses VERB at end too (e.g., EVENTS_LIST), so match
# both (?:^|_)verb[_\s] and (?:^|_)verb$ patterns.
_READ_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?:^|_)list(?=[_\s]|$)", re.IGNORECASE),     # list_users, EVENTS_LIST
    re.compile(r"(?:^|_)get(?=[_\s]|$)", re.IGNORECASE),      # get_profile, get_thread
    re.compile(r"(?:^|_)fetch(?=[_\s]|$)", re.IGNORECASE),    # fetch_emails
    re.compile(r"(?:^|_)search(?=[_\s]|$)", re.IGNORECASE),   # search_slack
    re.compile(r"(?:^|_)find(?=[_\s]|$)", re.IGNORECASE),     # find_free_slots
    re.compile(r"(?:^|_)check(?=[_\s]|$)", re.IGNORECASE),    # check_status
    re.compile(r"(?:^|_)count(?=[_\s]|$)", re.IGNORECASE),    # count_users
    re.compile(r"(?:^|_)query(?=[_\s]|$)", re.IGNORECASE),    # query_data
    re.compile(r"(?:^|_)lookup(?=[_\s]|$)", re.IGNORECASE),   # lookup_user
    re.compile(r"(?:^|_)show(?=[_\s]|$)", re.IGNORECASE),     # show_calendar
    re.compile(r"(?:^|_)retrieve(?=[_\s]|$)", re.IGNORECASE), # retrieve_record
    re.compile(r"(?:^|_)view(?=[_\s]|$)", re.IGNORECASE),     # view_details
    re.compile(r"(?:^|_)export(?=[_\s]|$)", re.IGNORECASE),   # export_data
    re.compile(r"(?:^|_)download(?=[_\s]|$)", re.IGNORECASE), # download_file
]


# ── Layer 2: Explicit overrides ─────────────────────────────────────
# Tool name → forced ActionType. Takes priority over heuristics.
# Populated at startup from wrapper annotations and runtime calls.

_overrides: dict[str, ActionType] = {}

# Internal tools that are known-safe READ operations
_INTERNAL_READ_TOOLS: frozenset[str] = frozenset({
    "lucy_list_crons",
    "lucy_list_heartbeats",
    "lucy_search_slack_history",
    "lucy_get_channel_history",
    "lucy_web_search",
    "lucy_read_file",
    "lucy_list_files",
    # Composio meta-tools (discovery, not execution)
    "COMPOSIO_SEARCH_TOOLS",
    "COMPOSIO_GET_TOOL_SCHEMAS",
    "COMPOSIO_MANAGE_CONNECTIONS",
})

# Internal tools that are WRITE (reversible modifications)
_INTERNAL_WRITE_TOOLS: frozenset[str] = frozenset({
    "lucy_create_cron",
    "lucy_modify_cron",
    "lucy_create_heartbeat",
    "lucy_write_file",
    "lucy_edit_file",
    "lucy_store_api_key",
    "lucy_resolve_custom_integration",
    "lucy_spaces_deploy",
    "lucy_generate_pdf",
    "lucy_generate_excel",
    "lucy_generate_docx",
    "lucy_generate_pptx",
    "lucy_generate_image",
})

# Internal tools that are DESTRUCTIVE
_INTERNAL_DESTRUCTIVE_TOOLS: frozenset[str] = frozenset({
    "lucy_delete_cron",
    "lucy_delete_heartbeat",
    "lucy_delete_custom_integration",
    "lucy_send_email",
})


def register_override(tool_name: str, action_type: ActionType) -> None:
    """Register an explicit classification override for a tool.

    This takes priority over heuristic classification. Useful for tools
    whose names don't match common patterns, or to promote a WRITE
    tool to DESTRUCTIVE (e.g., ``create_checkout`` actually charges money).
    """
    _overrides[tool_name] = action_type
    logger.debug(
        "action_classifier_override_registered",
        tool=tool_name,
        action_type=action_type.value,
    )


def register_overrides_from_wrapper(
    slug: str,
    tools: list[dict[str, Any]],
) -> None:
    """Register overrides from wrapper TOOLS definitions.

    Wrappers can annotate individual tools with ``"action_type": "READ"``
    (or WRITE/DESTRUCTIVE) in their TOOLS list. This function reads those
    annotations and registers them as overrides.

    Called during wrapper loading (``load_custom_wrapper_tools``).
    """
    for tool_def in tools:
        name = tool_def.get("name", "")
        annotation = tool_def.get("action_type", "").upper()
        if name and annotation:
            try:
                action_type = ActionType(annotation)
                # Register both raw name and prefixed name
                register_override(name, action_type)
                register_override(f"lucy_custom_{name}", action_type)
                logger.debug(
                    "action_classifier_wrapper_annotation",
                    slug=slug,
                    tool=name,
                    action_type=action_type.value,
                )
            except ValueError:
                logger.warning(
                    "action_classifier_invalid_annotation",
                    slug=slug,
                    tool=name,
                    annotation=annotation,
                )


def classify(tool_name: str, parameters: dict[str, Any] | None = None) -> ActionType:
    """Classify a tool action into READ, WRITE, or DESTRUCTIVE.

    Classification priority:
    1. Explicit overrides (``register_override`` / wrapper annotations)
    2. Internal tool sets (``_INTERNAL_READ_TOOLS`` etc.)
    3. Heuristic pattern matching on tool name
    4. Parameter-based hints (e.g., ``confirmed=true`` suggests write)
    5. Default: WRITE (safe default — requires confirmation)
    """
    # Strip the lucy_custom_ prefix for classification purposes
    # but check overrides with BOTH the raw and stripped name
    stripped = tool_name.removeprefix("lucy_custom_")

    # ── Layer 1: Explicit overrides (highest priority) ───────────────
    if tool_name in _overrides:
        return _overrides[tool_name]
    if stripped in _overrides:
        return _overrides[stripped]

    # ── Layer 2: Internal tool sets ──────────────────────────────────
    if tool_name in _INTERNAL_READ_TOOLS:
        return ActionType.READ
    if tool_name in _INTERNAL_WRITE_TOOLS:
        return ActionType.WRITE
    if tool_name in _INTERNAL_DESTRUCTIVE_TOOLS:
        return ActionType.DESTRUCTIVE

    # ── Layer 3: Heuristic pattern matching ──────────────────────────
    # Check DESTRUCTIVE first (strictest), then WRITE, then READ
    for pattern in _DESTRUCTIVE_PATTERNS:
        if pattern.search(stripped):
            return ActionType.DESTRUCTIVE

    for pattern in _WRITE_PATTERNS:
        if pattern.search(stripped):
            return ActionType.WRITE

    for pattern in _READ_PATTERNS:
        if pattern.search(stripped):
            return ActionType.READ

    # ── Layer 4: Parameter hints ─────────────────────────────────────
    if parameters:
        # A "confirmed" parameter suggests this is a write/destructive action
        if "confirmed" in parameters:
            return ActionType.WRITE

    # ── Layer 5: Tool name prefix heuristics ─────────────────────────
    # Composio meta-tools that are purely discovery/orchestration
    if tool_name.startswith("COMPOSIO_"):
        # COMPOSIO_MULTI_EXECUTE_TOOL is special — it's an executor.
        # Classification depends on what actions it's running, handled
        # separately in the confirmation gate.
        if tool_name == "COMPOSIO_MULTI_EXECUTE_TOOL":
            return ActionType.WRITE  # conservative default
        if tool_name == "COMPOSIO_REMOTE_BASH_TOOL":
            return ActionType.WRITE
        if tool_name == "COMPOSIO_REMOTE_WORKBENCH":
            return ActionType.WRITE
        # Search/schema tools are read-only
        return ActionType.READ

    # ── Default: WRITE (safe default) ────────────────────────────────
    logger.info(
        "action_classifier_defaulting_to_write",
        tool=tool_name,
        stripped=stripped,
    )
    return ActionType.WRITE


def classify_composio_multi_execute(
    actions: list[Any],
) -> ActionType:
    """Classify a COMPOSIO_MULTI_EXECUTE_TOOL call by its inner actions.

    Returns the HIGHEST risk classification across all inner actions.
    If any action is DESTRUCTIVE, the whole call is DESTRUCTIVE.
    """
    highest = ActionType.READ

    for act in actions:
        if isinstance(act, str):
            act_name = act
        elif isinstance(act, dict):
            act_name = (
                act.get("tool_slug")
                or act.get("action")
                or act.get("tool")
                or ""
            )
        else:
            act_name = str(act)

        action_type = classify(act_name)

        if action_type == ActionType.DESTRUCTIVE:
            return ActionType.DESTRUCTIVE
        if action_type == ActionType.WRITE and highest == ActionType.READ:
            highest = ActionType.WRITE

    return highest


def get_classification_summary(tool_name: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a detailed classification summary for debugging/logging."""
    action_type = classify(tool_name, parameters)
    stripped = tool_name.removeprefix("lucy_custom_")

    source = "default"
    if tool_name in _overrides or stripped in _overrides:
        source = "override"
    elif tool_name in _INTERNAL_READ_TOOLS:
        source = "internal_read_set"
    elif tool_name in _INTERNAL_WRITE_TOOLS:
        source = "internal_write_set"
    elif tool_name in _INTERNAL_DESTRUCTIVE_TOOLS:
        source = "internal_destructive_set"
    else:
        for pattern in _DESTRUCTIVE_PATTERNS:
            if pattern.search(stripped):
                source = f"heuristic_destructive:{pattern.pattern}"
                break
        else:
            for pattern in _WRITE_PATTERNS:
                if pattern.search(stripped):
                    source = f"heuristic_write:{pattern.pattern}"
                    break
            else:
                for pattern in _READ_PATTERNS:
                    if pattern.search(stripped):
                        source = f"heuristic_read:{pattern.pattern}"
                        break

    return {
        "tool_name": tool_name,
        "stripped_name": stripped,
        "action_type": action_type.value,
        "source": source,
        "requires_confirmation": action_type != ActionType.READ,
    }
