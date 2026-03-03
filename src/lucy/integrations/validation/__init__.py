"""Validation framework for custom API wrapper integrations.

Provides schema-level and runtime validation to catch broken wrappers
before they reach users.  Import the top-level functions from here:

    from lucy.integrations.validation import (
        validate_wrapper,
        WrapperHealth,
    )
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from .runtime_validator import (
    RuntimeValidationResult,
    validate_wrapper_runtime,
)
from .schema_validator import SchemaValidationResult, validate_tool_definitions

logger = structlog.get_logger()


@dataclass
class WrapperHealth:
    """Unified health status for a single wrapper, combining schema + runtime checks."""

    slug: str
    service_name: str
    healthy: bool = True
    schema_result: SchemaValidationResult | None = None
    runtime_result: RuntimeValidationResult | None = None
    error_count: int = 0
    warning_count: int = 0
    tools_registered: int = 0

    def summary(self) -> str:
        """One-line health summary for logging."""
        status = "✓ healthy" if self.healthy else "✗ unhealthy"
        parts = [f"{self.slug}: {status}"]
        if self.tools_registered:
            parts.append(f"{self.tools_registered} tools")
        if self.error_count:
            parts.append(f"{self.error_count} errors")
        if self.warning_count:
            parts.append(f"{self.warning_count} warnings")
        return " | ".join(parts)


def validate_wrapper(
    slug: str,
    wrapper_path: Path,
    meta_path: Path | None = None,
    tools: list[dict[str, Any]] | None = None,
    execute_fn: Callable | None = None,
    service_name: str = "",
) -> WrapperHealth:
    """Full validation of a single wrapper: schema + runtime checks.

    Parameters
    ----------
    slug:
        Wrapper identifier (e.g. "clerk", "polarsh").
    wrapper_path:
        Path to the wrapper.py file.
    meta_path:
        Path to meta.json (optional).
    tools:
        The TOOLS list — if None, will be extracted from the module during
        runtime validation.
    execute_fn:
        The execute() function — if None, will be extracted from the module.
    service_name:
        Human-readable service name for reporting.
    """
    health = WrapperHealth(
        slug=slug,
        service_name=service_name or slug,
    )

    # ── Runtime validation ──
    runtime = validate_wrapper_runtime(slug, wrapper_path, meta_path)
    health.runtime_result = runtime

    if not runtime.healthy:
        health.healthy = False
        health.error_count += sum(1 for c in runtime.checks if not c.passed)
        health.warning_count += sum(
            1 for c in runtime.checks if c.passed and c.detail
        )
        logger.warning(
            "wrapper_runtime_validation_failed",
            slug=slug,
            checks=[
                {"check": c.check, "passed": c.passed, "detail": c.detail}
                for c in runtime.checks
                if not c.passed
            ],
        )

    # ── Schema validation (only if we have tools) ──
    if tools is not None:
        schema = validate_tool_definitions(slug, tools, execute_fn)
        health.schema_result = schema
        health.tools_registered = schema.tools_passed

        if not schema.valid:
            health.healthy = False

        health.error_count += sum(
            1 for i in schema.issues if i.severity == "error"
        )
        health.warning_count += sum(
            1 for i in schema.issues if i.severity == "warning"
        )

        if schema.issues:
            errors = [i for i in schema.issues if i.severity == "error"]
            warnings = [i for i in schema.issues if i.severity == "warning"]
            if errors:
                logger.warning(
                    "wrapper_schema_validation_errors",
                    slug=slug,
                    error_count=len(errors),
                    errors=[
                        {"tool": e.tool_name, "msg": e.message} for e in errors[:5]
                    ],
                )
            if warnings:
                logger.info(
                    "wrapper_schema_validation_warnings",
                    slug=slug,
                    warning_count=len(warnings),
                    warnings=[
                        {"tool": w.tool_name, "msg": w.message}
                        for w in warnings[:5]
                    ],
                )
    elif health.runtime_result:
        # Estimate tools from runtime check
        for check in runtime.checks:
            if check.check == "tools_export" and check.passed:
                # Extract count from detail like "35 tools defined"
                try:
                    health.tools_registered = int(check.detail.split()[0])
                except (ValueError, IndexError):
                    pass

    return health


# Public API
__all__ = [
    "validate_wrapper",
    "WrapperHealth",
    "SchemaValidationResult",
    "RuntimeValidationResult",
    "validate_tool_definitions",
    "validate_wrapper_runtime",
]
