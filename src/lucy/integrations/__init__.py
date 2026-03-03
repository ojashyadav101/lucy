"""Integration layer: Composio meta-tools, dynamic resolution, MCP, and gateway."""

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
from lucy.integrations.openclaw_gateway import (
    OpenClawGatewayClient,
    OpenClawGatewayError,
    get_gateway_client,
)
from lucy.integrations.resolver import ResolutionResult, resolve_integration, resolve_multiple
from lucy.integrations.wrapper_generator import discover_saved_wrappers

__all__ = [
    "ComposioClient",
    "IntegrationClassification",
    "MCPDiscoveryResult",
    "OpenClawGatewayClient",
    "OpenClawGatewayError",
    "ResolutionResult",
    "classify_service",
    "connect_and_discover",
    "discover_endpoints",
    "discover_saved_wrappers",
    "get_composio_client",
    "get_gateway_client",
    "mcp_call_tool",
    "mcp_tools_to_openai",
    "parse_mcp_tool_name",
    "resolve_integration",
    "resolve_multiple",
]
