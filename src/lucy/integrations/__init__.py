"""Integration layer: Composio meta-tools, CamoFox browser, and dynamic resolution."""

from lucy.integrations.camofox import CamoFoxClient, get_camofox_client
from lucy.integrations.composio_client import ComposioClient, get_composio_client
from lucy.integrations.grounded_search import (
    IntegrationClassification,
    classify_service,
    discover_endpoints,
)
from lucy.integrations.openclaw_gateway import (
    OpenClawGatewayClient,
    OpenClawGatewayError,
    get_gateway_client,
)
from lucy.integrations.resolver import ResolutionResult, resolve_integration, resolve_multiple
from lucy.integrations.wrapper_generator import discover_saved_wrappers

__all__ = [
    "CamoFoxClient",
    "ComposioClient",
    "IntegrationClassification",
    "OpenClawGatewayClient",
    "OpenClawGatewayError",
    "ResolutionResult",
    "classify_service",
    "discover_endpoints",
    "discover_saved_wrappers",
    "get_camofox_client",
    "get_composio_client",
    "get_gateway_client",
    "resolve_integration",
    "resolve_multiple",
]
