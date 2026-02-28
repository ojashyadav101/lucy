"""Grounded search for integration classification.

Two-phase research:
  Phase 1 — Cheap Gemini call to classify MCP / OpenAPI / SDK availability.
  Phase 2 — Fetch actual API docs and extract full endpoint inventory so the
             wrapper generator can build comprehensive coverage.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog

from lucy.config import LLMPresets, settings

logger = structlog.get_logger()

_SEARCH_MODEL = settings.model_tier_fast

_CLASSIFICATION_PROMPT = """\
You are an integration research assistant. Given a service name, determine how
an AI agent can programmatically connect to it.

Research the service "{service_name}" and answer ONLY with a JSON object:

{{
  "service_name": "{service_name}",
  "has_mcp": true/false,
  "mcp_repo_url": "GitHub URL or npm package name if MCP server exists, else null",
  "mcp_docs_url": "URL to MCP server documentation/README, else null",
  "has_openapi": true/false,
  "openapi_spec_url": "Direct URL to .json or .yaml OpenAPI spec file, else null",
  "openapi_docs_url": "URL to API reference documentation, else null",
  "has_sdk": true/false,
  "sdk_package": "PyPI or npm package name for official/popular SDK, else null",
  "sdk_repo_url": "GitHub repo URL for SDK, else null",
  "api_docs_url": "URL to the service's general API documentation, else null",
  "api_base_url": "The base URL for API calls (e.g. https://api.example.com), else null",
  "auth_method": "oauth2 | api_key | bearer_token | basic | none | unknown",
  "summary": "One-sentence summary of the best integration path"
}}

Rules:
- MCP = Model Context Protocol server. Search for "[service] MCP server" on GitHub.
- OpenAPI = Swagger/OpenAPI specification file. Look for official API docs that publish one.
- SDK = official or well-maintained Python library on PyPI or GitHub.
- api_base_url = the root URL that API calls are made to (not the docs URL).
- Only report things you are confident exist. Do not hallucinate URLs.
- If a service is too niche to have any of these, say so honestly.
- Return ONLY valid JSON, no markdown fences, no explanation.
"""

_ENDPOINT_DISCOVERY_PROMPT = """\
You are an API endpoint analyst. Given the following information about
"{service_name}", produce a COMPLETE inventory of every API endpoint category
and the specific operations an AI assistant should be able to perform.

API Documentation URL: {api_docs_url}
API Base URL: {api_base_url}
OpenAPI Spec URL: {openapi_spec_url}
SDK Package: {sdk_package}
Auth Method: {auth_method}

{api_content}

Analyze the above and return ONLY a JSON object:

{{
  "api_base_url": "confirmed base URL for API calls",
  "endpoint_categories": [
    {{
      "category": "Products",
      "description": "Manage products and pricing",
      "endpoints": [
        {{
          "name": "list_products",
          "method": "GET",
          "path": "/api/v1/products",
          "description": "List all products with optional filters",
          "parameters": [
            {{"name": "organization_id", "type": "string", "required": false, "description": "Filter by organization"}}
          ]
        }}
      ]
    }}
  ],
  "total_endpoints": 25,
  "auth_header_format": "Bearer {{api_key}}",
  "notes": "Any important API quirks (pagination style, rate limits, required headers)"
}}

Rules:
- Be EXHAUSTIVE. Cover every category: CRUD operations, search, analytics, billing,
  webhooks, users/customers, etc.
- For each category, list EVERY endpoint you can identify from the documentation.
- Include the exact HTTP method (GET/POST/PUT/PATCH/DELETE) and path.
- Include ALL parameters with their types and whether they're required.
- DO NOT skip endpoints because they seem less important. A complete integration
  needs complete coverage.
- If you cannot determine exact paths, infer them from standard REST conventions
  and the SDK/docs you have.
- Return ONLY valid JSON, no markdown fences, no explanation.
"""


@dataclass
class EndpointInfo:
    name: str = ""
    method: str = "GET"
    path: str = ""
    description: str = ""
    parameters: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class EndpointCategory:
    category: str = ""
    description: str = ""
    endpoints: list[EndpointInfo] = field(default_factory=list)


@dataclass
class IntegrationClassification:
    """Result of grounded search for a service."""

    service_name: str = ""
    has_mcp: bool = False
    mcp_repo_url: str | None = None
    mcp_docs_url: str | None = None
    has_openapi: bool = False
    openapi_spec_url: str | None = None
    openapi_docs_url: str | None = None
    has_sdk: bool = False
    sdk_package: str | None = None
    sdk_repo_url: str | None = None
    api_docs_url: str | None = None
    api_base_url: str | None = None
    auth_method: str = "unknown"
    summary: str = ""
    raw_response: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    # Phase 2: detailed endpoint inventory
    endpoint_categories: list[EndpointCategory] = field(default_factory=list)
    total_endpoints: int = 0
    auth_header_format: str | None = None
    api_notes: str | None = None


async def classify_service(service_name: str) -> IntegrationClassification:
    """Phase 1: classify a service's integration options via Gemini.

    After the LLM returns its best guess, we VERIFY the api_base_url by
    actually hitting it. If it returns HTML instead of JSON, we know it's
    the website, not the API. In that case, we research the correct URL
    using the OpenAPI spec and web search — not hard-coded heuristics.
    """
    t0 = time.monotonic()
    api_key = settings.openrouter_api_key
    if not api_key:
        return IntegrationClassification(
            service_name=service_name,
            error="OpenRouter API key not configured",
        )

    prompt = _CLASSIFICATION_PROMPT.format(service_name=service_name)
    parsed = await _call_gemini(prompt, api_key)
    duration = round((time.monotonic() - t0) * 1000, 1)

    if isinstance(parsed, str):
        return IntegrationClassification(service_name=service_name, error=parsed)

    classification = IntegrationClassification(
        service_name=service_name,
        has_mcp=bool(parsed.get("has_mcp")),
        mcp_repo_url=parsed.get("mcp_repo_url"),
        mcp_docs_url=parsed.get("mcp_docs_url"),
        has_openapi=bool(parsed.get("has_openapi")),
        openapi_spec_url=parsed.get("openapi_spec_url"),
        openapi_docs_url=parsed.get("openapi_docs_url"),
        has_sdk=bool(parsed.get("has_sdk")),
        sdk_package=parsed.get("sdk_package"),
        sdk_repo_url=parsed.get("sdk_repo_url"),
        api_docs_url=parsed.get("api_docs_url"),
        api_base_url=parsed.get("api_base_url"),
        auth_method=parsed.get("auth_method", "unknown"),
        summary=parsed.get("summary", ""),
        raw_response=parsed,
    )

    # Verify the claimed base URL is actually an API, not a website
    await _verify_and_correct_base_url(classification, api_key)

    logger.info(
        "grounded_search_complete",
        service=service_name,
        has_mcp=classification.has_mcp,
        has_openapi=classification.has_openapi,
        has_sdk=classification.has_sdk,
        api_base_url=classification.api_base_url,
        duration_ms=round((time.monotonic() - t0) * 1000, 1),
        raw_response=parsed,
    )

    return classification


async def _verify_and_correct_base_url(
    classification: IntegrationClassification,
    api_key: str,
) -> None:
    """Verify the claimed api_base_url actually serves an API.

    Strategy:
    1. Hit the URL — if it returns JSON or a 401/403 (auth required), it's an API.
    2. If it returns HTML, the LLM gave us the website, not the API.
    3. To find the real URL: check the OpenAPI spec's `servers` block.
    4. If that fails: ask the LLM to web-search for the correct API base URL.

    No hard-coded heuristics (no "try adding api. prefix" etc.).
    """
    base = classification.api_base_url
    if not base:
        return

    # Step 1: Probe the claimed URL
    probe = await _probe_url(base)
    if probe == "api":
        logger.info("base_url_verified_as_api", service=classification.service_name, url=base)
        return

    logger.info(
        "base_url_is_not_api",
        service=classification.service_name,
        url=base,
        probe_result=probe,
    )

    # Step 2: Try to find the correct URL from the OpenAPI spec
    corrected = await _find_base_url_from_spec(classification)
    if corrected:
        old = classification.api_base_url
        classification.api_base_url = corrected
        logger.info(
            "base_url_corrected_from_spec",
            service=classification.service_name,
            old=old,
            new=corrected,
        )
        return

    # Step 3: Ask the LLM to research the correct base URL via web search
    corrected = await _research_base_url(classification, api_key)
    if corrected:
        # Verify the researched URL is actually an API
        verify = await _probe_url(corrected)
        if verify == "api":
            old = classification.api_base_url
            classification.api_base_url = corrected
            logger.info(
                "base_url_corrected_from_research",
                service=classification.service_name,
                old=old,
                new=corrected,
            )
            return

    logger.warning(
        "base_url_could_not_be_verified",
        service=classification.service_name,
        url=base,
    )


async def _probe_url(url: str) -> str:
    """Hit a URL and classify what it serves.

    Returns:
        "api"     — responded with JSON or auth-required (401/403)
        "website" — responded with HTML
        "error"   — could not connect
    """
    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=10.0
        ) as client:
            resp = await client.get(
                url.rstrip("/"),
                headers={"Accept": "application/json"},
            )
            ct = resp.headers.get("content-type", "")

            if resp.status_code in (401, 403):
                return "api"
            if "json" in ct or "openapi" in ct:
                return "api"
            if "html" in ct:
                return "website"
            if resp.text.lstrip()[:1] in ("{", "["):
                return "api"
            return "website"
    except Exception:
        return "error"


async def _find_base_url_from_spec(
    classification: IntegrationClassification,
) -> str | None:
    """Try to extract the base URL from the OpenAPI spec's `servers` block."""
    spec_urls: list[str] = []
    if classification.openapi_spec_url:
        spec_urls.append(classification.openapi_spec_url)
    if classification.api_base_url:
        base = classification.api_base_url.rstrip("/")
        spec_urls.append(f"{base}/openapi.json")

    for spec_url in spec_urls:
        try:
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=15.0
            ) as client:
                resp = await client.get(spec_url)
                if resp.status_code != 200:
                    continue
                spec = resp.json()
                servers = spec.get("servers", [])
                if servers and isinstance(servers[0], dict):
                    candidate = servers[0].get("url", "")
                    if candidate and candidate.startswith("http"):
                        probe = await _probe_url(candidate)
                        if probe == "api":
                            return candidate.rstrip("/")
        except Exception:
            continue
    return None


async def _research_base_url(
    classification: IntegrationClassification,
    api_key: str,
) -> str | None:
    """Ask the LLM to web-search for the correct API base URL.

    This is the generic fallback — no hard-coded domain patterns, just
    asking the LLM with grounded search to find the real endpoint.
    """
    prompt = f"""I need to find the correct API base URL for "{classification.service_name}".

The URL {classification.api_base_url} appears to be the website, not the API.
The API documentation is at: {classification.api_docs_url or 'unknown'}

Search for the correct REST API base URL that developers use to make HTTP
requests to {classification.service_name}.

Return ONLY a JSON object with one field:
{{"api_base_url": "https://the-correct-api-url.com"}}

If you cannot determine the correct URL, return:
{{"api_base_url": null}}

Return ONLY valid JSON, no markdown, no explanation."""

    parsed = await _call_gemini(prompt, api_key)
    if isinstance(parsed, dict) and parsed.get("api_base_url"):
        return parsed["api_base_url"].rstrip("/")
    return None


async def discover_endpoints(
    classification: IntegrationClassification,
) -> IntegrationClassification:
    """Phase 2: fetch API docs and extract full endpoint inventory.

    Mutates the classification in-place with endpoint_categories.
    """
    t0 = time.monotonic()
    api_key = settings.openrouter_api_key
    if not api_key:
        return classification

    # Fetch actual API content to give the LLM real data to work with
    api_content = await _fetch_api_documentation(classification)

    prompt = _ENDPOINT_DISCOVERY_PROMPT.format(
        service_name=classification.service_name,
        api_docs_url=classification.api_docs_url or "Unknown",
        api_base_url=classification.api_base_url or "Unknown",
        openapi_spec_url=classification.openapi_spec_url or "None",
        sdk_package=classification.sdk_package or "None",
        auth_method=classification.auth_method,
        api_content=api_content,
    )

    parsed = await _call_gemini(prompt, api_key, max_tokens=8192)
    duration = round((time.monotonic() - t0) * 1000, 1)

    if isinstance(parsed, str):
        logger.warning(
            "endpoint_discovery_failed",
            service=classification.service_name,
            error=parsed,
            duration_ms=duration,
        )
        return classification

    if parsed.get("api_base_url"):
        classification.api_base_url = parsed["api_base_url"]

    classification.auth_header_format = parsed.get("auth_header_format")
    classification.api_notes = parsed.get("notes")
    classification.total_endpoints = parsed.get("total_endpoints", 0)

    categories = []
    for cat_data in parsed.get("endpoint_categories", []):
        endpoints = []
        for ep_data in cat_data.get("endpoints", []):
            endpoints.append(EndpointInfo(
                name=ep_data.get("name", ""),
                method=ep_data.get("method", "GET"),
                path=ep_data.get("path", ""),
                description=ep_data.get("description", ""),
                parameters=ep_data.get("parameters", []),
            ))
        categories.append(EndpointCategory(
            category=cat_data.get("category", ""),
            description=cat_data.get("description", ""),
            endpoints=endpoints,
        ))

    classification.endpoint_categories = categories

    total_eps = sum(len(c.endpoints) for c in categories)
    logger.info(
        "endpoint_discovery_complete",
        service=classification.service_name,
        categories=len(categories),
        total_endpoints=total_eps,
        category_names=[c.category for c in categories],
        duration_ms=duration,
    )

    return classification


async def _fetch_api_documentation(classification: IntegrationClassification) -> str:
    """Fetch actual API documentation content to feed into endpoint discovery.

    Tries: OpenAPI spec first, then API docs page, then SDK readme.
    Also corrects the api_base_url from the OpenAPI spec if found.
    Returns a truncated text representation.
    """
    max_chars = 30_000
    urls_to_try: list[str] = []

    if classification.openapi_spec_url:
        urls_to_try.append(classification.openapi_spec_url)
    if classification.api_base_url:
        base = classification.api_base_url.rstrip("/")
        domain = base.split("//", 1)[-1] if "//" in base else base
        urls_to_try.extend([
            f"{base}/openapi.json",
            f"{base}/docs/openapi.json",
            f"{base}/swagger.json",
            f"https://api.{domain}/openapi.json",
        ])
    if classification.openapi_docs_url:
        urls_to_try.append(classification.openapi_docs_url)
    if classification.api_docs_url:
        urls_to_try.append(classification.api_docs_url)
    if classification.sdk_repo_url:
        urls_to_try.append(classification.sdk_repo_url)

    seen: set[str] = set()
    for url in urls_to_try:
        if url in seen:
            continue
        seen.add(url)
        try:
            is_spec_url = url.endswith((".json", ".yaml", ".yml"))
            timeout = 30.0 if is_spec_url else 15.0

            async with httpx.AsyncClient(
                follow_redirects=True, timeout=timeout
            ) as client:
                resp = await client.get(url)
                if resp.status_code == 200 and resp.text.strip():
                    raw = resp.text
                    logger.info(
                        "api_docs_fetched",
                        service=classification.service_name,
                        url=url,
                        content_length=len(raw),
                    )
                    if is_spec_url or _looks_like_json(raw):
                        try:
                            spec = json.loads(raw)
                            _correct_base_url_from_spec(classification, spec)
                            return _summarize_openapi_spec(spec)
                        except json.JSONDecodeError:
                            return f"API Documentation from {url}:\n\n{raw[:max_chars]}"
                    return f"API Documentation from {url}:\n\n{raw[:max_chars]}"
        except Exception as e:
            logger.debug("api_docs_fetch_attempt_failed", url=url, error=str(e))
            continue

    return "No API documentation could be fetched. Use your knowledge of this service."


def _looks_like_json(content: str) -> bool:
    return content.lstrip()[:1] in ("{", "[")


def _correct_base_url_from_spec(
    classification: IntegrationClassification, spec: dict[str, Any]
) -> None:
    """Extract and set the true api_base_url from the OpenAPI servers block."""
    servers = spec.get("servers", [])
    if servers and isinstance(servers[0], dict):
        spec_base = servers[0].get("url", "")
        if spec_base and spec_base.startswith("http"):
            old = classification.api_base_url
            classification.api_base_url = spec_base.rstrip("/")
            if old != classification.api_base_url:
                logger.info(
                    "api_base_url_corrected_from_spec",
                    service=classification.service_name,
                    old_base=old,
                    new_base=classification.api_base_url,
                )


def _summarize_openapi_spec(spec: dict[str, Any]) -> str:
    """Extract a concise summary of all paths from an OpenAPI spec.

    Caps output to avoid overwhelming the LLM with large specs.
    """
    lines = ["OpenAPI Specification Summary:\n"]

    info = spec.get("info", {})
    lines.append(f"Title: {info.get('title', 'Unknown')}")
    lines.append(f"Version: {info.get('version', 'Unknown')}")

    servers = spec.get("servers", [])
    if servers:
        lines.append(f"Base URL: {servers[0].get('url', 'Unknown')}")

    paths = spec.get("paths", {})
    total_ops = sum(
        1 for _p, methods in paths.items()
        for m in methods if m in ("get", "post", "put", "patch", "delete")
    )
    lines.append(f"\nEndpoints ({total_ops} operations across {len(paths)} paths):\n")

    tagged: dict[str, list[str]] = {}
    for path, methods in paths.items():
        for method, details in methods.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            if not isinstance(details, dict):
                continue
            summary = details.get("summary", details.get("operationId", ""))
            tags = details.get("tags", ["Untagged"])
            tag = tags[0] if tags else "Untagged"

            params = details.get("parameters", [])
            param_parts = []
            for p in params[:5]:
                req = "*" if p.get("required") else ""
                param_parts.append(f"{p.get('name', '?')}{req}")
            params_str = f" (params: {', '.join(param_parts)})" if param_parts else ""

            body_hint = " [body]" if details.get("requestBody") else ""

            line = f"  {method.upper()} {path} — {summary}{params_str}{body_hint}"
            tagged.setdefault(tag, []).append(line)

    for tag_name in sorted(tagged.keys()):
        lines.append(f"\n## {tag_name}")
        lines.extend(tagged[tag_name])

    result = "\n".join(lines)
    max_chars = 15_000
    if len(result) > max_chars:
        result = result[:max_chars] + f"\n\n... (truncated, {total_ops} total operations)"
    return result


async def _call_gemini(
    prompt: str,
    api_key: str,
    max_tokens: int = 2048,
) -> dict[str, Any] | str:
    """Call Gemini via OpenRouter and parse the JSON response.

    Returns parsed dict on success, error string on failure.
    """
    payload = {
        "model": _SEARCH_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": LLMPresets.SEARCH.temperature,
        "max_tokens": max_tokens,
    }

    try:
        async with httpx.AsyncClient(
            base_url=settings.openrouter_base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "X-Title": "Lucy Integration Resolver",
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

        return json.loads(content)

    except json.JSONDecodeError as e:
        return f"Failed to parse Gemini response as JSON: {e}"
    except Exception as e:
        return f"Gemini call failed: {e}"
