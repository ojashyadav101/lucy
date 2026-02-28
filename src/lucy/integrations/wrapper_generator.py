"""Custom Python wrapper generator (Stage 3).

When neither MCP nor OpenAPI specs are available, generates a
comprehensive Python API wrapper for a service, saves it to disk,
and registers it as a Composio custom tool so Lucy can call it
directly.

Key difference from v1: uses the endpoint inventory from Phase 2
(grounded_search.discover_endpoints) to generate ALL tools, not a
capped subset.

Wrappers are saved in src/lucy/integrations/custom_wrappers/<slug>/
and auto-loaded on startup.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import structlog

from lucy.config import LLMPresets, settings
from lucy.integrations.grounded_search import IntegrationClassification

logger = structlog.get_logger()

_WRAPPERS_DIR = Path(__file__).parent / "custom_wrappers"

_GENERATOR_MODEL = settings.model_tier_fast

_GENERATOR_PROMPT = """\
You are an expert Python developer. Generate a production-ready Python wrapper
module for the "{service_name}" API that an AI assistant can use to help users.

Service info:
- API Base URL: {api_base_url}
- API docs: {api_docs_url}
- SDK package (if any): {sdk_package}
- Auth method: {auth_method}
- Auth header format: {auth_header_format}

{endpoint_inventory}

Requirements:
1. Use `httpx` for HTTP calls (it is installed). Always set `follow_redirects=True`.
2. The wrapper must be a single Python file that defines:
   a) A TOOLS list — each entry is a dict with:
      - "name": str (e.g. "{slug}_list_products")
      - "description": str (clear, helpful description for an AI agent)
      - "parameters": dict (JSON Schema for the function's input, including
        required fields and property descriptions)
   b) An `async def execute(tool_name: str, args: dict, api_key: str) -> dict`
      function that dispatches to the right API call based on tool_name.
3. Auth credentials are passed as the `api_key` parameter to `execute()`.
   Use the auth header format specified above.
4. Handle errors gracefully and return {{"error": "message"}} on failure.
5. CRITICAL: Cover the full business lifecycle. Include tools for:
   - ALL CRUD operations on primary resources (products, subscriptions, etc.)
   - Checkout / purchase flows (if available)
   - Customer / user management
   - Orders, invoices, billing, and financial data
   - Search and list with pagination (accept `page` and `limit` params)
   - Any analytics or metrics endpoints
   - Webhook management if available
   Aim for 15-30 tools covering every CATEGORY. Skip internal/admin-only
   endpoints like OAuth2 internals, raw token management, etc.
6. Include a module docstring explaining what the wrapper does.
7. Do NOT import anything beyond httpx, json, and the standard library.
8. Use descriptive tool names with the service prefix: {slug}_<action>_<resource>.
9. Keep each function implementation concise. Share a common `_make_request`
   helper to avoid code duplication.
10. Keep the total code under 600 lines. Be efficient.

Categories to cover (based on endpoint inventory):
{category_summary}

Return ONLY the Python code. No markdown fences. No explanation.
"""

_GENERATOR_PROMPT_MINIMAL = """\
You are an expert Python developer. Generate a production-ready Python wrapper
module for the "{service_name}" API that an AI assistant can use to help users.

Service info:
- API docs: {api_docs_url}
- SDK package (if any): {sdk_package}
- Auth method: {auth_method}
- API Base URL: {api_base_url}

Since I could not fetch the full API documentation, please use your knowledge of
{service_name} to create a wrapper covering ALL major business categories:

- CRUD for all primary resources
- List/search with filters and pagination
- Checkout / purchase / payment flows
- Customer/user management
- Orders, invoices, billing
- Analytics or metrics if available
- Webhooks management if available

Requirements:
1. Use `httpx` for HTTP calls (it is installed). Always set `follow_redirects=True`.
2. The wrapper must be a single Python file that defines:
   a) A TOOLS list — each entry is a dict with:
      - "name": str (e.g. "{slug}_list_products")
      - "description": str (clear, helpful description for an AI agent)
      - "parameters": dict (JSON Schema for the function's input)
   b) An `async def execute(tool_name: str, args: dict, api_key: str) -> dict`
      function that dispatches to the right API call based on tool_name.
3. Auth credentials are passed as the `api_key` parameter to `execute()`.
4. Handle errors gracefully and return {{"error": "message"}} on failure.
5. Aim for 15-30 tools covering every business CATEGORY. Skip internal/admin-only
   endpoints. Cover the full lifecycle from creating to selling to managing.
6. Include a module docstring explaining what the wrapper does.
7. Do NOT import anything beyond httpx, json, and the standard library.
8. Use descriptive tool names with the service prefix: {slug}_<action>_<resource>.
9. Share a common `_make_request` helper. Keep total code under 600 lines.

Return ONLY the Python code. No markdown fences. No explanation.
"""


@dataclass
class WrapperDeployResult:
    """Outcome of a wrapper generation and deployment attempt."""

    success: bool = False
    service_name: str = ""
    wrapper_path: str | None = None
    tools_registered: list[str] | None = None
    error: str | None = None
    needs_api_key: bool = False
    api_key_env_var: str | None = None


async def generate_and_deploy_wrapper(
    classification: IntegrationClassification,
) -> WrapperDeployResult:
    """Generate a Python wrapper and save it to disk.

    Steps:
      1. Use Gemini to generate wrapper code, feeding it the full endpoint
         inventory when available.
      2. Save to custom_wrappers/<slug>/wrapper.py.
      3. Validate the module can be imported and has tools.
    """
    t0 = time.monotonic()
    service = classification.service_name
    slug = service.lower().replace(" ", "").replace("_", "").replace(".", "").replace("-", "")
    env_var = f"{slug.upper()}_API_KEY"

    wrapper_dir = _WRAPPERS_DIR / slug
    wrapper_dir.mkdir(parents=True, exist_ok=True)
    wrapper_file = wrapper_dir / "wrapper.py"
    init_file = wrapper_dir / "__init__.py"
    if not init_file.exists():
        init_file.write_text("", encoding="utf-8")

    max_attempts = 2
    last_error = ""
    for attempt in range(1, max_attempts + 1):
        wrapper_code = await _generate_wrapper_code(classification, slug, env_var)
        if not wrapper_code:
            return WrapperDeployResult(
                service_name=service,
                error="Failed to generate wrapper code via LLM",
            )

        try:
            wrapper_file.write_text(wrapper_code, encoding="utf-8")

            py_spec = importlib.util.spec_from_file_location(
                f"custom_wrapper_{slug}", str(wrapper_file)
            )
            if py_spec and py_spec.loader:
                mod = importlib.util.module_from_spec(py_spec)
                py_spec.loader.exec_module(mod)

                tools_list = getattr(mod, "TOOLS", [])
                tool_names = [
                    t.get("name", "?") for t in tools_list if isinstance(t, dict)
                ]

                if not tools_list:
                    logger.warning("wrapper_no_tools_defined", service=service)
                    return WrapperDeployResult(
                        service_name=service,
                        error="Generated wrapper defines no TOOLS list",
                    )

                meta = {
                    "service_name": service,
                    "slug": slug,
                    "auth_method": classification.auth_method,
                    "env_var": env_var,
                    "tools": tool_names,
                    "total_tools": len(tool_names),
                    "api_docs": classification.api_docs_url,
                    "api_base_url": classification.api_base_url,
                    "sdk_package": classification.sdk_package,
                    "endpoint_categories": [
                        c.category for c in classification.endpoint_categories
                    ],
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
                (wrapper_dir / "meta.json").write_text(
                    json.dumps(meta, indent=2), encoding="utf-8"
                )

                needs_key = classification.auth_method in (
                    "api_key", "bearer_token", "oauth2",
                )

                logger.info(
                    "wrapper_saved_and_validated",
                    service=service,
                    slug=slug,
                    tools=tool_names,
                    tool_count=len(tool_names),
                    code_length=len(wrapper_code),
                    attempt=attempt,
                    duration_ms=round((time.monotonic() - t0) * 1000, 1),
                )

                return WrapperDeployResult(
                    success=True,
                    service_name=service,
                    wrapper_path=str(wrapper_file),
                    tools_registered=tool_names,
                    needs_api_key=needs_key,
                    api_key_env_var=env_var if needs_key else None,
                )

            return WrapperDeployResult(
                service_name=service,
                error="Could not create module spec for validation",
            )

        except SyntaxError as e:
            last_error = f"Generated code has syntax errors: {e}"
            logger.warning(
                "wrapper_syntax_error_retrying",
                service=service,
                attempt=attempt,
                error=str(e),
            )
            if attempt < max_attempts:
                continue
        except Exception as e:
            last_error = f"Wrapper validation failed: {e}"
            logger.error("wrapper_validation_error", service=service, error=str(e))
            break

    return WrapperDeployResult(service_name=service, error=last_error)


async def _generate_wrapper_code(
    classification: IntegrationClassification,
    slug: str,
    env_var: str,
) -> str | None:
    """Call Gemini to generate comprehensive wrapper Python code."""
    api_key = settings.openrouter_api_key
    if not api_key:
        return None

    has_inventory = bool(classification.endpoint_categories)

    # Even without Phase 2 endpoint inventory, try to fetch the OpenAPI spec
    # summary directly for the generator prompt.
    spec_summary = ""
    if not has_inventory:
        spec_summary = await _fetch_openapi_summary(classification)

    if has_inventory:
        endpoint_inventory = _format_endpoint_inventory(classification)
        category_summary = "\n".join(
            f"  - {c.category}: {c.description} ({len(c.endpoints)} endpoints)"
            for c in classification.endpoint_categories
        )
        prompt = _GENERATOR_PROMPT.format(
            service_name=classification.service_name,
            api_base_url=classification.api_base_url or "Infer from documentation",
            api_docs_url=classification.api_docs_url or "Not available",
            sdk_package=classification.sdk_package or "None",
            auth_method=classification.auth_method,
            auth_header_format=classification.auth_header_format or "Bearer {api_key}",
            slug=slug,
            endpoint_inventory=endpoint_inventory,
            category_summary=category_summary,
        )
    elif spec_summary:
        prompt = _GENERATOR_PROMPT.format(
            service_name=classification.service_name,
            api_base_url=classification.api_base_url or "Infer from documentation",
            api_docs_url=classification.api_docs_url or "Not available",
            sdk_package=classification.sdk_package or "None",
            auth_method=classification.auth_method,
            auth_header_format=classification.auth_header_format or "Bearer {api_key}",
            slug=slug,
            endpoint_inventory=spec_summary,
            category_summary="(See OpenAPI spec summary above for all categories)",
        )
    else:
        prompt = _GENERATOR_PROMPT_MINIMAL.format(
            service_name=classification.service_name,
            api_docs_url=classification.api_docs_url or "Not available",
            sdk_package=classification.sdk_package or "None",
            auth_method=classification.auth_method,
            api_base_url=classification.api_base_url or "Infer from documentation",
            slug=slug,
        )

    payload = {
        "model": _GENERATOR_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": LLMPresets.CODE_GEN.temperature,
        "max_tokens": LLMPresets.CODE_GEN.max_tokens,
    }

    try:
        async with httpx.AsyncClient(
            base_url=settings.openrouter_base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "X-Title": "Lucy Wrapper Generator",
            },
            timeout=httpx.Timeout(connect=5.0, read=120.0, write=5.0, pool=15.0),
        ) as client:
            resp = await client.post("/chat/completions", json=payload)
            resp.raise_for_status()
            data = resp.json()

        content = ""
        choices = data.get("choices", [])
        if choices:
            content = choices[0].get("message", {}).get("content", "")

        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[-1]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

        if not content or len(content) < 100:
            logger.warning("wrapper_code_too_short", service=classification.service_name)
            return None

        logger.info(
            "wrapper_code_generated",
            service=classification.service_name,
            code_length=len(content),
            had_endpoint_inventory=has_inventory,
        )
        return content

    except Exception as e:
        logger.error(
            "wrapper_code_generation_failed",
            service=classification.service_name,
            error=str(e),
        )
        return None


def _format_endpoint_inventory(classification: IntegrationClassification) -> str:
    """Format the discovered endpoint inventory into a prompt-friendly string."""
    lines = ["Full API Endpoint Inventory:\n"]

    for cat in classification.endpoint_categories:
        lines.append(f"\n## {cat.category} — {cat.description}")
        for ep in cat.endpoints:
            params_str = ""
            if ep.parameters:
                param_parts = []
                for p in ep.parameters:
                    req = " [REQUIRED]" if p.get("required") else ""
                    param_parts.append(
                        f"{p.get('name', '?')}: {p.get('type', 'string')}{req}"
                    )
                params_str = f" | Params: {', '.join(param_parts)}"
            lines.append(
                f"  {ep.method} {ep.path} — {ep.description}{params_str}"
            )

    lines.append(f"\nTotal endpoints: {sum(len(c.endpoints) for c in classification.endpoint_categories)}")
    return "\n".join(lines)


async def _fetch_openapi_summary(classification: IntegrationClassification) -> str:
    """Try to fetch the OpenAPI spec and create a path summary for the generator."""
    from lucy.integrations.grounded_search import (
        _correct_base_url_from_spec,
        _summarize_openapi_spec,
    )

    urls_to_try: list[str] = []
    if classification.openapi_spec_url:
        urls_to_try.append(classification.openapi_spec_url)
    if classification.api_base_url:
        base = classification.api_base_url.rstrip("/")
        urls_to_try.append(f"{base}/openapi.json")

    seen: set[str] = set()
    for url in urls_to_try:
        if url in seen:
            continue
        seen.add(url)
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    spec = resp.json()
                    _correct_base_url_from_spec(classification, spec)
                    summary = _summarize_openapi_spec(spec)
                    logger.info(
                        "openapi_spec_fetched_for_generator",
                        service=classification.service_name,
                        url=url,
                        summary_length=len(summary),
                    )
                    return summary
        except Exception:
            continue
    return ""


def discover_saved_wrappers() -> list[dict[str, Any]]:
    """Scan custom_wrappers/ for previously saved wrappers.

    Returns a list of metadata dicts for each discovered wrapper.
    """
    results = []
    if not _WRAPPERS_DIR.exists():
        return results
    for meta_path in _WRAPPERS_DIR.glob("*/meta.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["_dir"] = str(meta_path.parent)
            results.append(meta)
        except Exception:
            continue
    return results


def delete_custom_wrapper(slug: str) -> dict[str, Any]:
    """Delete a custom wrapper directory and its API key from keys.json.

    Returns a summary of what was removed.
    """
    import shutil

    from lucy.config import settings

    wrapper_dir = _WRAPPERS_DIR / slug
    removed: list[str] = []

    if not wrapper_dir.exists():
        return {"error": f"No custom integration found for '{slug}'"}

    meta_path = wrapper_dir / "meta.json"
    service_name = slug
    tool_count = 0
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            service_name = meta.get("service_name", slug)
            tool_count = meta.get("total_tools", 0)
        except Exception as e:
            logger.warning("wrapper_meta_parse_failed", slug=slug, error=str(e))

    shutil.rmtree(wrapper_dir, ignore_errors=True)
    removed.append(f"wrapper directory: custom_wrappers/{slug}/")

    keys_path = Path(settings.workspace_root).parent / "keys.json"
    if keys_path.exists():
        try:
            keys_data = json.loads(keys_path.read_text(encoding="utf-8"))
            ci = keys_data.get("custom_integrations", {})
            if slug in ci:
                del ci[slug]
                keys_path.write_text(
                    json.dumps(keys_data, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                removed.append(f"API key for '{slug}'")
        except Exception as e:
            logger.warning("delete_wrapper_keys_cleanup_failed", slug=slug, error=str(e))

    logger.info(
        "custom_wrapper_deleted",
        slug=slug,
        service=service_name,
        removed=removed,
    )

    return {
        "service_name": service_name,
        "slug": slug,
        "tool_count": tool_count,
        "removed": removed,
    }
