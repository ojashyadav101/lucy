"""Dynamic Integration Resolver — orchestrates the 3-stage pipeline.

When Composio cannot find a native toolkit for a service, this module:

  1. Runs a grounded search (Gemini) to classify the service.
  2. Attempts Stage 1 (MCP), Stage 2 (OpenAPI), or Stage 3 (Custom Wrapper)
     in strict priority order.
  3. Returns a structured result that the agent can present to the user.

The resolver is designed to be called *after* user consent has been obtained.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

from lucy.integrations.grounded_search import (
    IntegrationClassification,
    classify_service,
    discover_endpoints,
)
from lucy.integrations.mcp_manager import MCPInstallResult, install_mcp_server
from lucy.integrations.openapi_registrar import (
    OpenAPIRegistrationResult,
    register_openapi_spec,
)
from lucy.integrations.wrapper_generator import (
    WrapperDeployResult,
    generate_and_deploy_wrapper,
)

logger = structlog.get_logger()


class ResolutionStage(str, Enum):
    MCP = "mcp"
    OPENAPI = "openapi"
    WRAPPER = "wrapper"
    FAILED = "failed"


@dataclass
class ResolutionResult:
    """Final outcome of the dynamic integration pipeline."""

    service_name: str = ""
    stage: ResolutionStage = ResolutionStage.FAILED
    success: bool = False

    classification: IntegrationClassification | None = None
    mcp_result: MCPInstallResult | None = None
    openapi_result: OpenAPIRegistrationResult | None = None
    wrapper_result: WrapperDeployResult | None = None

    needs_api_key: bool = False
    api_key_env_var: str | None = None
    result_data: dict[str, Any] = field(default_factory=dict)
    user_message: str = ""
    error: str | None = None

    timing_ms: dict[str, float] = field(default_factory=dict)
    decision_log: list[str] = field(default_factory=list)


async def resolve_integration(service_name: str) -> ResolutionResult:
    """Run the full 3-stage resolution pipeline for a single service.

    Caller is responsible for obtaining user consent before invoking this.
    """
    pipeline_start = time.monotonic()
    result = ResolutionResult(service_name=service_name)

    logger.info(
        "resolver_pipeline_started",
        service=service_name,
        step="PIPELINE_START",
    )

    # ── Step B: Grounded Research ────────────────────────────
    t0 = time.monotonic()
    logger.info("resolver_starting_research", service=service_name, step="RESEARCH_START")
    classification = await classify_service(service_name)
    research_ms = (time.monotonic() - t0) * 1000
    result.timing_ms["grounded_search_ms"] = round(research_ms, 1)
    result.classification = classification

    logger.info(
        "resolver_research_complete",
        service=service_name,
        step="RESEARCH_COMPLETE",
        duration_ms=round(research_ms, 1),
        has_mcp=classification.has_mcp,
        mcp_repo=classification.mcp_repo_url,
        has_openapi=classification.has_openapi,
        openapi_url=classification.openapi_spec_url,
        has_sdk=classification.has_sdk,
        sdk_package=classification.sdk_package,
        api_docs=classification.api_docs_url,
        auth_method=classification.auth_method,
        summary=classification.summary,
    )

    if classification.error:
        result.error = classification.error
        result.decision_log.append(f"RESEARCH_FAILED: {classification.error}")
        result.result_data = {
            "service": service_name,
            "status": "research_failed",
            "error": classification.error,
        }
        result.timing_ms["total_ms"] = round((time.monotonic() - pipeline_start) * 1000, 1)
        return result

    result.decision_log.append(
        f"RESEARCH_OK: mcp={classification.has_mcp}, "
        f"openapi={classification.has_openapi}, "
        f"sdk={classification.has_sdk}, "
        f"api_docs={'yes' if classification.api_docs_url else 'no'}"
    )

    # ── Stage 1: MCP ─────────────────────────────────────────
    if classification.has_mcp and classification.mcp_repo_url:
        t0 = time.monotonic()
        result.decision_log.append(f"STAGE1_MCP_ATTEMPTING: repo={classification.mcp_repo_url}")
        logger.info(
            "resolver_trying_mcp",
            service=service_name,
            step="STAGE1_MCP_START",
            repo=classification.mcp_repo_url,
        )
        mcp_result = await install_mcp_server(classification)
        mcp_ms = (time.monotonic() - t0) * 1000
        result.timing_ms["mcp_install_ms"] = round(mcp_ms, 1)
        result.mcp_result = mcp_result

        if mcp_result.success:
            result.stage = ResolutionStage.MCP
            result.success = True
            result.needs_api_key = mcp_result.needs_api_key
            result.api_key_env_var = mcp_result.api_key_env_var
            result.decision_log.append(
                f"STAGE1_MCP_SUCCESS: session={mcp_result.session_id}, "
                f"needs_key={mcp_result.needs_api_key}, duration={round(mcp_ms)}ms"
            )
            result.result_data = _build_mcp_success_data(service_name, mcp_result)
            result.timing_ms["total_ms"] = round((time.monotonic() - pipeline_start) * 1000, 1)
            _log_pipeline_summary(result)
            return result

        result.decision_log.append(
            f"STAGE1_MCP_FAILED: {mcp_result.error}, falling through to Stage 2"
        )
        logger.warning(
            "resolver_mcp_failed_falling_through",
            service=service_name,
            step="STAGE1_MCP_FAILED",
            error=mcp_result.error,
            duration_ms=round(mcp_ms, 1),
        )
    else:
        result.decision_log.append("STAGE1_MCP_SKIPPED: no MCP server found in research")

    # ── Stage 2: OpenAPI ─────────────────────────────────────
    if classification.has_openapi and classification.openapi_spec_url:
        t0 = time.monotonic()
        result.decision_log.append(
            f"STAGE2_OPENAPI_ATTEMPTING: spec={classification.openapi_spec_url}"
        )
        logger.info(
            "resolver_trying_openapi",
            service=service_name,
            step="STAGE2_OPENAPI_START",
            spec_url=classification.openapi_spec_url,
        )
        openapi_result = await register_openapi_spec(classification)
        openapi_ms = (time.monotonic() - t0) * 1000
        result.timing_ms["openapi_register_ms"] = round(openapi_ms, 1)
        result.openapi_result = openapi_result

        if openapi_result.success:
            result.stage = ResolutionStage.OPENAPI
            result.success = True
            result.decision_log.append(
                f"STAGE2_OPENAPI_SUCCESS: slug={openapi_result.toolkit_slug}, "
                f"duration={round(openapi_ms)}ms"
            )
            result.result_data = {
                "service": service_name,
                "method": "OpenAPI specification",
                "status": "connected",
                "toolkit_slug": openapi_result.toolkit_slug,
                "needs_api_key": False,
            }
            result.timing_ms["total_ms"] = round((time.monotonic() - pipeline_start) * 1000, 1)
            _log_pipeline_summary(result)
            return result

        result.decision_log.append(
            f"STAGE2_OPENAPI_FAILED: {openapi_result.error}, falling through to Stage 3"
        )
        logger.warning(
            "resolver_openapi_failed_falling_through",
            service=service_name,
            step="STAGE2_OPENAPI_FAILED",
            error=openapi_result.error,
            duration_ms=round(openapi_ms, 1),
        )
    else:
        result.decision_log.append("STAGE2_OPENAPI_SKIPPED: no OpenAPI spec found in research")

    # ── Phase 2: Deep Endpoint Discovery ─────────────────────
    # Before generating a wrapper, fetch the actual API docs and extract
    # a complete endpoint inventory so the wrapper covers everything.
    if classification.has_sdk or classification.api_docs_url or classification.has_openapi:
        t0 = time.monotonic()
        logger.info(
            "resolver_endpoint_discovery_start",
            service=service_name,
            step="PHASE2_ENDPOINT_DISCOVERY",
        )
        classification = await discover_endpoints(classification)
        discovery_ms = (time.monotonic() - t0) * 1000
        result.timing_ms["endpoint_discovery_ms"] = round(discovery_ms, 1)
        result.classification = classification

        ep_count = sum(len(c.endpoints) for c in classification.endpoint_categories)
        cat_names = [c.category for c in classification.endpoint_categories]
        result.decision_log.append(
            f"PHASE2_DISCOVERY_COMPLETE: {ep_count} endpoints across "
            f"{len(cat_names)} categories ({', '.join(cat_names)}), "
            f"duration={round(discovery_ms)}ms"
        )
        logger.info(
            "resolver_endpoint_discovery_complete",
            service=service_name,
            step="PHASE2_DISCOVERY_COMPLETE",
            endpoint_count=ep_count,
            categories=cat_names,
            duration_ms=round(discovery_ms, 1),
        )

    # ── Stage 3: Generate wrapper via LLM and deploy ────────
    if classification.has_sdk or classification.api_docs_url:
        t0 = time.monotonic()
        result.decision_log.append(
            f"STAGE3_WRAPPER_GENERATING: sdk={classification.sdk_package}, "
            f"docs={classification.api_docs_url}, "
            f"endpoints_discovered={sum(len(c.endpoints) for c in classification.endpoint_categories)}"
        )
        logger.info(
            "resolver_generating_wrapper",
            service=service_name,
            step="STAGE3_WRAPPER_START",
            sdk=classification.sdk_package,
            api_docs=classification.api_docs_url,
            endpoint_categories=len(classification.endpoint_categories),
        )

        wrapper_result = await generate_and_deploy_wrapper(classification)
        wrapper_ms = (time.monotonic() - t0) * 1000
        result.timing_ms["wrapper_generate_ms"] = round(wrapper_ms, 1)
        result.wrapper_result = wrapper_result

        if wrapper_result.success:
            result.stage = ResolutionStage.WRAPPER
            result.success = True
            result.needs_api_key = wrapper_result.needs_api_key
            result.api_key_env_var = wrapper_result.api_key_env_var
            result.decision_log.append(
                f"STAGE3_WRAPPER_SUCCESS: tools={wrapper_result.tools_registered}, "
                f"path={wrapper_result.wrapper_path}, duration={round(wrapper_ms)}ms"
            )
            result.result_data = _build_wrapper_success_data(
                service_name, wrapper_result,
            )
            result.timing_ms["total_ms"] = round(
                (time.monotonic() - pipeline_start) * 1000, 1
            )
            _log_pipeline_summary(result)
            return result

        result.decision_log.append(
            f"STAGE3_WRAPPER_FAILED: {wrapper_result.error}"
        )
        logger.warning(
            "resolver_wrapper_failed",
            service=service_name,
            step="STAGE3_WRAPPER_FAILED",
            error=wrapper_result.error,
            duration_ms=round(wrapper_ms, 1),
        )
    else:
        result.decision_log.append(
            "STAGE3_WRAPPER_SKIPPED: no SDK or API docs found in research"
        )

    # ── Stage 4: Honest Failure ──────────────────────────────
    result.stage = ResolutionStage.FAILED
    result.decision_log.append("PIPELINE_FAILED: all stages exhausted")
    result.result_data = _build_failure_data(service_name, classification)
    result.timing_ms["total_ms"] = round((time.monotonic() - pipeline_start) * 1000, 1)
    _log_pipeline_summary(result)
    return result


async def resolve_multiple(service_names: list[str]) -> list[ResolutionResult]:
    """Resolve multiple services sequentially."""
    results = []
    for name in service_names:
        r = await resolve_integration(name)
        results.append(r)
    return results


def _log_pipeline_summary(result: ResolutionResult) -> None:
    """Emit a single summary log line with full pipeline telemetry."""
    logger.info(
        "resolver_pipeline_complete",
        step="PIPELINE_COMPLETE",
        service=result.service_name,
        stage=result.stage.value,
        success=result.success,
        needs_api_key=result.needs_api_key,
        timing_ms=result.timing_ms,
        decision_log=result.decision_log,
        error=result.error,
    )


# ── Structured result builders ────────────────────────────────
# Return data dicts, NOT pre-formatted user messages.
# The LLM in the agent loop receives this data and crafts its own
# natural response — ensuring every message feels human.


def _build_mcp_success_data(
    service: str, mcp: MCPInstallResult
) -> dict[str, Any]:
    return {
        "service": service,
        "method": "MCP server",
        "status": "connected",
        "needs_api_key": mcp.needs_api_key,
        "api_key_env_var": mcp.api_key_env_var,
    }


def _build_wrapper_success_data(
    service: str, wrapper: WrapperDeployResult
) -> dict[str, Any]:
    return {
        "service": service,
        "method": "custom API wrapper",
        "status": "connected",
        "capabilities_count": len(wrapper.tools_registered or []),
        "capability_categories": _categorize_tools(
            wrapper.tools_registered or []
        ),
        "needs_api_key": wrapper.needs_api_key,
        "api_key_env_var": wrapper.api_key_env_var,
    }


def _categorize_tools(tool_names: list[str]) -> list[str]:
    """Group raw tool names into plain-English capability categories."""
    keyword_map = {
        "products": "managing products",
        "subscriptions": "subscriptions",
        "customers": "customer management",
        "orders": "orders and invoices",
        "checkout": "checkout flows",
        "benefits": "benefits and perks",
        "webhooks": "webhook management",
        "discounts": "discounts and promotions",
        "metrics": "analytics and metrics",
        "organizations": "organization settings",
        "payments": "payments",
        "refunds": "refunds",
        "users": "user management",
        "sessions": "session management",
        "invitations": "invitations",
        "emails": "email management",
        "domains": "domain configuration",
        "templates": "templates",
        "roles": "roles and permissions",
    }
    seen: set[str] = set()
    categories: list[str] = []
    for name in tool_names:
        lower = name.lower()
        for keyword, label in keyword_map.items():
            if keyword in lower and label not in seen:
                seen.add(label)
                categories.append(label)
    return categories


def _build_failure_data(
    service: str, classification: IntegrationClassification
) -> dict[str, Any]:
    reasons = []
    if not classification.has_mcp:
        reasons.append("no MCP server found")
    if not classification.has_openapi:
        reasons.append("no public OpenAPI specification")
    if not classification.has_sdk and not classification.api_docs_url:
        reasons.append("no accessible API or SDK")
    return {
        "service": service,
        "status": "failed",
        "reasons": reasons,
    }
