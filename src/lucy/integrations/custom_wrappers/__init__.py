"""Auto-discovery, validation, and registration of custom API wrappers.

Scans custom_wrappers/<slug>/meta.json for saved wrappers, validates them
against schema and runtime checks, and converts their TOOLS lists into
OpenAI-format function definitions the agent loop can dispatch.

Wrappers are prefixed with ``lucy_custom_`` so they route through the
internal tool execution path, never hitting Composio.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()

_WRAPPERS_DIR = Path(__file__).parent

# ─── Wrapper Health Registry ────────────────────────────────────────────────
# Populated during load_custom_wrapper_tools() — maps slug → health status.
# Importable by other modules to check wrapper readiness at any time.

_wrapper_health: dict[str, dict[str, Any]] = {}


def get_wrapper_health() -> dict[str, dict[str, Any]]:
    """Return the health status of all loaded wrappers.

    Each entry contains:
    - ``healthy``: bool — whether the wrapper passed validation
    - ``service_name``: str
    - ``tool_count``: int — number of tools registered
    - ``errors``: list[str] — validation errors (empty if healthy)
    - ``warnings``: list[str] — non-fatal issues
    """
    return dict(_wrapper_health)


def get_healthy_wrappers() -> list[str]:
    """Return slugs of all wrappers that passed validation."""
    return [slug for slug, h in _wrapper_health.items() if h.get("healthy")]


# ─── Intent-Based Wrapper Detection ────────────────────────────────────────
# Maps conceptual intents to wrapper slugs. When a user asks about a concept,
# we match it to the wrapper that handles that domain — not just keyword
# matching on tool names.

_INTENT_MAP: dict[str, dict[str, Any]] = {
    "polarsh": {
        "service_name": "Polar.sh",
        "keywords": [
            "polar", "polarsh", "polar.sh",
        ],
        "intents": [
            # Revenue / billing domain
            "revenue", "sales", "mrr", "arr", "income", "earnings",
            "billing", "invoice", "invoices", "payment", "payments",
            # Subscription domain
            "subscription", "subscriptions", "subscriber", "subscribers",
            "recurring", "churn", "renewal", "renewals",
            # Product / pricing domain
            "product", "products", "pricing", "price", "prices",
            "discount", "discounts", "coupon", "coupons",
            # Customer domain
            "customer", "customers", "buyer", "buyers",
            # Order domain
            "order", "orders", "purchase", "purchases", "checkout",
            # Benefit domain
            "benefit", "benefits", "perk", "perks",
            # Metrics
            "metrics", "analytics dashboard", "polar dashboard",
        ],
    },
    "clerk": {
        "service_name": "Clerk",
        "keywords": [
            "clerk", "clerk.com", "clerkjs",
        ],
        "intents": [
            # User / auth domain
            "user", "users", "signup", "signups", "sign-up", "sign up",
            "authentication", "auth", "login", "logins", "log in",
            "registration", "registrations", "registered",
            # Session domain
            "session", "sessions", "active sessions", "logged in",
            # Organization domain
            "organization", "organizations", "org", "orgs", "team member",
            "membership", "memberships",
            # Identity domain
            "email address", "phone number", "identity", "profile",
            "ban user", "unban",
            # Domain / instance
            "domain", "domains", "instance settings", "webhook", "webhooks",
        ],
    },
}


def detect_relevant_wrappers(message: str) -> list[str]:
    """Detect which custom wrappers are relevant to a user message.

    Uses a two-tier approach:
    1. **Direct keyword match** — explicit mentions of service names
    2. **Intent-based match** — maps conceptual domains (revenue, users,
       subscriptions) to the wrapper that handles them

    Intent matching uses word-boundary-aware search to avoid false positives
    (e.g. "user" in "usual" won't match).

    Only returns wrappers that are currently loaded and healthy.

    Parameters
    ----------
    message:
        The user's message text.

    Returns
    -------
    List of wrapper slugs relevant to the message, ordered by confidence
    (direct keyword matches first).
    """
    if not message:
        return []

    msg_lower = message.lower()
    healthy_slugs = set(get_healthy_wrappers())

    # Also include unhealthy but loaded wrappers — they may still have tools
    loaded_slugs = set(_wrapper_health.keys())
    available = healthy_slugs | loaded_slugs

    direct_matches: list[str] = []
    intent_matches: dict[str, int] = {}  # slug → match count

    for slug, config in _INTENT_MAP.items():
        if slug not in available:
            continue

        # Tier 1: Direct keyword match (highest confidence)
        for kw in config.get("keywords", []):
            if kw in msg_lower:
                if slug not in direct_matches:
                    direct_matches.append(slug)
                break

        # Tier 2: Intent-based match (concept mapping)
        if slug not in direct_matches:
            match_count = 0
            for intent in config.get("intents", []):
                # Word-boundary match to avoid substring false positives
                pattern = r"\b" + re.escape(intent) + r"\b"
                if re.search(pattern, msg_lower):
                    match_count += 1

            if match_count > 0:
                intent_matches[slug] = match_count

    # Sort intent matches by number of matching intents (more = more relevant)
    sorted_intents = sorted(intent_matches.keys(), key=lambda s: -intent_matches[s])

    # Combine: direct matches first, then intent-based
    result = direct_matches[:]
    for slug in sorted_intents:
        if slug not in result:
            result.append(slug)

    if result:
        logger.debug(
            "wrapper_detection",
            direct=direct_matches,
            intent={s: intent_matches.get(s, 0) for s in sorted_intents},
            result=result,
        )

    return result


# ─── Wrapper Loading with Validation Gate ───────────────────────────────────


def load_custom_wrapper_tools() -> list[dict[str, Any]]:
    """Scan all custom wrapper directories, validate, and return tool defs.

    Each wrapper must have:
      - meta.json with at minimum ``slug`` and ``service_name``
      - wrapper.py with a ``TOOLS`` list of dicts and an ``execute()`` function

    After loading, each wrapper runs through the validation framework.
    Wrappers with schema errors are still registered (to avoid breaking
    existing functionality) but their health status is logged as unhealthy.

    Tool names are prefixed with ``lucy_custom_`` so the agent routes them
    to ``_execute_custom_wrapper_tool`` instead of Composio.
    """
    tool_defs: list[dict[str, Any]] = []
    _wrapper_health.clear()

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
        service_name = meta.get("service_name", slug)

        # ── Import wrapper module ──
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
            _wrapper_health[slug] = {
                "healthy": False,
                "service_name": service_name,
                "tool_count": 0,
                "errors": [f"Import failed: {exc}"],
                "warnings": [],
            }
            continue

        raw_tools: list[dict[str, Any]] = getattr(mod, "TOOLS", [])
        execute_fn = getattr(mod, "execute", None)
        has_execute = callable(execute_fn)

        if not raw_tools or not has_execute:
            logger.warning(
                "custom_wrapper_missing_interface",
                slug=slug,
                has_tools=bool(raw_tools),
                has_execute=has_execute,
            )
            _wrapper_health[slug] = {
                "healthy": False,
                "service_name": service_name,
                "tool_count": 0,
                "errors": [
                    f"Missing interface: TOOLS={'present' if raw_tools else 'missing'}, "
                    f"execute={'present' if has_execute else 'missing'}"
                ],
                "warnings": [],
            }
            continue

        # ── Validation gate ──
        validation_errors: list[str] = []
        validation_warnings: list[str] = []

        try:
            from lucy.integrations.validation import validate_wrapper, WrapperHealth

            health: WrapperHealth = validate_wrapper(
                slug=slug,
                wrapper_path=wrapper_path,
                meta_path=meta_path,
                tools=raw_tools,
                execute_fn=execute_fn,
                service_name=service_name,
            )

            if health.schema_result:
                for issue in health.schema_result.issues:
                    if issue.severity == "error":
                        validation_errors.append(f"[{issue.tool_name}] {issue.message}")
                    else:
                        validation_warnings.append(f"[{issue.tool_name}] {issue.message}")

            if health.runtime_result:
                for check in health.runtime_result.checks:
                    if not check.passed:
                        validation_errors.append(f"[runtime:{check.check}] {check.detail}")

            if validation_errors:
                logger.warning(
                    "custom_wrapper_validation_issues",
                    slug=slug,
                    service=service_name,
                    error_count=len(validation_errors),
                    warning_count=len(validation_warnings),
                    errors=validation_errors[:5],
                )
            elif validation_warnings:
                logger.info(
                    "custom_wrapper_validation_warnings",
                    slug=slug,
                    service=service_name,
                    warning_count=len(validation_warnings),
                    warnings=validation_warnings[:5],
                )

        except Exception as exc:
            # Validation framework itself failed — log but don't block
            logger.debug(
                "custom_wrapper_validation_skipped",
                slug=slug,
                error=str(exc),
            )

        # ── Register tools (even if validation found warnings) ──
        registered_names: list[str] = []
        for tool in raw_tools:
            if not isinstance(tool, dict):
                continue
            original_name = tool.get("name", "")
            if not original_name:
                continue

            prefixed_name = f"lucy_custom_{original_name}"
            raw_desc = tool.get("description", f"{original_name} from {service_name}")
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

            # Register action_type annotations from wrapper TOOLS
            try:
                from lucy.core.action_classifier import register_overrides_from_wrapper
                register_overrides_from_wrapper(slug, raw_tools)
            except Exception as exc:
                logger.debug(
                    "action_classifier_annotation_load_skipped",
                    slug=slug,
                    error=str(exc),
                )

            logger.info(
                "custom_wrapper_loaded",
                slug=slug,
                service=service_name,
                tool_count=len(registered_names),
                tools=registered_names,
                healthy=not bool(validation_errors),
            )

        # ── Update health registry ──
        _wrapper_health[slug] = {
            "healthy": not bool(validation_errors),
            "service_name": service_name,
            "tool_count": len(registered_names),
            "errors": validation_errors,
            "warnings": validation_warnings,
        }

    # ── Summary log ──
    if _wrapper_health:
        healthy_count = sum(1 for h in _wrapper_health.values() if h["healthy"])
        total = len(_wrapper_health)
        total_tools = sum(h["tool_count"] for h in _wrapper_health.values())
        logger.info(
            "custom_wrappers_loaded",
            total_wrappers=total,
            healthy=healthy_count,
            unhealthy=total - healthy_count,
            total_tools=total_tools,
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
