"""Integration registry and caching layer.

Manages connections and capabilities per workspace.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List
import structlog
from datetime import datetime, timezone, timedelta
from uuid import UUID

from sqlalchemy import select
from lucy.db.session import AsyncSessionLocal
from lucy.db.models import Integration
from lucy.integrations.composio_client import get_composio_client

logger = structlog.get_logger()


class IntegrationRegistry:
    """Registry for workspace integrations and connections.
    
    Includes a TTL cache for active connections to avoid hitting DB/API
    for every tool execution.
    """

    def __init__(self, cache_ttl_seconds: int = 300) -> None:
        """Initialize registry.
        
        Args:
            cache_ttl_seconds: Cache duration for active connections.
        """
        self.cache_ttl = timedelta(seconds=cache_ttl_seconds)
        # {workspace_id: {"connections": [...], "expires_at": datetime}}
        self._cache: Dict[str, Dict[str, Any]] = {}

    async def get_active_providers(self, workspace_id: UUID) -> List[str]:
        """Get a list of active integration providers for a workspace.
        
        Args:
            workspace_id: Workspace identifier.
            
        Returns:
            List of provider names (e.g. ['github', 'linear']).
        """
        # 1. Check cache
        cache_key = str(workspace_id)
        now = datetime.now(timezone.utc)
        
        if cache_key in self._cache:
            entry = self._cache[cache_key]
            if entry["expires_at"] > now:
                logger.debug("integration_registry_cache_hit", workspace_id=cache_key)
                return entry["connections"]

        # 2. Cache miss, check DB
        logger.debug("integration_registry_cache_miss", workspace_id=cache_key)
        
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Integration).where(
                    Integration.workspace_id == workspace_id,
                    Integration.status == "active"
                )
            )
            integrations = result.scalars().all()
            
            providers = [i.provider for i in integrations]
            
            # Update cache
            self._cache[cache_key] = {
                "connections": providers,
                "expires_at": now + self.cache_ttl
            }
            
            return providers

    async def sync_workspace_connections(self, workspace_id: UUID) -> None:
        """Sync actual connections from Composio to local DB for a workspace.

        Also triggers BM25 index population for any newly connected apps
        so tools are immediately available for retrieval.
        """
        client = get_composio_client()
        connections = await client.get_entity_connections(entity_id=str(workspace_id))

        active_apps = [c["app"].lower() for c in connections if c.get("status") == "ACTIVE"]

        newly_connected: list[str] = []

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Integration).where(Integration.workspace_id == workspace_id)
            )
            current_integrations = {i.provider: i for i in result.scalars().all()}

            for app in active_apps:
                if app in current_integrations:
                    current = current_integrations[app]
                    if current.status != "active":
                        current.status = "active"
                        current.updated_at = datetime.now(timezone.utc)
                        newly_connected.append(app)
                else:
                    new_integration = Integration(
                        workspace_id=workspace_id,
                        provider=app,
                        status="active"
                    )
                    db.add(new_integration)
                    newly_connected.append(app)

            for provider, integration in current_integrations.items():
                if provider not in active_apps and integration.status == "active":
                    integration.status = "inactive"
                    integration.updated_at = datetime.now(timezone.utc)

            await db.commit()
            await self.invalidate_cache(workspace_id)

        logger.info("workspace_connections_synced", workspace_id=str(workspace_id), count=len(active_apps))

        if newly_connected:
            try:
                from lucy.retrieval.tool_retriever import get_retriever
                retriever = get_retriever()
                total = await retriever.populate(workspace_id, set(newly_connected))
                logger.info(
                    "bm25_index_populated_on_connect",
                    workspace_id=str(workspace_id),
                    new_apps=newly_connected,
                    total_indexed=total,
                )
            except Exception as e:
                logger.warning("bm25_index_populate_on_connect_failed", error=str(e))

    async def get_connection_url(self, workspace_id: UUID, provider: str) -> str | None:
        """Get an OAuth connection URL for a provider.
        
        Args:
            workspace_id: Workspace identifier.
            provider: Provider name (e.g. 'github').
            
        Returns:
            OAuth URL or None.
        """
        client = get_composio_client()
        return await client.create_connection_link(entity_id=str(workspace_id), app=provider)

    async def invalidate_cache(self, workspace_id: UUID) -> None:
        """Invalidate provider cache for a workspace."""
        cache_key = str(workspace_id)
        if cache_key in self._cache:
            del self._cache[cache_key]
        logger.debug("integration_registry_cache_invalidated", workspace_id=cache_key)


# Singleton
_registry: IntegrationRegistry | None = None

def get_integration_registry() -> IntegrationRegistry:
    """Get singleton IntegrationRegistry."""
    global _registry
    if _registry is None:
        _registry = IntegrationRegistry()
    return _registry
