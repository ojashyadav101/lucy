"""Auto-discovery and registration of custom API wrappers as LLM-callable tools.

Scans custom_wrappers/<slug>/meta.json for saved wrappers and converts their
TOOLS lists into OpenAI-format function definitions that the agent loop can
dispatch. Wrappers are prefixed with ``lucy_custom_`` so they route through
the internal tool execution path, never hitting Composio.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()

_WRAPPERS_DIR = Path(__file__).parent


def load_custom_wrapper_tools(
    relevant_slugs: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Scan all custom wrapper directories and return OpenAI-format tool defs.

    Each wrapper must have:
      - meta.json with at minimum ``slug`` and ``service_name``
      - wrapper.py with a ``TOOLS`` list of dicts and an ``execute()`` function

    Tool names are prefixed with ``lucy_custom_`` so the agent routes them
    to ``_execute_custom_wrapper_tool`` instead of Composio.

    Args:
        relevant_slugs: If provided, only load wrappers whose slug is in this
            list. Pass None to load all wrappers (legacy behavior).
    """
    tool_defs: list[dict[str, Any]] = []

    if not _WRAPPERS_DIR.exists():
        return tool_defs

    for meta_path in _WRAPPERS_DIR.glob("*/meta.json"):
        slug_dir = meta_path.parent
        wrapper_path = slug_dir / "wrapper.py"

        if not wrapper_path.exists():
            continue

        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("custom_wrapper_meta_unreadable", path=str(meta_path))
            continue

        slug = meta.get("slug", slug_dir.name)

        # Skip this wrapper if we only want specific slugs
        if relevant_slugs is not None and slug not in relevant_slugs:
            continue

        try:
            spec = importlib.util.spec_from_file_location(
                f"custom_wrapper_{slug}", str(wrapper_path),
            )
            if not spec or not spec.loader:
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception as exc:
            logger.warning(
                "custom_wrapper_import_failed",
                slug=slug,
                error=str(exc),
            )
            continue

        raw_tools: list[dict[str, Any]] = getattr(mod, "TOOLS", [])
        has_execute = callable(getattr(mod, "execute", None))

        if not raw_tools or not has_execute:
            logger.warning(
                "custom_wrapper_missing_interface",
                slug=slug,
                has_tools=bool(raw_tools),
                has_execute=has_execute,
            )
            continue

        registered_names: list[str] = []
        for tool in raw_tools:
            if not isinstance(tool, dict):
                continue
            original_name = tool.get("name", "")
            if not original_name:
                continue

            prefixed_name = f"lucy_custom_{original_name}"
            raw_desc = tool.get("description", f"{original_name} from {meta.get('service_name', slug)}")
            description = f"{raw_desc} (Call this tool directly — do NOT use COMPOSIO_MULTI_EXECUTE_TOOL for this.)"
            parameters = tool.get("parameters", {"type": "object", "properties": {}})

            tool_defs.append({
                "type": "function",
                "function": {
                    "name": prefixed_name,
                    "description": description,
                    "parameters": parameters,
                },
            })
            registered_names.append(prefixed_name)

        if registered_names:
            meta["tools"] = [n.replace("lucy_custom_", "") for n in registered_names]
            meta["total_tools"] = len(registered_names)
            meta_path.write_text(
                json.dumps(meta, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            logger.info(
                "custom_wrapper_loaded",
                slug=slug,
                service=meta.get("service_name", slug),
                tool_count=len(registered_names),
                tools=registered_names,
            )

    return tool_defs


def detect_relevant_wrappers(
    message: str,
    allow_load_all: bool = True,
) -> list[str] | None:
    """Detect which custom wrappers are relevant to the user's message.

    Returns a list of wrapper slugs that should be loaded, or None if
    ALL wrappers should be loaded (when the message is ambiguous or
    explicitly mentions "integrations"/"tools"/"what can you do").

    If *allow_load_all* is False (used for cron executions), the
    broad "load everything" shortcut is skipped — only wrappers whose
    keywords appear in the message are returned.

    This saves ~12K tokens per request by not loading 66 irrelevant
    tool definitions (e.g., 31 Clerk + 35 Polar.sh tools when the
    user is asking about calendars).
    """
    import re

    msg = message.lower()

    # If the user is asking about tools/integrations broadly, load everything
    if allow_load_all:
        _LOAD_ALL = re.compile(
            r"\b(?:integrations?|tools?|what can you|capabilities|services?|connected)\b",
            re.IGNORECASE,
        )
        if _LOAD_ALL.search(msg):
            return None  # load all

    # Map wrapper slugs to trigger keywords
    _WRAPPER_TRIGGERS: dict[str, list[str]] = {}

    if not _WRAPPERS_DIR.exists():
        return []

    for meta_path in _WRAPPERS_DIR.glob("*/meta.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        slug = meta.get("slug", meta_path.parent.name)
        service = meta.get("service_name", slug).lower()
        # Use slug and service name as keywords
        _WRAPPER_TRIGGERS[slug] = [slug, service, service.replace(".", "")]

    # Add known keyword expansions
    _EXTRA_KEYWORDS: dict[str, list[str]] = {
        "clerk": ["user", "users", "auth", "authentication", "sign up", "signup", "login", "session", "organization"],
        "polarsh": ["polar", "subscription", "product", "order", "customer", "checkout", "billing", "payment", "benefit", "discount"],
    }

    relevant: list[str] = []
    for slug, keywords in _WRAPPER_TRIGGERS.items():
        all_keywords = keywords + _EXTRA_KEYWORDS.get(slug, [])
        for kw in all_keywords:
            if kw in msg:
                relevant.append(slug)
                break

    return relevant if relevant else []


def execute_custom_tool(
    tool_name: str,
    parameters: dict[str, Any],
    api_key: str,
) -> Any:
    """Execute a custom wrapper tool by name.

    ``tool_name`` arrives with the ``lucy_custom_`` prefix stripped already
    (e.g. ``polarsh_list_products``). The slug is the part before the first
    underscore-separated action verb.
    """
    parts = tool_name.split("_", 1)
    slug = parts[0] if parts else tool_name

    wrapper_path = _WRAPPERS_DIR / slug / "wrapper.py"
    if not wrapper_path.exists():
        return {"error": f"No wrapper found for slug '{slug}'"}

    try:
        spec = importlib.util.spec_from_file_location(
            f"custom_wrapper_{slug}", str(wrapper_path),
        )
        if not spec or not spec.loader:
            return {"error": f"Cannot load wrapper for '{slug}'"}

        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as exc:
        return {"error": f"Failed to import wrapper for '{slug}': {exc}"}

    execute_fn = getattr(mod, "execute", None)
    if not callable(execute_fn):
        return {"error": f"Wrapper for '{slug}' has no execute() function"}

    import asyncio

    if asyncio.iscoroutinefunction(execute_fn):
        return execute_fn(tool_name, parameters, api_key)

    return execute_fn(tool_name, parameters, api_key)
