"""Integration tests for Composio Integrations.

These tests verify:
1. ComposioClient wrapping and async execution
2. IntegrationRegistry caching behavior
3. Toolset generation
4. Worker execution flow
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from lucy.integrations.composio_client import ComposioClient
from lucy.integrations.registry import IntegrationRegistry
from lucy.integrations.toolset import ComposioToolset
from lucy.integrations.worker import IntegrationWorker
from lucy.db.models import Integration


@pytest.fixture
def mock_composio_toolset():
    """Mock the underlying ComposioToolSet."""
    with patch("lucy.integrations.composio_client.ComposioToolSet") as mock:
        toolset_instance = MagicMock()
        mock.return_value = toolset_instance
        yield toolset_instance


class TestComposioClient:
    """Test Composio client wrapper."""

    @pytest.mark.asyncio
    async def test_get_tools_success(self, mock_composio_toolset):
        """Client gets tools asynchronously."""
        mock_composio_toolset.get_tools.return_value = [{"name": "GITHUB_ISSUE"}]
        
        client = ComposioClient(api_key="test-key")
        
        # Override to_thread for test determinism
        with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
            mock_thread.return_value = [{"name": "GITHUB_ISSUE"}]
            tools = await client.get_tools(apps=["GITHUB"])
            
            assert len(tools) == 1
            assert tools[0]["name"] == "GITHUB_ISSUE"

    @pytest.mark.asyncio
    async def test_execute_action_success(self, mock_composio_toolset):
        """Client executes action asynchronously."""
        client = ComposioClient(api_key="test-key")
        
        with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
            mock_thread.return_value = {"success": True, "id": "123"}
            
            result = await client.execute_action(
                action="GITHUB_CREATE_ISSUE",
                params={"title": "Test"},
                entity_id="user-123"
            )
            
            assert "result" in result
            assert result["result"]["success"] is True


class TestIntegrationRegistry:
    """Test IntegrationRegistry caching and DB sync."""

    @pytest.mark.asyncio
    async def test_cache_hit_returns_early(self):
        """Registry returns cached providers without DB query."""
        registry = IntegrationRegistry()
        ws_id = uuid.uuid4()
        
        # Populate cache
        registry._cache[str(ws_id)] = {
            "connections": ["github"],
            "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5)
        }
        
        providers = await registry.get_active_providers(ws_id)
        assert providers == ["github"]

    @pytest.mark.asyncio
    async def test_cache_miss_queries_db(self):
        """Registry queries DB on cache miss."""
        registry = IntegrationRegistry()
        ws_id = uuid.uuid4()
        
        # Mock DB session
        mock_db_session = AsyncMock()
        mock_result = MagicMock()
        
        # Create mock integration model
        mock_integration = MagicMock(spec=Integration)
        mock_integration.provider = "linear"
        mock_result.scalars().all.return_value = [mock_integration]
        mock_db_session.execute.return_value = mock_result
        
        with patch("lucy.integrations.registry.AsyncSessionLocal", return_value=mock_db_session) as mock_session_maker:
            mock_session_maker.return_value.__aenter__.return_value = mock_db_session
            
            providers = await registry.get_active_providers(ws_id)
            
            assert providers == ["linear"]
            assert str(ws_id) in registry._cache


class TestComposioToolset:
    """Test toolset generation."""

    @pytest.mark.asyncio
    async def test_get_workspace_tools(self):
        """Toolset gathers schemas for active workspace providers."""
        toolset = ComposioToolset()
        ws_id = uuid.uuid4()
        
        # Mock registry to return providers
        mock_registry = AsyncMock()
        mock_registry.get_active_providers.return_value = ["github"]
        
        # Mock client to return tool schemas
        mock_client = AsyncMock()
        mock_client.get_tools.return_value = [{"name": "GITHUB_TOOL"}]
        
        with patch("lucy.integrations.toolset.get_integration_registry", return_value=mock_registry), \
             patch("lucy.integrations.toolset.get_composio_client", return_value=mock_client):
             
            tools = await toolset.get_workspace_tools(ws_id)
            
            assert len(tools) == 1
            assert tools[0]["name"] == "GITHUB_TOOL"
            mock_client.get_tools.assert_called_once_with(apps=["GITHUB"])


class TestIntegrationWorker:
    """Test integration worker execution."""

    @pytest.mark.asyncio
    async def test_worker_execute(self):
        """Worker executes action via client."""
        worker = IntegrationWorker()
        ws_id = uuid.uuid4()
        
        mock_client = AsyncMock()
        mock_client.execute_action.return_value = {"result": "success"}
        
        with patch("lucy.integrations.worker.get_composio_client", return_value=mock_client):
            result = await worker.execute(
                action="TEST_ACTION",
                parameters={"key": "val"},
                workspace_id=ws_id,
            )
            
            assert result == {"result": "success"}
            mock_client.execute_action.assert_called_once_with(
                action="TEST_ACTION",
                params={"key": "val"},
                entity_id=str(ws_id)
            )