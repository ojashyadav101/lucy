"""Dynamic tool schema builder.

Retrieves and formats OpenAPI schemas for available integrations 
into LLM-compatible tool definitions.
"""

from __future__ import annotations

import structlog
from typing import Any, List, Dict
from uuid import UUID

from lucy.integrations.registry import get_integration_registry
from lucy.integrations.composio_client import get_composio_client

logger = structlog.get_logger()


class ComposioToolset:
    """Builder for runtime tool schemas based on active connections."""
    
    async def get_workspace_tools(self, workspace_id: UUID) -> List[Dict[str, Any]]:
        """Get all tool schemas available to a specific workspace.
        
        Args:
            workspace_id: The workspace to retrieve tools for.
            
        Returns:
            List of tool schema dicts in OpenAI format.
        """
        registry = get_integration_registry()
        active_providers = await registry.get_active_providers(workspace_id)
        
        if not active_providers:
            return []
            
        client = get_composio_client()
        
        try:
            tools = await client.get_tools(
                user_id=str(workspace_id),
                apps=[p.lower() for p in active_providers],
            )
            logger.info("workspace_tools_built", workspace_id=str(workspace_id), tool_count=len(tools))
            return tools
        except Exception as e:
            logger.error("workspace_tools_build_failed", workspace_id=str(workspace_id), error=str(e))
            return []

    async def get_action_tools(self, actions: List[str], user_id: str = "default") -> List[Dict[str, Any]]:
        """Get tool schemas for specific actions.
        
        Args:
            actions: List of action slugs (e.g. ['GITHUB_CREATE_ISSUE']).
            user_id: Composio user/entity identifier.
            
        Returns:
            List of tool schema dicts.
        """
        if not actions:
            return []
            
        client = get_composio_client()
        try:
            tools = await client.get_tools(
                user_id=user_id,
                actions=[a.upper() for a in actions],
            )
            return tools
        except Exception as e:
            logger.error("action_tools_build_failed", error=str(e))
            return []


# Singleton
_toolset: ComposioToolset | None = None

def get_toolset() -> ComposioToolset:
    """Get singleton ComposioToolset."""
    global _toolset
    if _toolset is None:
        _toolset = ComposioToolset()
    return _toolset
