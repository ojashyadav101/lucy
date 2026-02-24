"""OpenAPI spec registrar (Stage 2).

Fetches a service's OpenAPI specification and attempts to register it
with Composio as a Custom App so Lucy can use COMPOSIO_MANAGE_CONNECTIONS
natively afterward.

Composio's SDK does not expose a programmatic create-app endpoint, so
this module uses direct HTTP calls to the Composio web API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from lucy.config import settings
from lucy.integrations.grounded_search import IntegrationClassification

logger = structlog.get_logger()

_COMPOSIO_API_BASE = "https://backend.composio.dev/api"


@dataclass
class OpenAPIRegistrationResult:
    """Outcome of an OpenAPI spec registration attempt."""

    success: bool = False
    service_name: str = ""
    toolkit_slug: str | None = None
    spec_url: str | None = None
    error: str | None = None


async def register_openapi_spec(
    classification: IntegrationClassification,
) -> OpenAPIRegistrationResult:
    """Fetch an OpenAPI spec and register it with Composio.

    Steps:
      1. Download the OpenAPI spec from the discovered URL.
      2. POST it to Composio's custom-app creation endpoint.
      3. Return the new toolkit slug on success.
    """
    service = classification.service_name
    spec_url = classification.openapi_spec_url

    if not spec_url:
        return OpenAPIRegistrationResult(
            service_name=service,
            error="No OpenAPI spec URL provided by grounded search",
        )

    api_key = settings.composio_api_key
    if not api_key:
        return OpenAPIRegistrationResult(
            service_name=service,
            error="Composio API key not configured",
        )

    try:
        # 1. Fetch the spec — try the given URL first, then common variants
        spec_content = None
        urls_to_try = _spec_url_variants(spec_url, service)

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            for url in urls_to_try:
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        spec_content = resp.text
                        spec_url = url
                        break
                except Exception:
                    continue

        if not spec_content:
            return OpenAPIRegistrationResult(
                service_name=service,
                spec_url=spec_url,
                error=f"Could not fetch OpenAPI spec from any of: {urls_to_try}",
            )

        slug = service.lower().replace(" ", "_").replace("-", "_")

        # 2. Attempt Composio custom app registration via their API
        headers = {
            "x-api-key": api_key,
            "Content-Type": "application/json",
        }

        # Try the v1 apps endpoint (undocumented but sometimes present)
        registration_payload = {
            "name": service,
            "unique_key": slug,
            "description": f"Custom integration for {service}",
            "openApiSpec": spec_content,
            "authScheme": _map_auth_scheme(classification.auth_method),
        }

        async with httpx.AsyncClient(
            base_url=_COMPOSIO_API_BASE,
            headers=headers,
            timeout=60.0,
        ) as client:
            # Try POST /v1/apps/openapi
            try:
                resp = await client.post("/v1/apps/openapi", json=registration_payload)
                if resp.status_code in (200, 201):
                    data = resp.json()
                    registered_slug = data.get("appKey", data.get("unique_key", slug))
                    logger.info(
                        "openapi_registered_v1",
                        service=service,
                        slug=registered_slug,
                    )
                    return OpenAPIRegistrationResult(
                        success=True,
                        service_name=service,
                        toolkit_slug=registered_slug,
                        spec_url=spec_url,
                    )
            except Exception:
                pass

            # Fallback: try POST /v1/apps with the spec as body
            try:
                resp = await client.post("/v1/apps", json=registration_payload)
                if resp.status_code in (200, 201):
                    data = resp.json()
                    registered_slug = data.get("appKey", data.get("unique_key", slug))
                    logger.info(
                        "openapi_registered_v1_apps",
                        service=service,
                        slug=registered_slug,
                    )
                    return OpenAPIRegistrationResult(
                        success=True,
                        service_name=service,
                        toolkit_slug=registered_slug,
                        spec_url=spec_url,
                    )
            except Exception:
                pass

            # Fallback: try the v2 endpoint
            try:
                resp = await client.post("/v2/apps", json=registration_payload)
                if resp.status_code in (200, 201):
                    data = resp.json()
                    registered_slug = data.get("appKey", data.get("unique_key", slug))
                    logger.info(
                        "openapi_registered_v2",
                        service=service,
                        slug=registered_slug,
                    )
                    return OpenAPIRegistrationResult(
                        success=True,
                        service_name=service,
                        toolkit_slug=registered_slug,
                        spec_url=spec_url,
                    )
            except Exception:
                pass

        logger.warning(
            "openapi_registration_all_endpoints_failed",
            service=service,
            spec_url=spec_url,
        )
        return OpenAPIRegistrationResult(
            service_name=service,
            spec_url=spec_url,
            error=(
                "Composio's API did not accept the OpenAPI spec via any known endpoint. "
                "Manual upload at https://app.composio.dev/custom_tools may be required."
            ),
        )

    except httpx.HTTPStatusError as e:
        logger.error(
            "openapi_fetch_failed",
            service=service,
            url=spec_url,
            status=e.response.status_code,
        )
        return OpenAPIRegistrationResult(
            service_name=service,
            spec_url=spec_url,
            error=f"Failed to fetch spec (HTTP {e.response.status_code})",
        )
    except Exception as e:
        logger.error("openapi_registration_error", service=service, error=str(e))
        return OpenAPIRegistrationResult(
            service_name=service,
            spec_url=spec_url,
            error=str(e),
        )


def _map_auth_scheme(auth_method: str) -> str:
    """Map our classification auth method to Composio's auth scheme enum."""
    mapping = {
        "oauth2": "OAUTH2",
        "api_key": "API_KEY",
        "bearer_token": "BEARER_TOKEN",
        "basic": "BASIC",
        "none": "NONE",
    }
    return mapping.get(auth_method, "API_KEY")


def _spec_url_variants(original_url: str, service_name: str) -> list[str]:
    """Generate common URL variants for OpenAPI specs."""
    variants = [original_url]

    slug = service_name.lower().replace(" ", "").replace(".", "").replace("-", "")

    from urllib.parse import urlparse
    parsed = urlparse(original_url)
    domain = parsed.netloc or ""
    base_domain = domain.replace("www.", "")

    common_patterns = [
        f"https://api.{base_domain}/openapi.json",
        f"https://api.{base_domain}/v1/openapi.json",
        f"https://api.{base_domain}/docs/openapi.json",
        f"https://api.{base_domain}/swagger.json",
        f"https://{base_domain}/api/openapi.json",
        f"https://{base_domain}/api/v1/openapi.json",
        f"https://{base_domain}/docs/openapi.json",
    ]

    for p in common_patterns:
        if p not in variants:
            variants.append(p)

    return variants
