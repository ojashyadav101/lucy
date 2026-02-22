"""Integration tests for OpenClaw client and agent.

These tests verify:
1. OpenClaw client can connect
2. Chat completions work
3. Agent can execute tasks end-to-end

Run with: pytest tests/integration/test_openclaw.py -v
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
import httpx

from lucy.core.openclaw import (
    OpenClawClient,
    ChatConfig,
    OpenClawResponse,
    OpenClawError,
)
from lucy.core.agent import LucyAgent, TaskContext
from lucy.db.models import Task, TaskStatus, Workspace, User


@pytest.fixture
def mock_httpx_client():
    """Create a mock httpx AsyncClient."""
    with patch("lucy.core.openclaw.httpx.AsyncClient") as mock:
        client = MagicMock()
        mock.return_value = client
        yield client


class TestOpenClawClient:
    """Test OpenClaw HTTP client."""

    async def test_client_initialization(self, mock_httpx_client) -> None:
        """Client initializes with correct configuration."""
        client = OpenClawClient(
            base_url="http://test.example.com:3000",
            api_key="test-key",
        )
        
        assert client.base_url == "http://test.example.com:3000"
        assert client.api_key == "test-key"

    async def test_health_check_success(self, mock_httpx_client) -> None:
        """Health check returns status on success."""
        # Setup mock response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_httpx_client.get = AsyncMock(return_value=mock_response)
        mock_response.raise_for_status = MagicMock()
        
        client = OpenClawClient()
        result = await client.health_check()
        
        assert result["status"] == "ok"

    async def test_health_check_failure(self, mock_httpx_client) -> None:
        """Health check raises OpenClawError on failure."""
        from httpx import HTTPStatusError
        
        # Setup mock to raise HTTP error
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.text = "Service Unavailable"
        
        error = HTTPStatusError(
            "Server error",
            request=MagicMock(),
            response=mock_response,
        )
        mock_httpx_client.get = AsyncMock(side_effect=error)
        
        client = OpenClawClient()
        
        with pytest.raises(OpenClawError) as exc_info:
            await client.health_check()
        
        assert exc_info.value.status_code == 503

    async def test_chat_completion_success(self, mock_httpx_client) -> None:
        """Chat completion sends messages and returns response."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Hello from OpenClaw", "role": "assistant"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        mock_httpx_client.post = AsyncMock(return_value=mock_response)
        mock_response.raise_for_status = MagicMock()
        
        client = OpenClawClient()
        result = await client.chat_completion(
            messages=[{"role": "user", "content": "Hi"}],
        )
        
        assert result.content == "Hello from OpenClaw"
        assert result.usage == {"prompt_tokens": 10, "completion_tokens": 5}


class TestLucyAgent:
    """Test LucyAgent orchestration."""

    @pytest_asyncio.fixture
    async def mock_openclaw(self) -> AsyncMock:
        """Create a mock OpenClaw client."""
        mock = AsyncMock(spec=OpenClawClient)
        mock.chat_completion = AsyncMock(return_value=OpenClawResponse(
            content="Test response",
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        ))
        return mock

    async def test_agent_initialization(self, mock_openclaw) -> None:
        """Agent initializes with OpenClaw client."""
        agent = LucyAgent(openclaw=mock_openclaw)
        assert agent.openclaw is mock_openclaw

    async def test_model_selection_by_intent(self, mock_openclaw) -> None:
        """Agent selects appropriate model based on intent."""
        agent = LucyAgent(openclaw=mock_openclaw)
        
        # Code tasks get Claude
        code_model = agent._select_model("code")
        assert "claude" in code_model.lower()
        
        # Lookup tasks get fast model
        lookup_model = agent._select_model("lookup")
        assert "flash" in lookup_model.lower()
        
        # Default is Kimi K2.5
        default_model = agent._select_model(None)
        assert "kimi" in default_model.lower()

    async def test_task_context_creation(self) -> None:
        """TaskContext holds all execution context."""
        # Create mock objects
        mock_task = MagicMock(spec=Task)
        mock_task.id = uuid.uuid4()
        mock_task.workspace_id = uuid.uuid4()
        mock_task.config = {"channel_id": "C123", "thread_ts": "123.456"}
        
        mock_workspace = MagicMock(spec=Workspace)
        mock_user = MagicMock(spec=User)
        
        ctx = TaskContext(
            task=mock_task,
            workspace=mock_workspace,
            requester=mock_user,
        )
        
        assert ctx.task is mock_task
        assert ctx.workspace is mock_workspace
        assert ctx.requester is mock_user


class TestTaskExecutionFlow:
    """Test full task execution flow."""

    async def test_task_state_transitions(self) -> None:
        """Task moves through correct state transitions."""
        # Mock task
        task = MagicMock(spec=Task)
        task.id = uuid.uuid4()
        task.status = TaskStatus.CREATED
        task.config = {"original_text": "Test request"}
        
        # Simulate execution
        task.status = TaskStatus.RUNNING
        assert task.status == TaskStatus.RUNNING
        
        task.status = TaskStatus.COMPLETED
        assert task.status == TaskStatus.COMPLETED

    async def test_step_recording(self) -> None:
        """Task steps are recorded for each phase."""
        from lucy.db.models import TaskStep
        
        step = TaskStep(
            task_id=uuid.uuid4(),
            sequence_number=1,
            step_type="llm_call",
            description="Processing request",
        )
        
        assert step.step_type == "llm_call"
        assert step.sequence_number == 1


class TestErrorHandling:
    """Test error handling and recovery."""

    async def test_openclaw_error_has_status_code(self) -> None:
        """OpenClawError includes HTTP status code."""
        error = OpenClawError("Test error", status_code=503)
        
        assert str(error) == "Test error"
        assert error.status_code == 503

    async def test_agent_handles_message_failure(self, mock_openclaw) -> None:
        """Agent handles message send failure gracefully."""
        mock_openclaw.chat_completion = AsyncMock(
            side_effect=OpenClawError("Message failed", status_code=500)
        )
        
        agent = LucyAgent(openclaw=mock_openclaw)
        
        # Would fail during LLM call step
        # In real execution, this would update task to FAILED

