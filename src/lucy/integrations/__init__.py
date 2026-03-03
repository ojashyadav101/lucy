"""Integration layer: Composio meta-tools, CamoFox browser, dynamic resolution, MCP, and OAuth backend."""

from lucy.integrations.camofox import CamoFoxClient, get_camofox_client
from lucy.integrations.composio_client import ComposioClient, get_composio_client
from lucy.integrations.grounded_search import (
    IntegrationClassification,
    classify_service,
    discover_endpoints,
)
from lucy.integrations.mcp_client import (
    MCPDiscoveryResult,
    connect_and_discover,
    mcp_tools_to_openai,
    parse_mcp_tool_name,
)
from lucy.integrations.mcp_client import (
    call_tool as mcp_call_tool,
)
from lucy.integrations.oauth_backend import (
    ComposioBackend,
    OAuthBackend,
    get_oauth_backend,
    set_oauth_backend,
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
    "ComposioBackend",
    "ComposioClient",
    "IntegrationClassification",
    "MCPDiscoveryResult",
    "OAuthBackend",
    "OpenClawGatewayClient",
    "OpenClawGatewayError",
    "ResolutionResult",
    "call_tool",
    "classify_service",
    "connect_and_discover",
    "discover_endpoints",
    "discover_saved_wrappers",
    "get_camofox_client",
    "get_composio_client",
    "get_gateway_client",
    "get_oauth_backend",
    "mcp_call_tool",
    "mcp_tools_to_openai",
    "parse_mcp_tool_name",
    "resolve_integration",
    "resolve_multiple",
    "set_oauth_backend",
]
