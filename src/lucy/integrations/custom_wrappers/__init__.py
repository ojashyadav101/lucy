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


def load_custom_wrapper_tools() -> list[dict[str, Any]]:
    """Scan all custom wrapper directories and return OpenAI-format tool defs.

    Each wrapper must have:
      - meta.json with at minimum ``slug`` and ``service_name``
      - wrapper.py with a ``TOOLS`` list of dicts and an ``execute()`` function

    Tool names are prefixed with ``lucy_custom_`` so the agent routes them
    to ``_execute_custom_wrapper_tool`` instead of Composio.
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
            description = tool.get("description", f"{original_name} from {meta.get('service_name', slug)}")
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
