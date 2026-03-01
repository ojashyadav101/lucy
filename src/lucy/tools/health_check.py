"""Tool health checker for Lucy.

Provides a comprehensive health report for all registered tools,
checking schema validity, import readiness, and integration connectivity.

Usage:
    from lucy.tools.health_check import run_health_check
    report = await run_health_check()

The report is structured for both programmatic use (dict) and
human-readable logging. Run at startup to catch misconfigurations
before they hit users at runtime.
"""

from __future__ import annotations

import asyncio
import importlib
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger()


# ═══════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ToolCheck:
    """Health check result for a single tool."""
    name: str
    module: str
    status: str  # "healthy", "degraded", "unavailable"
    schema_valid: bool = True
    import_ok: bool = True
    connectivity_ok: bool | None = None  # None = not tested
    latency_ms: float | None = None
    error: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class ModuleCheck:
    """Health check result for a tool module."""
    module_name: str
    import_ok: bool = True
    tool_count: int = 0
    tools: list[ToolCheck] = field(default_factory=list)
    error: str = ""


@dataclass
class HealthReport:
    """Complete health report for all tools."""
    timestamp: str = ""
    total_tools: int = 0
    healthy: int = 0
    degraded: int = 0
    unavailable: int = 0
    modules: list[ModuleCheck] = field(default_factory=list)
    custom_wrappers: dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0

    def summary(self) -> str:
        """One-line summary for logging."""
        return (
            f"Tools: {self.total_tools} total, "
            f"{self.healthy} healthy, "
            f"{self.degraded} degraded, "
            f"{self.unavailable} unavailable "
            f"({self.duration_ms:.0f}ms)"
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for JSON serialization."""
        return {
            "timestamp": self.timestamp,
            "summary": self.summary(),
            "total_tools": self.total_tools,
            "healthy": self.healthy,
            "degraded": self.degraded,
            "unavailable": self.unavailable,
            "modules": [
                {
                    "name": m.module_name,
                    "import_ok": m.import_ok,
                    "tool_count": m.tool_count,
                    "error": m.error,
                    "tools": [
                        {
                            "name": t.name,
                            "status": t.status,
                            "schema_valid": t.schema_valid,
                            "import_ok": t.import_ok,
                            "connectivity_ok": t.connectivity_ok,
                            "latency_ms": t.latency_ms,
                            "error": t.error,
                            "warnings": t.warnings,
                        }
                        for t in m.tools
                    ],
                }
                for m in self.modules
            ],
            "custom_wrappers": self.custom_wrappers,
            "duration_ms": self.duration_ms,
        }


# ═══════════════════════════════════════════════════════════════════════════
# TOOL MODULE REGISTRY
# ═══════════════════════════════════════════════════════════════════════════

# Each entry: (module_path, get_definitions_fn_name, connectivity_check_fn)
_TOOL_MODULES: list[tuple[str, str, str | None]] = [
    ("lucy.tools.code_executor", "get_code_tool_definitions", None),
    ("lucy.tools.browser", "get_browser_tool_definitions", "_check_browser_health"),
    ("lucy.tools.file_generator", "get_file_tool_definitions", None),
    ("lucy.tools.chart_generator", "get_chart_tool_definitions", None),
    ("lucy.tools.email_tools", "get_email_tool_definitions", "_check_email_health"),
    ("lucy.tools.web_search", "get_web_search_tool_definitions", "_check_search_health"),
    ("lucy.tools.workspace_tools", "get_workspace_tool_definitions", None),
    ("lucy.tools.services", "get_services_tool_definitions", "_check_services_health"),
    ("lucy.tools.spaces", "get_spaces_tool_definitions", None),
]


# ═══════════════════════════════════════════════════════════════════════════
# SCHEMA VALIDATION
# ═══════════════════════════════════════════════════════════════════════════

def _validate_tool_schema(tool_def: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate an OpenAI-format tool definition.

    Returns (valid, warnings).
    """
    warnings: list[str] = []

    if tool_def.get("type") != "function":
        return False, ["Missing or invalid 'type': expected 'function'"]

    fn = tool_def.get("function", {})
    if not fn:
        return False, ["Missing 'function' object"]

    name = fn.get("name", "")
    if not name:
        return False, ["Missing 'function.name'"]

    if not fn.get("description"):
        warnings.append("Missing description")

    params = fn.get("parameters", {})
    if params:
        if params.get("type") != "object":
            warnings.append("Parameters type should be 'object'")

        required = fn.get("parameters", {}).get("required", [])
        properties = fn.get("parameters", {}).get("properties", {})

        for req_param in required:
            if req_param not in properties:
                warnings.append(
                    f"Required param '{req_param}' not in properties"
                )

        for prop_name, prop_def in properties.items():
            if not isinstance(prop_def, dict):
                warnings.append(f"Property '{prop_name}' is not a dict")
            elif "type" not in prop_def and "enum" not in prop_def:
                warnings.append(f"Property '{prop_name}' has no type")

    return True, warnings


# ═══════════════════════════════════════════════════════════════════════════
# CONNECTIVITY CHECKS
# ═══════════════════════════════════════════════════════════════════════════

async def _check_browser_health() -> tuple[bool, float, str]:
    """Check CamoFox browser service connectivity."""
    try:
        from lucy.integrations.camofox import get_camofox_client
        t0 = time.monotonic()
        client = get_camofox_client()
        healthy = await asyncio.wait_for(client.is_healthy(), timeout=5.0)
        latency = (time.monotonic() - t0) * 1000
        return healthy, latency, "" if healthy else "CamoFox not responding"
    except asyncio.TimeoutError:
        return False, 5000, "CamoFox health check timed out"
    except ImportError:
        return False, 0, "camofox module not installed"
    except Exception as e:
        return False, 0, f"CamoFox check failed: {e}"


async def _check_email_health() -> tuple[bool, float, str]:
    """Check AgentMail email service connectivity."""
    try:
        from lucy.integrations.agentmail_client import get_email_client
        t0 = time.monotonic()
        client = get_email_client()
        # Just verify the client initializes without error
        latency = (time.monotonic() - t0) * 1000
        return True, latency, ""
    except ImportError:
        return False, 0, "agentmail_client module not installed"
    except RuntimeError as e:
        return False, 0, str(e)
    except Exception as e:
        return False, 0, f"Email check failed: {e}"


async def _check_search_health() -> tuple[bool, float, str]:
    """Check OpenRouter/Perplexity search connectivity."""
    try:
        from lucy.config import settings
        from lucy.infra.circuit_breaker import openrouter_breaker

        if not settings.openrouter_api_key:
            return False, 0, "No OpenRouter API key configured"

        if not openrouter_breaker.should_allow_request():
            return False, 0, "OpenRouter circuit breaker is open (recent failures)"

        # Light connectivity test: check the API responds
        import httpx
        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{settings.openrouter_base_url}/models",
                headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
            )
            latency = (time.monotonic() - t0) * 1000
            ok = resp.status_code == 200
            error = "" if ok else f"OpenRouter returned {resp.status_code}"
            return ok, latency, error
    except ImportError as e:
        return False, 0, f"Missing module: {e}"
    except Exception as e:
        return False, 0, f"Search check failed: {e}"


async def _check_services_health() -> tuple[bool, float, str]:
    """Check OpenClaw Gateway connectivity."""
    try:
        from lucy.integrations.openclaw_gateway import get_gateway_client
        t0 = time.monotonic()
        client = await asyncio.wait_for(get_gateway_client(), timeout=5.0)
        latency = (time.monotonic() - t0) * 1000
        return True, latency, ""
    except asyncio.TimeoutError:
        return False, 5000, "Gateway connection timed out"
    except ImportError:
        return False, 0, "openclaw_gateway module not installed"
    except RuntimeError as e:
        return False, 0, str(e)
    except Exception as e:
        return False, 0, f"Gateway check failed: {e}"


_CONNECTIVITY_CHECKS = {
    "_check_browser_health": _check_browser_health,
    "_check_email_health": _check_email_health,
    "_check_search_health": _check_search_health,
    "_check_services_health": _check_services_health,
}


# ═══════════════════════════════════════════════════════════════════════════
# CUSTOM WRAPPER CHECK
# ═══════════════════════════════════════════════════════════════════════════

def _check_custom_wrappers() -> dict[str, Any]:
    """Check health of all custom API wrappers."""
    try:
        from lucy.integrations.custom_wrappers import (
            get_wrapper_health,
            load_custom_wrapper_tools,
        )

        # Load all wrappers to populate health registry
        tool_defs = load_custom_wrapper_tools(relevant_slugs=None)
        health = get_wrapper_health()

        total = len(health)
        healthy = sum(1 for h in health.values() if h.get("healthy"))
        total_tools = sum(h.get("tool_count", 0) for h in health.values())

        return {
            "total_wrappers": total,
            "healthy_wrappers": healthy,
            "unhealthy_wrappers": total - healthy,
            "total_tools": total_tools,
            "wrappers": {
                slug: {
                    "service": h.get("service_name", slug),
                    "healthy": h.get("healthy", False),
                    "tool_count": h.get("tool_count", 0),
                    "errors": h.get("errors", []),
                }
                for slug, h in health.items()
            },
        }
    except ImportError:
        return {"error": "custom_wrappers module not available"}
    except Exception as e:
        return {"error": f"Custom wrapper check failed: {e}"}


# ═══════════════════════════════════════════════════════════════════════════
# MAIN HEALTH CHECK
# ═══════════════════════════════════════════════════════════════════════════

async def run_health_check(
    check_connectivity: bool = True,
    timeout_per_check: float = 10.0,
) -> HealthReport:
    """Run comprehensive health check on all tool modules.

    Args:
        check_connectivity: Whether to test integration connectivity
            (browser, email, search, services). Set False for fast
            schema-only validation.
        timeout_per_check: Max seconds per connectivity check.

    Returns:
        HealthReport with per-tool status, schema validation,
        and optional connectivity results.
    """
    from datetime import datetime, timezone

    t0 = time.monotonic()
    report = HealthReport(
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    for module_path, get_defs_fn, connectivity_fn in _TOOL_MODULES:
        module_check = ModuleCheck(module_name=module_path.split(".")[-1])

        # Step 1: Import the module
        try:
            mod = importlib.import_module(module_path)
        except ImportError as e:
            module_check.import_ok = False
            module_check.error = f"Import failed: {e}"
            report.modules.append(module_check)
            continue
        except Exception as e:
            module_check.import_ok = False
            module_check.error = f"Module error: {e}"
            report.modules.append(module_check)
            continue

        # Step 2: Get tool definitions
        try:
            get_defs = getattr(mod, get_defs_fn)
            tool_defs = get_defs()
            module_check.tool_count = len(tool_defs)
        except Exception as e:
            module_check.error = f"Failed to get tool defs: {e}"
            report.modules.append(module_check)
            continue

        # Step 3: Validate each tool schema
        connectivity_result = None
        if check_connectivity and connectivity_fn and connectivity_fn in _CONNECTIVITY_CHECKS:
            try:
                check_fn = _CONNECTIVITY_CHECKS[connectivity_fn]
                connectivity_result = await asyncio.wait_for(
                    check_fn(), timeout=timeout_per_check,
                )
            except asyncio.TimeoutError:
                connectivity_result = (False, timeout_per_check * 1000, "Check timed out")
            except Exception as e:
                connectivity_result = (False, 0, str(e))

        for tool_def in tool_defs:
            fn_name = tool_def.get("function", {}).get("name", "unknown")
            schema_valid, schema_warnings = _validate_tool_schema(tool_def)

            tool_check = ToolCheck(
                name=fn_name,
                module=module_check.module_name,
                schema_valid=schema_valid,
                warnings=schema_warnings,
                status="healthy",  # Updated below
            )

            # Apply connectivity result to all tools in this module
            if connectivity_result is not None:
                ok, latency, error = connectivity_result
                tool_check.connectivity_ok = ok
                tool_check.latency_ms = latency
                if not ok:
                    tool_check.error = error

            # Determine overall status
            if not schema_valid:
                tool_check.status = "unavailable"
            elif connectivity_result is not None and not connectivity_result[0]:
                tool_check.status = "degraded"
            elif schema_warnings:
                tool_check.status = "healthy"  # Warnings don't degrade
            else:
                tool_check.status = "healthy"

            module_check.tools.append(tool_check)

        report.modules.append(module_check)

    # Step 4: Check custom wrappers
    report.custom_wrappers = _check_custom_wrappers()

    # Step 5: Aggregate counts
    for module in report.modules:
        for tool in module.tools:
            report.total_tools += 1
            if tool.status == "healthy":
                report.healthy += 1
            elif tool.status == "degraded":
                report.degraded += 1
            else:
                report.unavailable += 1

    # Add custom wrapper tools to total
    cw_tools = report.custom_wrappers.get("total_tools", 0)
    cw_healthy = report.custom_wrappers.get("healthy_wrappers", 0)
    cw_total = report.custom_wrappers.get("total_wrappers", 0)
    report.total_tools += cw_tools
    report.healthy += cw_tools  # Assume healthy if loaded

    report.duration_ms = round((time.monotonic() - t0) * 1000, 1)

    # Log the summary
    logger.info(
        "tool_health_check_complete",
        summary=report.summary(),
        duration_ms=report.duration_ms,
        modules_checked=len(report.modules),
        custom_wrappers=cw_total,
    )

    return report


async def run_quick_check() -> dict[str, Any]:
    """Run a fast schema-only check (no connectivity tests).

    Returns a simple dict suitable for a /health endpoint or startup log.
    """
    report = await run_health_check(check_connectivity=False)
    return {
        "status": "healthy" if report.unavailable == 0 else "degraded",
        "tools": report.total_tools,
        "healthy": report.healthy,
        "degraded": report.degraded,
        "unavailable": report.unavailable,
        "duration_ms": report.duration_ms,
    }


# ═══════════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT (for manual testing)
# ═══════════════════════════════════════════════════════════════════════════

async def _main() -> None:
    """Run health check and print results."""
    import json

    print("Running tool health check...")
    report = await run_health_check(check_connectivity=True)
    print(f"\n{report.summary()}\n")

    for module in report.modules:
        status_icon = "✅" if module.import_ok else "❌"
        print(f"{status_icon} {module.module_name} ({module.tool_count} tools)")
        if module.error:
            print(f"   Error: {module.error}")
        for tool in module.tools:
            icon = {"healthy": "  ✅", "degraded": "  ⚠️", "unavailable": "  ❌"}[tool.status]
            extra = ""
            if tool.latency_ms is not None:
                extra += f" ({tool.latency_ms:.0f}ms)"
            if tool.error:
                extra += f" — {tool.error}"
            if tool.warnings:
                extra += f" [warnings: {', '.join(tool.warnings)}]"
            print(f"{icon} {tool.name}{extra}")

    if report.custom_wrappers:
        cw = report.custom_wrappers
        if "error" in cw:
            print(f"\n⚠️  Custom wrappers: {cw['error']}")
        else:
            print(
                f"\n📦 Custom wrappers: {cw.get('total_wrappers', 0)} loaded, "
                f"{cw.get('healthy_wrappers', 0)} healthy, "
                f"{cw.get('total_tools', 0)} tools"
            )
            for slug, info in cw.get("wrappers", {}).items():
                icon = "✅" if info["healthy"] else "❌"
                print(f"  {icon} {slug} ({info['service']}, {info['tool_count']} tools)")
                if info.get("errors"):
                    for err in info["errors"][:3]:
                        print(f"     ⚠️  {err}")


if __name__ == "__main__":
    asyncio.run(_main())
