"""Integration tests for memory module.

These tests verify:
1. Mem0 + Qdrant client initializes
2. Memories can be added
3. Memories can be searched
4. Task sync works correctly
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio

from lucy.core.agent import TaskContext
from lucy.db.models import Task, Workspace, User
from lucy.memory.vector import VectorMemory
from lucy.memory.sync import sync_task_to_memory


@pytest.fixture
def mock_mem0():
    """Mock the Mem0 library."""
    with patch("lucy.memory.vector.Memory") as mock:
        memory_instance = MagicMock()
        mock.from_config.return_value = memory_instance
        yield memory_instance


class TestVectorMemory:
    """Test VectorMemory implementation."""

    def test_initialization(self, mock_mem0) -> None:
        """VectorMemory initializes with correct config."""
        memory = VectorMemory()
        
        assert memory.memory is mock_mem0
        mock_mem0.__class__.from_config.assert_called_once()
        
        # Verify config structure
        args, kwargs = mock_mem0.__class__.from_config.call_args
        config = args[0]
        assert config["vector_store"]["provider"] == "qdrant"
        assert config["llm"]["provider"] == "openai"

    def test_add_memory(self, mock_mem0) -> None:
        """Adding a memory calls underlying memory store correctly."""
        memory = VectorMemory()
        memory.memory = mock_mem0
        
        mock_mem0.add.return_value = [{"id": "mem-1"}]
        
        workspace_id = uuid.uuid4()
        user_id = uuid.uuid4()
        
        result = memory.add(
            content="User prefers email",
            workspace_id=workspace_id,
            user_id=user_id,
            metadata={"source": "slack"}
        )
        
        assert result == [{"id": "mem-1"}]
        mock_mem0.add.assert_called_once_with(
            messages="User prefers email",
            user_id=str(user_id),
            agent_id=str(workspace_id),
            metadata={"source": "slack"}
        )

    def test_search_memory(self, mock_mem0) -> None:
        """Searching memory calls underlying store correctly."""
        memory = VectorMemory()
        memory.memory = mock_mem0
        
        mock_mem0.search.return_value = [{"memory": "User prefers email"}]
        
        workspace_id = uuid.uuid4()
        user_id = uuid.uuid4()
        
        result = memory.search(
            query="contact preference",
            workspace_id=workspace_id,
            user_id=user_id,
            limit=2
        )
        
        assert len(result) == 1
        assert result[0]["memory"] == "User prefers email"
        mock_mem0.search.assert_called_once_with(
            query="contact preference",
            user_id=str(user_id),
            agent_id=str(workspace_id),
            limit=2
        )


class TestMemorySync:
    """Test memory synchronization from tasks."""

    @pytest_asyncio.fixture
    async def mock_vector_memory(self):
        """Mock the VectorMemory singleton."""
        with patch("lucy.memory.sync.get_vector_memory") as mock_get:
            memory = MagicMock()
            memory.memory = MagicMock()  # Underlying Mem0
            mock_get.return_value = memory
            yield memory

    @pytest.mark.asyncio
    async def test_sync_task_to_memory(self, mock_vector_memory) -> None:
        """Task interaction is synchronized to memory asynchronously."""
        # Create mock context
        mock_task = MagicMock(spec=Task)
        mock_task.id = uuid.uuid4()
        mock_task.config = {"original_text": "Remember that my favorite color is blue."}
        
        mock_workspace = MagicMock(spec=Workspace)
        mock_workspace.id = uuid.uuid4()
        
        mock_user = MagicMock(spec=User)
        mock_user.id = uuid.uuid4()
        
        ctx = TaskContext(
            task=mock_task,
            workspace=mock_workspace,
            requester=mock_user,
            slack_channel_id="C123"
        )
        
        # We need to patch asyncio.create_task to await it immediately for testing
        with patch("lucy.memory.sync.asyncio.create_task") as mock_create_task:
            await sync_task_to_memory(ctx, "I will remember that your favorite color is blue.")
            
            # The function creates a task that runs the add operation
            # Let's extract the target function and call it directly to test logic
            assert mock_create_task.called
            args, _ = mock_create_task.call_args
            
            # The arg is an awaitable created by asyncio.to_thread
            # In a real test, we might want to let the event loop run or mock to_thread
            
        # Instead of dealing with the complex task awaiting, let's test the inner logic directly
        # by extracting and running the _add_memory closure
        with patch("lucy.memory.sync.asyncio.create_task") as mock_create_task:
            with patch("lucy.memory.sync.asyncio.to_thread") as mock_to_thread:
                await sync_task_to_memory(ctx, "I will remember that your favorite color is blue.")
                
                # Extract the closure passed to to_thread
                args, _ = mock_to_thread.call_args
                closure = args[0]
                
                # Execute it
                closure()
                
                # Verify memory.add was called correctly
                mock_vector_memory.add.assert_called_once()
                call_args, call_kwargs = mock_vector_memory.add.call_args
                
                assert call_kwargs["workspace_id"] == mock_workspace.id
                assert call_kwargs["user_id"] == mock_user.id
                assert call_kwargs["metadata"]["task_id"] == str(mock_task.id)
                assert call_kwargs["metadata"]["channel_id"] == "C123"
                
                # Verify content has both user and assistant messages
                content = call_kwargs["content"]
                assert len(content) == 2
                assert content[0]["role"] == "user"
                assert content[0]["content"] == "Remember that my favorite color is blue."
                assert content[1]["role"] == "assistant"
                assert content[1]["content"] == "I will remember that your favorite color is blue."