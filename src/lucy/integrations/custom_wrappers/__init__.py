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


def load_custom_wrapper_tools(relevant_slugs: list[str] | None = None) -> list[dict[str, Any]]:
    """Scan all custom wrapper directories, validate, and return tool defs.

    Each wrapper must have:
      - meta.json with at minimum ``slug`` and ``service_name``
      - wrapper.py with a ``TOOLS`` list of dicts and an ``execute()`` function

    After loading, each wrapper runs through the validation framework.
    Wrappers with schema errors are still registered (to avoid breaking
    existing functionality) but their health status is logged as unhealthy.

    Tool names are prefixed with ``lucy_custom_`` so the agent routes them
    to ``_execute_custom_wrapper_tool`` instead of Composio.

    Args:
        relevant_slugs: If provided, only load wrappers whose slug is in this
            list. Pass None to load all wrappers (legacy behavior).
    """
    tool_defs: list[dict[str, Any]] = []
    _wrapper_health.clear()

    if not _WRAPPERS_DIR.exists():
        return tool_defs

    for meta_path in _WRAPPERS_DIR.glob("*/meta.json"):
        # Filter by relevant_slugs if provided
        if relevant_slugs is not None:
            dir_slug = meta_path.parent.name
            if dir_slug not in relevant_slugs:
                continue
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


def detect_relevant_wrappers(
    message: str,
    allow_load_all: bool = True,
    strict_mode: bool = False,
) -> list[str] | None:
    """Detect which custom wrappers are relevant to the user's message.

    Returns a list of wrapper slugs that should be loaded, or None if
    ALL wrappers should be loaded (when the message is ambiguous or
    explicitly mentions "integrations"/"tools"/"what can you do").

    If *allow_load_all* is False (used for cron executions), the
    broad "load everything" shortcut is skipped — only wrappers whose
    keywords appear in the message are returned.

    If *strict_mode* is True (used for cron executions), only match on
    service names (clerk, polar), not generic keywords like "user" or
    "product" which appear in boilerplate cron instructions.

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
        # Filter by relevant_slugs if provided
        if relevant_slugs is not None:
            dir_slug = meta_path.parent.name
            if dir_slug not in relevant_slugs:
                continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        slug = meta.get("slug", meta_path.parent.name)
        service = meta.get("service_name", slug).lower()
        # Use slug and service name as keywords
        _WRAPPER_TRIGGERS[slug] = [slug, service, service.replace(".", "")]

    # In strict mode (crons), only match on service names, not generic keywords.
    # This prevents cron boilerplate like "fetch data for users" from loading
    # all 66 wrapper tools when the cron task doesn't actually need them.
    if strict_mode:
        _EXTRA_KEYWORDS: dict[str, list[str]] = {
            "clerk": ["clerk"],
            "polarsh": ["polar", "polar.sh"],
        }
        relevant: list[str] = []
        for slug, keywords in _WRAPPER_TRIGGERS.items():
            all_keywords = keywords + _EXTRA_KEYWORDS.get(slug, [])
            for kw in all_keywords:
                if kw in msg:
                    relevant.append(slug)
                    break
        return relevant if relevant else []

    # --- Context-aware keyword matching ---
    # Tier 1: Strong signals (always trigger) - explicit service mentions
    _STRONG_KEYWORDS: dict[str, list[str]] = {
        "clerk": ["clerk", "authentication", "sign up", "signup", "login"],
        "polarsh": ["polar", "polar.sh", "subscription", "checkout",
                     "billing", "payment", "benefit", "discount"],
        "googlecalendar": ["calendar", "meeting", "meetings", "schedule",
                           "event", "events", "free time", "free slot",
                           "am i free", "are you free", "i free",
                           "busy", "appointment", "block time"],
        "gmail": ["email", "emails", "gmail", "inbox", "unread",
                  "mail", "draft", "send email", "send a email",
                  "send an email", "compose email", "reply to email",
                  "email thread", "latest emails", "recent emails",
                  "new emails", "read email", "check email",
                  "check my email", "check my inbox"],
    }

    # Tier 2: Ambiguous keywords that need data-action context
    # These only trigger when combined with action verbs or data patterns
    _CONTEXT_KEYWORDS: dict[str, list[str]] = {
        "clerk": ["user", "users", "session", "organization"],
        "polarsh": ["product", "products", "order", "orders", "customer", "customers"],
    }

    # Negative patterns: these phrases contain tier-2 keywords but
    # are NOT about the service (e.g. "product update" = announcement)
    _NEGATIVE_PHRASES: set[str] = {
        "product update", "product announcement", "product launch",
        "product roadmap", "product news", "product email",
        "product strategy", "product review", "product feedback",
        "product demo", "product brief", "product vision",
        "user experience", "user interface", "user story",
        "user journey", "user research", "user persona",
        "user flow", "user guide", "user manual",
        "user friendly", "user-friendly",
        "session notes", "session summary", "session agenda",
        "order of", "in order to", "order to",
        "customer success", "customer support story",
    }

    # Data-action patterns that confirm a tier-2 keyword is about data
    _DATA_ACTION_RE = re.compile(
        r"\b(?:list|get|fetch|show|count|how many|total|check|find|"
        r"look up|lookup|search|query|pull|retrieve|export|import|"
        r"create|add|update|edit|delete|remove|ban|unban|revoke|"
        r"active|inactive|recent|new|all|our|the|my)\b",
        re.IGNORECASE,
    )

    # Writing/composition mode: if the primary intent is writing content
    # (not querying data), context keywords should NOT trigger wrappers.
    # E.g., "write a product update mentioning 3000 users" is a writing task.
    _COMPOSITION_RE = re.compile(
        r"\b(?:write|draft|compose|summarize|create\s+(?:a|an|the)\s+"
        r"(?:update|announcement|email|message|report|brief|summary|"
        r"newsletter|post|blog|note|memo|document))\b",
        re.IGNORECASE,
    )

    relevant: list[str] = []

    # Check strong keywords first (service names + unambiguous terms)
    for slug in set(list(_WRAPPER_TRIGGERS) + list(_STRONG_KEYWORDS)):
        if slug in relevant:
            continue
        all_strong = _WRAPPER_TRIGGERS.get(slug, []) + _STRONG_KEYWORDS.get(slug, [])
        for kw in all_strong:
            if kw in msg and slug not in relevant:
                relevant.append(slug)
                break

    # Check context keywords (only if not already matched)
    # Skip context keywords entirely if message is a composition task
    is_composition = bool(_COMPOSITION_RE.search(msg))
    if not is_composition:
        for slug, keywords in _CONTEXT_KEYWORDS.items():
            if slug in relevant:
                continue
            for kw in keywords:
                if kw not in msg:
                    continue
                # Check negative phrases first
                is_negative = False
                for neg in _NEGATIVE_PHRASES:
                    if neg in msg:
                        # The keyword appears only in a negative context
                        # Check if the keyword also appears outside the negative phrase
                        remaining = msg.replace(neg, "")
                        if kw not in remaining:
                            is_negative = True
                            break
                if is_negative:
                    continue
                # Require a data-action verb nearby to confirm data context
                if _DATA_ACTION_RE.search(msg):
                    if slug not in relevant:
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
