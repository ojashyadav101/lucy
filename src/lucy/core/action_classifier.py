"""Action Classification System for tool safety gating.

Every tool action is classified into one of three categories:

- **READ**: Fetches data, no side effects. Executes immediately.
- **WRITE**: Creates or modifies data, generally reversible. Executes
  immediately — the user's request is implicit consent.
- **DESTRUCTIVE**: Actions whose consequences are hard to reverse AND
  whose impact is significant enough to warrant a pause. The test is
  NOT the verb ("send", "delete") — it is the real-world outcome:

    Ask: "If this goes wrong, how bad is it and can it be undone?"
    - Hard to undo + significant impact → DESTRUCTIVE (gate it)
    - Easy to undo OR low impact → WRITE (just do it)

  Examples that ARE destructive:
    - Sending an email to a customer saying their account is closed
    - Permanently revoking a user's access token with no recovery path
    - Cancelling a paid subscription that funds critical services

  Examples that are NOT destructive, regardless of verb:
    - Sending a "hi how are you?" email (trivial, low-stakes)
    - Sending a Slack message (internal, easy to follow up on)
    - Deleting a ticket or calendar event (recoverable in the app)
    - Cancelling a meeting (low-stakes, reschedulable)
    - Removing a team member from a channel (reversible moderation)

  The LLM signals high-consequence actions by setting
  ``"_lucy_is_destructive": true`` in the tool call parameters.
  This is the primary signal for most Composio tool calls.

Classification priority (highest first):
1. LLM-signaled destructive intent: ``_lucy_is_destructive`` param
2. Explicit wrapper annotations (``"action_type": "DESTRUCTIVE"``)
3. Registered overrides via ``register_override()``
4. Hardcoded internal tool sets (known-safe or known-destructive lucy_* tools)
5. Default: WRITE — always auto-execute unless something above fires
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


# ── Hardcoded internal tool sets ─────────────────────────────────────
# Only lucy_* tools are hardcoded here because we control their semantics.
# Composio tools are classified via wrapper annotations or LLM signals.

_INTERNAL_READ_TOOLS: frozenset[str] = frozenset({
    "lucy_list_crons",
    "lucy_list_heartbeats",
    "lucy_search_slack_history",
    "lucy_get_channel_history",
    "lucy_web_search",
    "lucy_read_file",
    "lucy_list_files",
    "lucy_reflection",
    "lucy_react_to_message",
    "COMPOSIO_SEARCH_TOOLS",
    "COMPOSIO_GET_TOOL_SCHEMAS",
    "COMPOSIO_MANAGE_CONNECTIONS",
})

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
    # Gateway tools — bash command content-based classification via _classify_bash_command
    "lucy_exec_command",
    "lucy_start_background",
    "lucy_poll_process",
})

# These lucy_* tools are unconditionally destructive regardless of LLM signals.
# The list is intentionally tiny — only things Lucy controls that have no
# recovery path outside of the system.
_INTERNAL_DESTRUCTIVE_TOOLS: frozenset[str] = frozenset({
    "lucy_delete_cron",           # crons are not recoverable once deleted
    "lucy_delete_heartbeat",
    "lucy_delete_custom_integration",
    "lucy_send_email",            # Lucy's own outbound email tool
})


# ── Layer: Explicit overrides ─────────────────────────────────────────
# Tool name → forced ActionType. Populated at startup from wrapper annotations
# and runtime calls. Takes priority over all heuristics.
_overrides: dict[str, ActionType] = {}


def register_override(tool_name: str, action_type: ActionType) -> None:
    """Register an explicit classification override for a tool.

    Takes priority over all heuristic classification. Used for wrapper tools
    whose names don't follow common patterns, or to promote a WRITE
    tool to DESTRUCTIVE when the tool has irreversible consequences by design
    (e.g., a billing cancellation endpoint at a critical vendor).
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

    Wrappers can annotate individual tools with ``"action_type": "DESTRUCTIVE"``
    (or READ/WRITE) in their TOOLS list. This is the right place to mark
    a specific tool as high-stakes — e.g., a custom ``cancel_account`` endpoint
    for a mission-critical service your company relies on.

    Called during wrapper loading (``load_custom_wrapper_tools``).
    """
    for tool_def in tools:
        name = tool_def.get("name", "")
        annotation = tool_def.get("action_type", "").upper()
        if name and annotation:
            try:
                action_type = ActionType(annotation)
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


def _classify_bash_command(parameters: dict[str, Any] | None) -> ActionType:
    """Classify a shell command by its content.

    Most bash commands in agentic workflows are safe (read/explore/build).
    Only a tiny subset of truly destructive shell operations require a gate —
    commands that permanently destroy data or exfiltrate it externally.
    """
    if not parameters:
        return ActionType.WRITE

    cmd = str(parameters.get("cmd") or parameters.get("command") or "").strip().lower()
    if not cmd:
        return ActionType.WRITE

    # Truly destructive bash: irreversible deletion, database drops, disk wipes,
    # or data exfiltration via curl/wget POST.
    _BASH_DESTRUCTIVE = re.compile(
        r"\brm\s+(-\w*\s+)*-[rf]|"
        r"\bsudo\s+rm\b|"
        r"\bshred\b|"
        r"\bmkfs\b|"
        r"\bdd\s+if=.+of=/dev/|"
        r"\bdrop\s+(?:table|database|schema)\b|"
        r"\btruncate\s+table\b|"
        r"\bdropdb\b|"
        r"\bcurl\s+.+(-d|--data)|"
        r"\bwget\s+.*--post-data|"
        r">\s*/etc/|"
        r"\bsystemctl\s+(?:stop|disable|mask)\b",
        re.IGNORECASE,
    )
    if _BASH_DESTRUCTIVE.search(cmd):
        return ActionType.DESTRUCTIVE

    _BASH_READ = re.compile(
        r"^\s*(?:git\s+(?:clone|fetch|pull|log|status|diff|show|ls-files)|"
        r"ls|cat|head|tail|grep|find|wc|stat|file|du|df|echo|pwd|"
        r"pip\s+(?:install|list|show|freeze)|npm\s+(?:install|list|info|ci)|"
        r"python\s+|node\s+|npm\s+(?:run|start|test)|"
        r"curl\s+(?!.*(-d|--data|-X\s+POST|-X\s+PUT|-X\s+DELETE))|"
        r"wget\s+(?!.*--post-data)|"
        r"mongo|mongosh|psql|mysql)\b",
        re.IGNORECASE,
    )
    if _BASH_READ.match(cmd):
        return ActionType.READ

    return ActionType.WRITE


def classify(tool_name: str, parameters: dict[str, Any] | None = None) -> ActionType:
    """Classify a tool action into READ, WRITE, or DESTRUCTIVE.

    Classification priority:
    1. LLM-signaled destructive intent: ``_lucy_is_destructive`` in parameters
    2. Explicit overrides (wrapper annotations / ``register_override``)
    3. Internal tool sets (hardcoded lucy_* tools)
    4. Bash command content (for shell execution tools)
    5. Default: WRITE

    Note: There are NO generic heuristic patterns based on verb names like
    "send", "delete", "cancel". Those patterns produce false positives because
    consequence is context-dependent, not verb-dependent. The LLM signals
    high-consequence actions via the ``_lucy_is_destructive`` parameter.
    """
    stripped = tool_name.removeprefix("lucy_custom_")

    # ── Layer 1: LLM-signaled destructive intent ──────────────────────
    # The LLM sets _lucy_is_destructive=true when it determines the action
    # has significant, hard-to-reverse real-world consequences.
    if parameters and parameters.get("_lucy_is_destructive") is True:
        return ActionType.DESTRUCTIVE

    # ── Layer 2: Explicit overrides (highest static priority) ─────────
    if tool_name in _overrides:
        return _overrides[tool_name]
    if stripped in _overrides:
        return _overrides[stripped]

    # ── Layer 3: Internal tool sets ───────────────────────────────────
    if tool_name in _INTERNAL_READ_TOOLS:
        return ActionType.READ
    if tool_name in _INTERNAL_WRITE_TOOLS:
        if tool_name in ("lucy_exec_command", "COMPOSIO_REMOTE_BASH_TOOL", "COMPOSIO_REMOTE_WORKBENCH"):
            return _classify_bash_command(parameters)
        return ActionType.WRITE
    if tool_name in _INTERNAL_DESTRUCTIVE_TOOLS:
        return ActionType.DESTRUCTIVE

    # ── Layer 4: Composio meta-tool handling ──────────────────────────
    if tool_name.startswith("COMPOSIO_"):
        if tool_name == "COMPOSIO_MULTI_EXECUTE_TOOL":
            return ActionType.WRITE  # inner-action classification handled separately
        if tool_name in ("COMPOSIO_REMOTE_BASH_TOOL", "COMPOSIO_REMOTE_WORKBENCH"):
            return _classify_bash_command(parameters)
        return ActionType.READ  # discovery/schema tools

    # ── Layer 5: MCP tools ────────────────────────────────────────────
    # MCP tools default to WRITE unless they have an explicit override or
    # the LLM signaled _lucy_is_destructive.
    if tool_name.startswith("mcp_"):
        after_prefix = tool_name[4:]
        native_parts = after_prefix.split("_", 1)
        native_name = native_parts[1] if len(native_parts) == 2 else native_parts[0]
        # Only check for obvious read suffixes — everything else is WRITE
        if any(
            native_name.endswith(suffix)
            for suffix in ("_list", "_get", "_fetch", "_search", "_find", "_info",
                           "_status", "_version", "_ping", "_health")
        ) or native_name.startswith(("list_", "get_", "fetch_", "search_", "find_")):
            return ActionType.READ
        return ActionType.WRITE

    # ── Default: WRITE ────────────────────────────────────────────────
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
            act_params = None
        elif isinstance(act, dict):
            act_name = (
                act.get("tool_slug")
                or act.get("action")
                or act.get("tool")
                or ""
            )
            act_params = act.get("parameters") or act.get("params")
        else:
            act_name = str(act)
            act_params = None

        action_type = classify(act_name, act_params)

        if action_type == ActionType.DESTRUCTIVE:
            return ActionType.DESTRUCTIVE
        if action_type == ActionType.WRITE and highest == ActionType.READ:
            highest = ActionType.WRITE

    return highest


def get_classification_summary(tool_name: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:  # noqa: E501
    """Return a detailed classification summary for debugging/logging."""
    action_type = classify(tool_name, parameters)
    stripped = tool_name.removeprefix("lucy_custom_")

    source = "default_write"
    if parameters and parameters.get("_lucy_is_destructive") is True:
        source = "llm_signal"
    elif tool_name in _overrides or stripped in _overrides:
        source = "override"
    elif tool_name in _INTERNAL_READ_TOOLS:
        source = "internal_read_set"
    elif tool_name in _INTERNAL_WRITE_TOOLS:
        source = "internal_write_set"
    elif tool_name in _INTERNAL_DESTRUCTIVE_TOOLS:
        source = "internal_destructive_set"
    elif tool_name.startswith("mcp_"):
        source = "mcp_heuristic"
    elif tool_name.startswith("COMPOSIO_"):
        source = "composio_meta"

    return {
        "tool_name": tool_name,
        "stripped_name": stripped,
        "action_type": action_type.value,
        "source": source,
        "requires_confirmation": action_type == ActionType.DESTRUCTIVE,
    }
