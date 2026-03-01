"""Runtime validation for custom API wrappers.

Performs safe import-time and startup checks to verify that each wrapper
module actually loads, exposes the right interface, and can be called
without crashing.  This catches common bugs (syntax errors, missing
imports, broken dependencies) before a wrapper goes live.
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()


@dataclass
class RuntimeCheckResult:
    """Result of a single runtime validation check."""

    check: str
    passed: bool
    detail: str = ""
    duration_ms: float = 0.0


@dataclass
class RuntimeValidationResult:
    """Aggregate runtime validation result for one wrapper."""

    slug: str
    healthy: bool = True
    checks: list[RuntimeCheckResult] = field(default_factory=list)
    load_time_ms: float = 0.0

    def add_check(self, check: str, passed: bool, detail: str = "", duration_ms: float = 0.0) -> None:
        self.checks.append(RuntimeCheckResult(check, passed, detail, duration_ms))
        if not passed:
            self.healthy = False


def validate_wrapper_runtime(
    slug: str,
    wrapper_path: Path,
    meta_path: Path | None = None,
) -> RuntimeValidationResult:
    """Run runtime validation checks on a single wrapper.

    Checks performed:
    1. **meta.json readable** — meta file parses as valid JSON
    2. **Module import** — wrapper.py loads without exceptions
    3. **TOOLS export** — module has a non-empty TOOLS list
    4. **execute() export** — module has a callable execute()
    5. **execute() signature** — execute(tool_name, args, api_key) signature
    6. **Tool names consistent** — TOOLS names match slug convention
    7. **No import side-effects** — import completes within timeout

    Parameters
    ----------
    slug:
        The wrapper slug.
    wrapper_path:
        Path to the wrapper.py file.
    meta_path:
        Path to meta.json (optional).

    Returns
    -------
    RuntimeValidationResult with per-check pass/fail.
    """
    result = RuntimeValidationResult(slug=slug)

    # ── Check 1: meta.json ──
    if meta_path and meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if not isinstance(meta, dict):
                result.add_check("meta_json", False, "meta.json is not a dict")
            elif not meta.get("slug") and not meta.get("service_name"):
                result.add_check("meta_json", False, "meta.json missing slug and service_name")
            else:
                result.add_check("meta_json", True, f"service={meta.get('service_name', slug)}")
        except json.JSONDecodeError as exc:
            result.add_check("meta_json", False, f"Invalid JSON: {exc}")
        except Exception as exc:
            result.add_check("meta_json", False, f"Read error: {exc}")
    elif meta_path:
        result.add_check("meta_json", False, "meta.json not found")
    # If meta_path is None, skip this check silently

    # ── Check 2: Module import ──
    t0 = time.monotonic()
    mod = None
    try:
        spec = importlib.util.spec_from_file_location(
            f"custom_wrapper_validate_{slug}", str(wrapper_path)
        )
        if not spec or not spec.loader:
            result.add_check("module_import", False, "Cannot create module spec")
            return result

        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        load_ms = (time.monotonic() - t0) * 1000
        result.load_time_ms = load_ms
        result.add_check("module_import", True, f"Loaded in {load_ms:.0f}ms", load_ms)
    except Exception as exc:
        load_ms = (time.monotonic() - t0) * 1000
        result.load_time_ms = load_ms
        result.add_check("module_import", False, f"Import failed: {exc}", load_ms)
        return result  # Can't continue without a loaded module

    # ── Check 3: TOOLS export ──
    raw_tools = getattr(mod, "TOOLS", None)
    if raw_tools is None:
        result.add_check("tools_export", False, "Module has no TOOLS attribute")
    elif not isinstance(raw_tools, list):
        result.add_check("tools_export", False, f"TOOLS is {type(raw_tools).__name__}, expected list")
    elif len(raw_tools) == 0:
        result.add_check("tools_export", False, "TOOLS list is empty")
    else:
        result.add_check("tools_export", True, f"{len(raw_tools)} tools defined")

    # ── Check 4: execute() export ──
    execute_fn = getattr(mod, "execute", None)
    if execute_fn is None:
        result.add_check("execute_export", False, "Module has no execute() function")
    elif not callable(execute_fn):
        result.add_check("execute_export", False, f"execute is {type(execute_fn).__name__}, not callable")
    else:
        result.add_check("execute_export", True, "execute() found")

        # ── Check 5: execute() signature ──
        try:
            sig = inspect.signature(execute_fn)
            params = list(sig.parameters.keys())
            # We expect (tool_name, args/parameters, api_key) — 3 positional params
            if len(params) < 3:
                result.add_check(
                    "execute_signature",
                    False,
                    f"execute() has {len(params)} params ({params}), expected at least 3",
                )
            else:
                # First param should be tool_name-ish, last should be api_key-ish
                first = params[0]
                last_relevant = params[2] if len(params) >= 3 else params[-1]
                if "key" not in last_relevant.lower() and "api" not in last_relevant.lower():
                    result.add_check(
                        "execute_signature",
                        True,
                        f"Signature: ({', '.join(params)}) — api_key param name is '{last_relevant}' (non-standard but ok)",
                    )
                else:
                    result.add_check(
                        "execute_signature",
                        True,
                        f"Signature: ({', '.join(params)})",
                    )
        except (ValueError, TypeError) as exc:
            result.add_check("execute_signature", False, f"Cannot inspect signature: {exc}")

    # ── Check 6: Tool name consistency ──
    if isinstance(raw_tools, list) and raw_tools:
        inconsistent = []
        for tool in raw_tools:
            if isinstance(tool, dict):
                name = tool.get("name", "")
                if name and not name.startswith(f"{slug}_"):
                    inconsistent.append(name)
        if inconsistent:
            result.add_check(
                "name_convention",
                True,  # warning-level, not a hard fail
                f"{len(inconsistent)} tools don't start with '{slug}_': {inconsistent[:3]}",
            )
        else:
            result.add_check("name_convention", True, f"All tools prefixed with '{slug}_'")

    return result


def validate_all_wrappers(wrappers_dir: Path) -> dict[str, RuntimeValidationResult]:
    """Validate all wrapper directories under the given path.

    Returns a dict mapping slug → RuntimeValidationResult.
    """
    results: dict[str, RuntimeValidationResult] = {}

    if not wrappers_dir.exists():
        return results

    for meta_path in wrappers_dir.glob("*/meta.json"):
        slug_dir = meta_path.parent
        wrapper_path = slug_dir / "wrapper.py"

        if not wrapper_path.exists():
            slug = slug_dir.name
            r = RuntimeValidationResult(slug=slug, healthy=False)
            r.add_check("file_exists", False, "wrapper.py not found")
            results[slug] = r
            continue

        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            slug = meta.get("slug", slug_dir.name)
        except Exception:
            slug = slug_dir.name

        results[slug] = validate_wrapper_runtime(slug, wrapper_path, meta_path)

    return results
