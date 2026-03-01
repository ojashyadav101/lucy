"""Auto-discovery, validation, and registration of custom API wrappers.

Scans custom_wrappers/<slug>/meta.json for saved wrappers, validates them
against schema and runtime checks, and converts their TOOLS lists into
OpenAI-format function definitions the agent loop can dispatch.

Wrappers are prefixed with ``lucy_custom_`` so they route through the
internal tool execution path, never hitting Composio.

V2 improvements:
- Cached module imports (no re-import on every call)
- Structured error responses with error codes
- Retry with exponential backoff for transient failures
- Rate limiting awareness per wrapper
- Timeout protection on execute calls
- Fallback behavior when wrapper is unhealthy
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import re
import time
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()

_WRAPPERS_DIR = Path(__file__).parent

# ═══════════════════════════════════════════════════════════════════════════
# MODULE CACHE — avoid re-importing on every tool call
# ═══════════════════════════════════════════════════════════════════════════

_module_cache: dict[str, Any] = {}  # slug → loaded module
_MODULE_CACHE_MAX = 20


def _get_cached_module(slug: str) -> Any | None:
    """Get a cached wrapper module, or None if not cached."""
    return _module_cache.get(slug)


def _cache_module(slug: str, mod: Any) -> None:
    """Cache a loaded wrapper module."""
    if len(_module_cache) >= _MODULE_CACHE_MAX:
        # Evict oldest (simple FIFO)
        oldest = next(iter(_module_cache))
        del _module_cache[oldest]
    _module_cache[slug] = mod


# ═══════════════════════════════════════════════════════════════════════════
# RATE LIMITING — per-wrapper tracking
# ═══════════════════════════════════════════════════════════════════════════

_rate_limit_state: dict[str, dict[str, Any]] = {}
# slug → {"blocked_until": float, "consecutive_429s": int, "last_call": float}

_RATE_LIMIT_BACKOFF_BASE = 2.0  # seconds
_RATE_LIMIT_MAX_BACKOFF = 60.0  # seconds
_MIN_CALL_INTERVAL = 0.5  # seconds between calls to same wrapper


def _check_rate_limit(slug: str) -> str | None:
    """Check if a wrapper is currently rate-limited.

    Returns error message if rate-limited, None if OK.
    """
    state = _rate_limit_state.get(slug)
    if not state:
        return None

    now = time.monotonic()

    # Check hard block from 429 responses
    blocked_until = state.get("blocked_until", 0)
    if now < blocked_until:
        wait_secs = round(blocked_until - now, 1)
        return (
            f"Rate limited by {slug} API. "
            f"Retrying in {wait_secs}s. "
            f"Try a different approach or wait a moment."
        )

    # Check minimum interval between calls
    last_call = state.get("last_call", 0)
    if now - last_call < _MIN_CALL_INTERVAL:
        return None  # Don't block, just note it

    return None


def _record_rate_limit(slug: str) -> None:
    """Record a rate limit (429) response from a wrapper."""
    state = _rate_limit_state.setdefault(slug, {
        "blocked_until": 0, "consecutive_429s": 0, "last_call": 0,
    })
    state["consecutive_429s"] = state.get("consecutive_429s", 0) + 1
    backoff = min(
        _RATE_LIMIT_BACKOFF_BASE * (2 ** (state["consecutive_429s"] - 1)),
        _RATE_LIMIT_MAX_BACKOFF,
    )
    state["blocked_until"] = time.monotonic() + backoff
    logger.warning(
        "custom_wrapper_rate_limited",
        slug=slug,
        consecutive_429s=state["consecutive_429s"],
        backoff_secs=backoff,
    )


def _record_success(slug: str) -> None:
    """Record a successful call, resetting rate limit state."""
    state = _rate_limit_state.get(slug)
    if state:
        state["consecutive_429s"] = 0
        state["blocked_until"] = 0
    _rate_limit_state.setdefault(slug, {})["last_call"] = time.monotonic()


# ═══════════════════════════════════════════════════════════════════════════
# ERROR HANDLING — structured error responses
# ═══════════════════════════════════════════════════════════════════════════

class ToolError:
    """Structured error codes for tool failures."""
    WRAPPER_NOT_FOUND = "WRAPPER_NOT_FOUND"
    IMPORT_FAILED = "IMPORT_FAILED"
    NO_EXECUTE_FN = "NO_EXECUTE_FUNCTION"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    TIMEOUT = "EXECUTION_TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    UNHEALTHY = "WRAPPER_UNHEALTHY"
    AUTH_FAILED = "AUTH_FAILED"


def _make_error(
    code: str,
    message: str,
    slug: str = "",
    suggestion: str = "",
) -> dict[str, Any]:
    """Create a structured error response.

    Provides both a machine-readable error_code and a human-friendly
    message that the agent can relay to the user (after abstraction).
    """
    result: dict[str, Any] = {
        "error": message,
        "error_code": code,
        "success": False,
    }
    if suggestion:
        result["suggestion"] = suggestion
    if slug:
        result["wrapper"] = slug

    logger.warning(
        "custom_tool_error",
        error_code=code,
        slug=slug,
        message=message[:200],
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════
# WRAPPER HEALTH REGISTRY
# ═══════════════════════════════════════════════════════════════════════════

_wrapper_health: dict[str, dict[str, Any]] = {}


def get_wrapper_health() -> dict[str, dict[str, Any]]:
    """Return the health status of all loaded wrappers.

    Each entry contains:
    - ``healthy``: bool
    - ``service_name``: str
    - ``tool_count``: int
    - ``errors``: list[str]
    - ``warnings``: list[str]
    """
    return dict(_wrapper_health)


def get_healthy_wrappers() -> list[str]:
    """Return slugs of all wrappers that passed validation."""
    return [slug for slug, h in _wrapper_health.items() if h.get("healthy")]


# ═══════════════════════════════════════════════════════════════════════════
# TOOL LOADING
# ═══════════════════════════════════════════════════════════════════════════

def load_custom_wrapper_tools(relevant_slugs: list[str] | None = None, message: str = "") -> list[dict[str, Any]]:
    """Scan all custom wrapper directories, validate, and return tool defs.

    Each wrapper must have:
      - meta.json with at minimum ``slug`` and ``service_name``
      - wrapper.py with a ``TOOLS`` list of dicts and an ``execute()`` function

    After loading, each wrapper runs through the validation framework.
    Wrappers with schema errors are still registered (to avoid breaking
    existing functionality) but their health status is logged as unhealthy.

    Tool names are prefixed with ``lucy_custom_`` so the agent routes them
    to ``execute_custom_tool`` instead of Composio.

    Args:
        relevant_slugs: If provided, only load wrappers whose slug is in this
            list. Pass None to load all wrappers (legacy behavior).
        message: Current user message for tiered loading decisions.
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

        # ── Import wrapper module (with caching) ──
        mod = _get_cached_module(slug)
        if mod is None:
            try:
                spec = importlib.util.spec_from_file_location(
                    f"custom_wrapper_{slug}", str(wrapper_path),
                )
                if not spec or not spec.loader:
                    continue
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                _cache_module(slug, mod)
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
        # Tiered loading: some wrappers have TOOLS_ADVANCED for rarely-used tools.
        advanced_tools: list[dict[str, Any]] = getattr(mod, "TOOLS_ADVANCED", [])
        if advanced_tools:
            _msg = (message or "").lower()
            _ADVANCED_KW = {"webhook", "benefit", "benefit_grant", "manage endpoint"}
            if any(kw in _msg for kw in _ADVANCED_KW):
                raw_tools = list(raw_tools) + list(advanced_tools)
                logger.info(
                    "custom_wrapper_advanced_tools_loaded",
                    slug=slug,
                    advanced_count=len(advanced_tools),
                )
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


# ═══════════════════════════════════════════════════════════════════════════
# INTENT-BASED WRAPPER DETECTION (unchanged from original)
# ═══════════════════════════════════════════════════════════════════════════

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
    """
    import re

    msg = message.lower()

    # If the user is asking about tools/integrations broadly, load everything
    if allow_load_all:
        _LOAD_ALL = re.compile(
            r"(?:"
            r"(?:my|your|our|connected|available|installed|enabled)\s+(?:integrations?|tools?|services?|apps?)"
            r"|what\s+(?:integrations?|tools?|services?|apps?)\s+(?:do|can|are)"
            r"|what\s+can\s+you\s+(?:do|connect|integrate)"
            r"|capabilities"
            r"|(?:list|show|manage)\s+(?:integrations?|tools?|services?)"
            r")",
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
        _WRAPPER_TRIGGERS[slug] = [slug, service, service.replace(".", "")]

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
    _STRONG_KEYWORDS: dict[str, list[str]] = {
        "clerk": ["clerk", "authentication", "sign up", "signup", "login"],
        "polarsh": ["polar", "polar.sh", "subscription", "subscriptions",
                     "subscriber", "subscribers", "checkout",
                     "billing", "payment", "benefit", "discount",
                     "revenue", "mrr", "arr", "earnings"],
        "googlecalendar": ["calendar", "meeting", "meetings", "schedule",
                           "event", "events", "free time", "free slot",
                           "am i free", "are you free", "i free",
                           "busy", "appointment", "block time",
                           "tomorrow", "next week", "this week",
                           "today's schedule", "my schedule"],
        "gmail": ["email", "emails", "gmail", "inbox", "unread",
                  "mail", "draft", "send email", "send a email",
                  "send an email", "compose email", "reply to email",
                  "email thread", "latest emails", "recent emails",
                  "new emails", "read email", "check email",
                  "check my email", "check my inbox"],
    }

    _CALENDAR_TIME_RE = re.compile(
        r"\b(?:at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)|"
        r"\d{1,2}\s*(?:am|pm)|"
        r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b)",
        re.IGNORECASE,
    )

    _CONTEXT_KEYWORDS: dict[str, list[str]] = {
        "clerk": ["user", "users", "session", "organization"],
        "polarsh": ["product", "products", "order", "orders", "customer", "customers"],
    }

    _NEGATIVE_PHRASES: set[str] = {
        "product update", "product announcement", "product launch",
        "product roadmap", "product news", "product email",
        "product strategy", "product review", "product feedback",
        "product demo", "product brief", "product vision",
        "product page", "product market", "product hunt",
        "user experience", "user interface", "user story",
        "user journey", "user research", "user persona",
        "user flow", "user guide", "user manual",
        "user friendly", "user-friendly", "user reported",
        "user feedback", "user testing", "user engagement",
        "session notes", "session summary", "session agenda",
        "session recording", "brainstorming session",
        "order of", "in order to", "order to",
        "customer success", "customer support story",
        "customer journey", "customer feedback",
    }

    _DATA_ACTION_RE = re.compile(
        r"\b(?:list|get|fetch|show|count|how many|total|check|find|"
        r"look up|lookup|search|query|pull|retrieve|export|import|"
        r"create|add|update|edit|delete|remove|ban|unban|revoke|"
        r"active|inactive|recent|new|all|our|the|my)\b",
        re.IGNORECASE,
    )

    _COMPOSITION_RE = re.compile(
        r"\b(?:write|draft|compose|summarize|create\s+(?:a|an|the)\s+"
        r"(?:update|announcement|email|message|report|brief|summary|"
        r"newsletter|post|blog|note|memo|document))\b",
        re.IGNORECASE,
    )

    relevant: list[str] = []

    for slug in set(list(_WRAPPER_TRIGGERS) + list(_STRONG_KEYWORDS)):
        if slug in relevant:
            continue
        all_strong = _WRAPPER_TRIGGERS.get(slug, []) + _STRONG_KEYWORDS.get(slug, [])
        for kw in all_strong:
            if kw in msg and slug not in relevant:
                relevant.append(slug)
                break

    if "googlecalendar" not in relevant and _CALENDAR_TIME_RE.search(msg):
        relevant.append("googlecalendar")

    is_composition = bool(_COMPOSITION_RE.search(msg))
    if not is_composition:
        for slug, keywords in _CONTEXT_KEYWORDS.items():
            if slug in relevant:
                continue
            for kw in keywords:
                if kw not in msg:
                    continue
                is_negative = False
                for neg in _NEGATIVE_PHRASES:
                    if neg in msg:
                        remaining = msg.replace(neg, "")
                        if kw not in remaining:
                            is_negative = True
                            break
                if is_negative:
                    continue
                if _DATA_ACTION_RE.search(msg):
                    if slug not in relevant:
                        relevant.append(slug)
                    break

    return relevant if relevant else []


# ═══════════════════════════════════════════════════════════════════════════
# TOOL EXECUTION — V2 with retry, timeout, rate limiting, and caching
# ═══════════════════════════════════════════════════════════════════════════

_EXECUTE_TIMEOUT = 30.0  # Max seconds for a single tool call
_MAX_RETRIES = 2  # Max retries for transient failures
_RETRY_DELAY_BASE = 1.0  # Base delay between retries (exponential backoff)


def execute_custom_tool(
    tool_name: str,
    parameters: dict[str, Any],
    api_key: str,
) -> Any:
    """Execute a custom wrapper tool by name.

    V2 improvements over original:
    - Cached module imports (no re-import per call)
    - Rate limit checking before execution
    - Timeout protection on execute calls
    - Retry with exponential backoff for transient failures
    - Structured error responses with error codes
    - Proper async/sync detection and handling
    - Fallback behavior when wrapper is unhealthy

    ``tool_name`` arrives with the ``lucy_custom_`` prefix stripped already
    (e.g. ``polarsh_list_products``). The slug is the part before the first
    underscore-separated action verb.
    """
    parts = tool_name.split("_", 1)
    slug = parts[0] if parts else tool_name

    # ── Check 1: Wrapper exists ──
    wrapper_path = _WRAPPERS_DIR / slug / "wrapper.py"
    if not wrapper_path.exists():
        return _make_error(
            ToolError.WRAPPER_NOT_FOUND,
            f"Integration '{slug}' is not installed.",
            slug=slug,
            suggestion=(
                "Check available integrations or set up a custom connection. "
                "The integration may need to be configured first."
            ),
        )

    # ── Check 2: Wrapper health ──
    health = _wrapper_health.get(slug, {})
    if health and not health.get("healthy", True):
        errors = health.get("errors", [])
        error_summary = errors[0] if errors else "Validation failed"
        return _make_error(
            ToolError.UNHEALTHY,
            f"Integration '{slug}' has configuration issues: {error_summary}",
            slug=slug,
            suggestion=(
                "The integration may need to be reconfigured. "
                "Check the API key and connection settings."
            ),
        )

    # ── Check 3: Rate limiting ──
    rate_error = _check_rate_limit(slug)
    if rate_error:
        return _make_error(
            ToolError.RATE_LIMITED,
            rate_error,
            slug=slug,
            suggestion="Wait a moment before retrying, or try a different approach.",
        )

    # ── Load module (cached) ──
    mod = _get_cached_module(slug)
    if mod is None:
        try:
            spec = importlib.util.spec_from_file_location(
                f"custom_wrapper_{slug}", str(wrapper_path),
            )
            if not spec or not spec.loader:
                return _make_error(
                    ToolError.IMPORT_FAILED,
                    f"Cannot load integration '{slug}'.",
                    slug=slug,
                )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _cache_module(slug, mod)
        except Exception as exc:
            return _make_error(
                ToolError.IMPORT_FAILED,
                f"Integration '{slug}' failed to load: {str(exc)[:200]}",
                slug=slug,
                suggestion="The integration code may have errors. Try reconnecting.",
            )

    execute_fn = getattr(mod, "execute", None)
    if not callable(execute_fn):
        return _make_error(
            ToolError.NO_EXECUTE_FN,
            f"Integration '{slug}' is missing its execute function.",
            slug=slug,
            suggestion="The integration may need to be rebuilt.",
        )

    # ── Execute with retry and timeout ──
    import asyncio

    is_async = asyncio.iscoroutinefunction(execute_fn)

    last_error: str = ""
    for attempt in range(1, _MAX_RETRIES + 2):  # 1-indexed, +1 for initial try
        try:
            if is_async:
                # Run async function with timeout
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # Already in async context — return the coroutine
                    # The caller (agent loop) will await it
                    coro = execute_fn(tool_name, parameters, api_key)
                    # Wrap with timeout
                    return asyncio.ensure_future(
                        _execute_with_timeout(coro, slug, _EXECUTE_TIMEOUT)
                    )
                else:
                    result = asyncio.run(
                        _execute_with_timeout(
                            execute_fn(tool_name, parameters, api_key),
                            slug,
                            _EXECUTE_TIMEOUT,
                        )
                    )
            else:
                result = execute_fn(tool_name, parameters, api_key)

            # Check if result indicates a rate limit
            if isinstance(result, dict):
                error_text = str(result.get("error", "")).lower()
                status_code = result.get("status_code", result.get("status", 0))

                if status_code == 429 or "rate limit" in error_text or "too many requests" in error_text:
                    _record_rate_limit(slug)
                    if attempt <= _MAX_RETRIES:
                        delay = _RETRY_DELAY_BASE * (2 ** (attempt - 1))
                        logger.info(
                            "custom_tool_retry_rate_limit",
                            slug=slug,
                            attempt=attempt,
                            delay=delay,
                        )
                        time.sleep(delay)
                        continue
                    return _make_error(
                        ToolError.RATE_LIMITED,
                        f"Rate limited after {attempt} attempts. Try again in a moment.",
                        slug=slug,
                    )

                # Check for auth errors (don't retry these)
                if status_code == 401 or status_code == 403 or "unauthorized" in error_text or "forbidden" in error_text:
                    return _make_error(
                        ToolError.AUTH_FAILED,
                        f"Authentication failed for '{slug}'. The API key may be invalid or expired.",
                        slug=slug,
                        suggestion="Update the API key and try again.",
                    )

                # Check for transient server errors (retry these)
                if status_code in (500, 502, 503, 504) and attempt <= _MAX_RETRIES:
                    delay = _RETRY_DELAY_BASE * (2 ** (attempt - 1))
                    logger.info(
                        "custom_tool_retry_server_error",
                        slug=slug,
                        attempt=attempt,
                        status_code=status_code,
                        delay=delay,
                    )
                    time.sleep(delay)
                    last_error = result.get("error", f"Server error {status_code}")
                    continue

            # Success
            _record_success(slug)
            return result

        except asyncio.TimeoutError:
            if attempt <= _MAX_RETRIES:
                logger.warning(
                    "custom_tool_timeout_retry",
                    slug=slug,
                    attempt=attempt,
                    timeout=_EXECUTE_TIMEOUT,
                )
                continue
            return _make_error(
                ToolError.TIMEOUT,
                f"Request to '{slug}' timed out after {_EXECUTE_TIMEOUT}s.",
                slug=slug,
                suggestion="The service may be slow. Try again or use an alternative approach.",
            )

        except Exception as exc:
            last_error = str(exc)
            if attempt <= _MAX_RETRIES and _is_transient_error(exc):
                delay = _RETRY_DELAY_BASE * (2 ** (attempt - 1))
                logger.warning(
                    "custom_tool_retry_exception",
                    slug=slug,
                    attempt=attempt,
                    error=str(exc)[:100],
                    delay=delay,
                )
                time.sleep(delay)
                continue

            return _make_error(
                ToolError.EXECUTION_FAILED,
                f"Tool '{tool_name}' failed: {str(exc)[:200]}",
                slug=slug,
                suggestion="Try the request again or use an alternative approach.",
            )

    # All retries exhausted
    return _make_error(
        ToolError.EXECUTION_FAILED,
        f"Tool '{tool_name}' failed after {_MAX_RETRIES + 1} attempts: {last_error[:200]}",
        slug=slug,
        suggestion="The service may be experiencing issues. Try again later.",
    )


async def _execute_with_timeout(
    coro: Any,
    slug: str,
    timeout: float,
) -> Any:
    """Execute an async function with timeout protection."""
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(
            "custom_tool_execution_timeout",
            slug=slug,
            timeout=timeout,
        )
        raise


def _is_transient_error(exc: Exception) -> bool:
    """Check if an exception is likely transient (worth retrying)."""
    error_text = str(exc).lower()
    transient_indicators = [
        "timeout", "timed out",
        "connection reset", "connection refused", "connection error",
        "temporarily unavailable", "service unavailable",
        "500", "502", "503", "504",
        "network", "dns",
        "broken pipe",
    ]
    return any(indicator in error_text for indicator in transient_indicators)
