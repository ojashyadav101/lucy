"""Internal tool: web browsing via CamoFox.

Wraps the existing CamoFox browser client as lucy_* internal tools
so the LLM can browse the web, search, extract content, and interact
with web pages.

Architecture:
    lucy_browse_url → CamoFox REST API → Camoufox (stealth Firefox)
    lucy_browser_snapshot → accessibility snapshot with element refs
    lucy_browser_interact → click, type, scroll actions

CamoFox provides C++-level anti-detection so bot detection systems
don't block Lucy's browsing. Persistent per-user browser profiles.
"""

from __future__ import annotations

import re
from typing import Any

import structlog

logger = structlog.get_logger()

# Max chars from snapshot to send back to LLM
_MAX_SNAPSHOT_CHARS = 6000
_MAX_TEXT_CHARS = 4000


# ═══════════════════════════════════════════════════════════════════════════
# TOOL DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════════

def get_browser_tool_definitions() -> list[dict[str, Any]]:
    """Return OpenAI-format tool definitions for browsing."""
    return [
        {
            "type": "function",
            "function": {
                "name": "lucy_browse_url",
                "description": (
                    "Open a URL in the browser and get the page content. "
                    "Returns an accessibility snapshot with text and interactive elements. "
                    "Use for: reading web pages, checking websites, researching URLs, "
                    "web searches (navigate to google.com/search?q=...). "
                    "For multi-step browsing (login, fill forms), use this first "
                    "then lucy_browser_interact for subsequent actions."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": (
                                "The URL to navigate to. Can be a full URL or a search query "
                                "prefixed with @search (e.g. '@search best project management tools')."
                            ),
                        },
                    },
                    "required": ["url"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "lucy_browser_snapshot",
                "description": (
                    "Get the current state of the browser page. "
                    "Returns text content and interactive elements with references (e1, e2, etc). "
                    "Use this to re-read a page after interactions or to check what's visible."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "lucy_browser_interact",
                "description": (
                    "Interact with an element on the current page. "
                    "Elements are referenced by their eN identifier from the snapshot. "
                    "Actions: click, type, scroll, select."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["click", "type", "scroll", "select", "press_key"],
                            "description": "The interaction type.",
                        },
                        "ref": {
                            "type": "string",
                            "description": "Element reference from snapshot (e.g. 'e5').",
                        },
                        "text": {
                            "type": "string",
                            "description": "Text to type (for 'type' action) or key name (for 'press_key').",
                        },
                        "direction": {
                            "type": "string",
                            "enum": ["up", "down"],
                            "description": "Scroll direction (for 'scroll' action). Default: down.",
                        },
                    },
                    "required": ["action", "ref"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "lucy_browser_close",
                "description": (
                    "Close the browser tab when done browsing. "
                    "Always close when finished to free resources."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
        },
    ]


# ═══════════════════════════════════════════════════════════════════════════
# SESSION STATE — one tab per agent run
# ═══════════════════════════════════════════════════════════════════════════

# Simple per-workspace tab tracking. In production, use the task context
# to track tabs per conversation, not per workspace.
_active_tabs: dict[str, str] = {}  # workspace_id → tab_id


async def _get_or_create_tab(workspace_id: str) -> str:
    """Get existing tab or create a new one."""
    from lucy.integrations.camofox import get_camofox_client

    client = get_camofox_client()

    # Reuse existing tab if available
    if workspace_id in _active_tabs:
        tab_id = _active_tabs[workspace_id]
        try:
            # Verify tab still exists
            tabs = await client.list_tabs()
            tab_ids = {
                t.get("id") or t.get("tab_id") or t.get("tabId", "")
                for t in tabs
            }
            if tab_id in tab_ids:
                return tab_id
        except Exception:
            pass
        # Tab gone — create new
        del _active_tabs[workspace_id]

    tab_id = await client.create_tab(user_id=workspace_id)
    _active_tabs[workspace_id] = tab_id
    return tab_id


async def _close_tab(workspace_id: str) -> None:
    """Close the active tab for a workspace."""
    from lucy.integrations.camofox import get_camofox_client

    if workspace_id not in _active_tabs:
        return

    tab_id = _active_tabs.pop(workspace_id)
    try:
        client = get_camofox_client()
        await client.close_tab(tab_id)
    except Exception as e:
        logger.debug("tab_close_failed", tab_id=tab_id, error=str(e))


# ═══════════════════════════════════════════════════════════════════════════
# TOOL EXECUTION
# ═══════════════════════════════════════════════════════════════════════════

async def execute_browser_tool(
    tool_name: str,
    parameters: dict[str, Any],
    workspace_id: str = "",
) -> dict[str, Any]:
    """Execute a browser tool and return formatted results."""

    try:
        from lucy.integrations.camofox import get_camofox_client

        client = get_camofox_client()

        # Health check on first use
        if not await client.is_healthy():
            return {
                "error": (
                    "Browser service is not available right now. "
                    "I can try alternative approaches — web search via other tools, "
                    "or you can share the URL content directly."
                ),
            }

    except Exception as e:
        logger.warning("camofox_unavailable", error=str(e))
        return {
            "error": "Browser service is not available. Try an alternative approach.",
        }

    try:
        if tool_name == "lucy_browse_url":
            return await _handle_browse_url(client, parameters, workspace_id)

        elif tool_name == "lucy_browser_snapshot":
            return await _handle_snapshot(client, workspace_id)

        elif tool_name == "lucy_browser_interact":
            return await _handle_interact(client, parameters, workspace_id)

        elif tool_name == "lucy_browser_close":
            await _close_tab(workspace_id)
            return {"result": "Browser tab closed."}

        else:
            return {"error": f"Unknown browser tool: {tool_name}"}

    except Exception as e:
        logger.error(
            "browser_tool_error",
            tool=tool_name,
            error=str(e),
            workspace_id=workspace_id,
        )
        return {"error": f"Browser action failed: {str(e)[:200]}"}


async def _handle_browse_url(
    client: Any,
    parameters: dict[str, Any],
    workspace_id: str,
) -> dict[str, Any]:
    """Navigate to a URL and return page snapshot."""
    url = parameters.get("url", "").strip()
    if not url:
        return {"error": "No URL provided."}

    # Support @search shorthand
    if url.startswith("@search "):
        query = url[8:].strip()
        url = f"https://www.google.com/search?q={query.replace(' ', '+')}"

    # Add https:// if no protocol
    if not url.startswith(("http://", "https://", "@")):
        url = f"https://{url}"

    tab_id = await _get_or_create_tab(workspace_id)

    # Navigate
    nav_result = await client.navigate(tab_id, url)
    logger.info("browser_navigated", url=url, workspace_id=workspace_id)

    # Get snapshot
    snapshot = await client.snapshot(tab_id)

    return _format_snapshot(snapshot, url)


async def _handle_snapshot(
    client: Any,
    workspace_id: str,
) -> dict[str, Any]:
    """Get current page snapshot."""
    if workspace_id not in _active_tabs:
        return {"error": "No browser tab is open. Use lucy_browse_url first."}

    tab_id = _active_tabs[workspace_id]
    snapshot = await client.snapshot(tab_id)
    return _format_snapshot(snapshot)


async def _handle_interact(
    client: Any,
    parameters: dict[str, Any],
    workspace_id: str,
) -> dict[str, Any]:
    """Interact with a page element."""
    if workspace_id not in _active_tabs:
        return {"error": "No browser tab is open. Use lucy_browse_url first."}

    tab_id = _active_tabs[workspace_id]
    action = parameters.get("action", "")
    ref = parameters.get("ref", "")

    if not action or not ref:
        return {"error": "Both 'action' and 'ref' are required."}

    if action == "click":
        result = await client.click(tab_id, ref)
    elif action == "type":
        text = parameters.get("text", "")
        if not text:
            return {"error": "'text' is required for type action."}
        result = await client.type_text(tab_id, ref, text)
    elif action == "scroll":
        direction = parameters.get("direction", "down")
        result = await client.scroll(tab_id, ref, direction)
    elif action == "select":
        text = parameters.get("text", "")
        result = await client.select_option(tab_id, ref, text)
    elif action == "press_key":
        key = parameters.get("text", "Enter")
        result = await client.press_key(tab_id, ref, key)
    else:
        return {"error": f"Unknown action: {action}. Use click, type, scroll, select, or press_key."}

    logger.info(
        "browser_interacted",
        action=action,
        ref=ref,
        workspace_id=workspace_id,
    )

    # After interaction, get fresh snapshot
    snapshot = await client.snapshot(tab_id)
    return _format_snapshot(snapshot, interaction=f"{action} on {ref}")


# ═══════════════════════════════════════════════════════════════════════════
# OUTPUT FORMATTING
# ═══════════════════════════════════════════════════════════════════════════

def _format_snapshot(
    snapshot: dict[str, Any],
    url: str | None = None,
    interaction: str | None = None,
) -> dict[str, Any]:
    """Format a CamoFox snapshot for the LLM.

    Extracts readable text and interactive elements, truncating
    to fit within context window limits.
    """
    result: dict[str, Any] = {}

    if url:
        result["url"] = url
    if interaction:
        result["action_performed"] = interaction

    # Extract page title
    title = snapshot.get("title", "")
    if title:
        result["title"] = title

    # Extract text content (the accessibility snapshot)
    content = snapshot.get("snapshot", snapshot.get("content", ""))

    if isinstance(content, str):
        text = content
    elif isinstance(content, dict):
        # Recursive extraction from accessibility tree
        text = _extract_text_from_tree(content)
    elif isinstance(content, list):
        text = "\n".join(_extract_text_from_tree(c) if isinstance(c, dict) else str(c) for c in content)
    else:
        text = str(content) if content else "(empty page)"

    # Truncate
    if len(text) > _MAX_SNAPSHOT_CHARS:
        text = text[:_MAX_SNAPSHOT_CHARS] + "\n... (page truncated)"

    result["content"] = text

    # Extract interactive elements
    elements = snapshot.get("elements", [])
    if elements and isinstance(elements, list):
        interactive = []
        for el in elements[:50]:  # Max 50 elements
            ref = el.get("ref", "")
            role = el.get("role", "")
            name = el.get("name", el.get("text", ""))
            if ref and (role or name):
                interactive.append(f"[{ref}] {role}: {name}"[:100])
        if interactive:
            result["interactive_elements"] = "\n".join(interactive)

    return result


def _extract_text_from_tree(node: dict[str, Any], depth: int = 0) -> str:
    """Extract readable text from an accessibility tree node."""
    if depth > 10:
        return ""

    parts: list[str] = []

    # Node's own text
    name = node.get("name", "")
    role = node.get("role", "")
    ref = node.get("ref", "")
    value = node.get("value", "")

    if name:
        prefix = f"[{ref}] " if ref else ""
        if role in ("link", "button", "textbox", "combobox", "checkbox"):
            parts.append(f"{prefix}{role}: {name}")
        elif role == "heading":
            level = node.get("level", "")
            parts.append(f"\n{'#' * (int(level) if level else 2)} {name}")
        else:
            parts.append(f"{prefix}{name}")

    if value and value != name:
        parts.append(f"  value: {value}")

    # Recurse into children
    children = node.get("children", [])
    if isinstance(children, list):
        for child in children:
            if isinstance(child, dict):
                child_text = _extract_text_from_tree(child, depth + 1)
                if child_text:
                    parts.append(child_text)

    return "\n".join(parts)
