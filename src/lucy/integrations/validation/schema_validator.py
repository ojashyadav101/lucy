"""Schema validation for custom API wrapper tool definitions.

Validates that wrapper TOOLS lists and execute() functions conform to the
expected contract before they are registered in the agent's tool list.
Catches broken schemas, missing handlers, and parameter mismatches at
load time instead of at runtime when a user is waiting.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable

import structlog

logger = structlog.get_logger()

# Required top-level keys for each tool definition
_REQUIRED_TOOL_KEYS = {"name", "description", "parameters"}

# Valid JSON Schema types for parameters
_VALID_JSON_TYPES = {"string", "integer", "number", "boolean", "array", "object", "null"}


@dataclass
class ValidationIssue:
    """A single validation issue found during schema checking."""

    tool_name: str
    severity: str  # "error" | "warning"
    message: str


@dataclass
class SchemaValidationResult:
    """Result of validating a wrapper's TOOLS list against its execute function."""

    slug: str
    valid: bool = True
    issues: list[ValidationIssue] = field(default_factory=list)
    tools_checked: int = 0
    tools_passed: int = 0

    def add_error(self, tool_name: str, message: str) -> None:
        self.issues.append(ValidationIssue(tool_name, "error", message))
        self.valid = False

    def add_warning(self, tool_name: str, message: str) -> None:
        self.issues.append(ValidationIssue(tool_name, "warning", message))


def _validate_parameter_schema(
    tool_name: str,
    schema: dict[str, Any],
    result: SchemaValidationResult,
    *,
    path: str = "parameters",
) -> None:
    """Recursively validate a JSON-Schema-like parameter definition."""
    schema_type = schema.get("type")
    if schema_type and schema_type not in _VALID_JSON_TYPES:
        result.add_warning(
            tool_name,
            f"{path}.type is '{schema_type}' — not a standard JSON Schema type",
        )

    if schema_type == "object":
        props = schema.get("properties")
        if props is not None and not isinstance(props, dict):
            result.add_error(
                tool_name,
                f"{path}.properties must be a dict, got {type(props).__name__}",
            )
        elif isinstance(props, dict):
            for prop_name, prop_schema in props.items():
                if not isinstance(prop_schema, dict):
                    result.add_error(
                        tool_name,
                        f"{path}.properties.{prop_name} must be a dict",
                    )
                else:
                    _validate_parameter_schema(
                        tool_name,
                        prop_schema,
                        result,
                        path=f"{path}.properties.{prop_name}",
                    )

        required = schema.get("required")
        if required is not None:
            if not isinstance(required, list):
                result.add_error(
                    tool_name,
                    f"{path}.required must be a list, got {type(required).__name__}",
                )
            elif isinstance(props, dict):
                for req_key in required:
                    if req_key not in props:
                        result.add_warning(
                            tool_name,
                            f"{path}.required lists '{req_key}' but it's not in properties",
                        )

    if schema_type == "array":
        items = schema.get("items")
        if items is not None and isinstance(items, dict):
            _validate_parameter_schema(
                tool_name, items, result, path=f"{path}.items"
            )


def _extract_dispatch_targets(execute_fn: Callable) -> set[str]:
    """Best-effort extraction of tool names handled by an execute() function.

    Scans the function source for string comparisons against tool_name
    (e.g. ``if tool_name == "clerk_list_users"``).  This is heuristic —
    dynamically dispatched wrappers will return an empty set, which is
    fine (we just skip the coverage check).
    """
    try:
        source = inspect.getsource(execute_fn)
    except (OSError, TypeError):
        return set()

    targets: set[str] = set()
    for line in source.splitlines():
        stripped = line.strip()
        # Match patterns like: tool_name == "clerk_list_users"
        for quote in ('"', "'"):
            marker = f"tool_name == {quote}"
            idx = stripped.find(marker)
            if idx != -1:
                end = stripped.find(quote, idx + len(marker))
                if end != -1:
                    targets.add(stripped[idx + len(marker) : end])
    return targets


def validate_tool_definitions(
    slug: str,
    tools: list[dict[str, Any]],
    execute_fn: Callable | None = None,
) -> SchemaValidationResult:
    """Validate a wrapper's TOOLS list and cross-check against execute().

    Checks performed:
    1. Each tool dict has required keys (name, description, parameters)
    2. Tool names are non-empty strings with the correct slug prefix
    3. Parameter schemas are well-formed JSON Schema objects
    4. If execute() source is available, checks every TOOLS entry has a handler
    5. Descriptions are non-empty and informative

    Parameters
    ----------
    slug:
        The wrapper slug (e.g. ``"clerk"``, ``"polarsh"``).
    tools:
        The raw TOOLS list from the wrapper module.
    execute_fn:
        The wrapper's ``execute()`` callable (optional — enables dispatch
        coverage checking).

    Returns
    -------
    SchemaValidationResult with per-tool issues.
    """
    result = SchemaValidationResult(slug=slug)

    if not isinstance(tools, list):
        result.add_error("__root__", f"TOOLS must be a list, got {type(tools).__name__}")
        return result

    if not tools:
        result.add_error("__root__", "TOOLS list is empty — nothing to register")
        return result

    # Extract dispatch targets from execute() if available
    dispatch_targets = (
        _extract_dispatch_targets(execute_fn) if execute_fn else set()
    )

    seen_names: set[str] = set()

    for idx, tool in enumerate(tools):
        result.tools_checked += 1
        tool_ok = True

        if not isinstance(tool, dict):
            result.add_error(
                f"__index_{idx}",
                f"TOOLS[{idx}] must be a dict, got {type(tool).__name__}",
            )
            continue

        name = tool.get("name", "")
        label = name or f"TOOLS[{idx}]"

        # -- Required keys --
        missing = _REQUIRED_TOOL_KEYS - set(tool.keys())
        if missing:
            result.add_error(label, f"Missing required keys: {sorted(missing)}")
            tool_ok = False

        # -- Name checks --
        if not name:
            result.add_error(label, "Tool 'name' is empty or missing")
            tool_ok = False
        elif not isinstance(name, str):
            result.add_error(label, f"Tool 'name' must be a string, got {type(name).__name__}")
            tool_ok = False
        else:
            if not name.startswith(f"{slug}_"):
                result.add_warning(
                    name,
                    f"Tool name '{name}' does not start with slug prefix '{slug}_'",
                )
            if name in seen_names:
                result.add_error(name, f"Duplicate tool name: '{name}'")
                tool_ok = False
            seen_names.add(name)

        # -- Description --
        desc = tool.get("description", "")
        if not desc or not isinstance(desc, str):
            result.add_warning(label, "Tool description is empty or missing")
        elif len(desc) < 10:
            result.add_warning(name, "Tool description is very short — may confuse the LLM")

        # -- Parameters --
        params = tool.get("parameters")
        if params is None:
            result.add_warning(label, "No 'parameters' key — tool accepts no arguments")
        elif not isinstance(params, dict):
            result.add_error(label, f"'parameters' must be a dict, got {type(params).__name__}")
            tool_ok = False
        else:
            _validate_parameter_schema(name or label, params, result)

        # -- Dispatch coverage --
        if dispatch_targets and name and name not in dispatch_targets:
            result.add_error(
                name,
                f"Tool '{name}' is in TOOLS but has no handler in execute()",
            )
            tool_ok = False

        if tool_ok:
            result.tools_passed += 1

    # Reverse check: handlers without TOOLS entries
    if dispatch_targets:
        orphan_handlers = dispatch_targets - seen_names
        for orphan in sorted(orphan_handlers):
            result.add_warning(
                orphan,
                f"execute() handles '{orphan}' but it's not in the TOOLS list",
            )

    return result
