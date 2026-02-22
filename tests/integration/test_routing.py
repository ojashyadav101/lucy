"""Integration tests for Model Router and Task Classification."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from litellm import ModelResponse

from lucy.routing.tiers import ModelTier
from lucy.routing.classifier import TaskClassifier
from lucy.routing.router import ModelRouter


@pytest.fixture
def mock_litellm():
    """Mock LiteLLM acompletion."""
    with patch("lucy.routing.classifier.acompletion") as mock_class_comp, \
         patch("lucy.routing.router.acompletion") as mock_router_comp:
         
        # Mock classifier response
        class_response = MagicMock(spec=ModelResponse)
        class_response.choices = [MagicMock()]
        class_response.choices[0].message.content = "TIER=3\nINTENT=code"
        mock_class_comp.return_value = class_response
        
        # Mock router response
        router_response = MagicMock(spec=ModelResponse)
        router_response.choices = [MagicMock()]
        router_response.choices[0].message.content = "I am routing."
        router_response.choices[0].message.tool_calls = None
        router_response.usage = MagicMock()
        mock_router_comp.return_value = router_response
        
        yield mock_class_comp, mock_router_comp


@pytest.mark.asyncio
async def test_classifier_fast_path() -> None:
    """Classifier uses regex fast-paths to avoid LLM calls."""
    classifier = TaskClassifier()
    
    # Very short
    res_short = await classifier.classify("hi")
    assert res_short.tier == ModelTier.TIER_1_FAST
    
    # Code keyword
    res_code = await classifier.classify("Please refactor this Python script")
    assert res_code.tier == ModelTier.TIER_3_FRONTIER
    assert res_code.intent == "code"


@pytest.mark.asyncio
async def test_classifier_llm_path(mock_litellm) -> None:
    """Classifier falls back to LLM for complex queries."""
    mock_class_comp, _ = mock_litellm
    
    classifier = TaskClassifier()
    res = await classifier.classify("I need you to build a complex system from scratch that integrates with five APIs")
    
    assert mock_class_comp.called
    assert res.tier == ModelTier.TIER_3_FRONTIER
    assert res.intent == "code"


@pytest.mark.asyncio
async def test_router_success(mock_litellm) -> None:
    """Router successfully calls litellm and logs cost."""
    _, mock_router_comp = mock_litellm
    
    with patch("lucy.routing.router.log_cost") as mock_log_cost:
        router = ModelRouter()
        
        res = await router.route(
            messages=[{"role": "user", "content": "hello"}],
            tier=ModelTier.TIER_2_STANDARD,
            workspace_id="test-workspace",
            task_id="test-task",
        )
        
        assert mock_router_comp.called
        assert res.choices[0].message.content == "I am routing."
        
        # Ensure log_cost was scheduled
        import asyncio
        await asyncio.sleep(0.01)  # give event loop time to run create_task
        assert mock_log_cost.called


@pytest.mark.asyncio
async def test_router_fallback(mock_litellm) -> None:
    """Router falls back to next model if primary fails."""
    _, mock_router_comp = mock_litellm
    
    # Make the first call fail, second succeed
    mock_router_comp.side_effect = [
        Exception("API down"),
        MagicMock(choices=[MagicMock(message=MagicMock(content="Fallback success", tool_calls=None))], usage=MagicMock())
    ]
    
    router = ModelRouter()
    
    res = await router.route(
        messages=[{"role": "user", "content": "hello"}],
        tier=ModelTier.TIER_2_STANDARD,
    )
    
    assert mock_router_comp.call_count == 2
    assert res.choices[0].message.content == "Fallback success"
