"""Cost tracking and logging system."""

from __future__ import annotations

import structlog
from typing import Any
from litellm import model_cost, completion_cost

from lucy.db.models import CostLog
from lucy.db.session import AsyncSessionLocal

logger = structlog.get_logger()


async def log_cost(
    workspace_id: str,
    model: str,
    usage: Any,
    task_id: str | None = None,
) -> None:
    """Log the cost of an LLM completion to the database.
    
    Args:
        workspace_id: Workspace ID for billing
        model: The model identifier used
        usage: The usage object from the LLM response
        task_id: Optional task ID this cost belongs to
    """
    try:
        # Calculate cost using litellm
        # Litellm model_cost dict handles openrouter pricing correctly
        cost_usd = 0.0
        
        try:
            # We mock the response object shape litellm expects for cost calculation
            class MockResponse:
                def __init__(self, model_name, usage_obj):
                    self.model = model_name
                    self.usage = usage_obj
            
            mock_res = MockResponse(model, usage)
            cost_usd = completion_cost(completion_response=mock_res)
        except Exception as e:
            logger.debug("litellm_cost_calc_failed", model=model, error=str(e))
            # Fallback estimation if not in litellm dict
            prompt_tokens = getattr(usage, "prompt_tokens", 0)
            completion_tokens = getattr(usage, "completion_tokens", 0)
            
            # Rough fallback estimates
            if "kimi" in model:
                cost_usd = (prompt_tokens * 1.0 + completion_tokens * 1.0) / 1_000_000
            elif "claude-3-5" in model:
                cost_usd = (prompt_tokens * 3.0 + completion_tokens * 15.0) / 1_000_000
            elif "mini" in model or "flash" in model:
                cost_usd = (prompt_tokens * 0.15 + completion_tokens * 0.60) / 1_000_000

        # Don't log if cost is 0 and tokens are 0
        if cost_usd == 0 and getattr(usage, "total_tokens", 0) == 0:
            return

        # Write to database
        async with AsyncSessionLocal() as db:
            log_entry = CostLog(
                workspace_id=workspace_id,
                task_id=task_id,
                component="llm_router",  # Required field
                model=model,
                input_tokens=getattr(usage, "prompt_tokens", usage.get("prompt_tokens", 0) if isinstance(usage, dict) else 0),
                output_tokens=getattr(usage, "completion_tokens", usage.get("completion_tokens", 0) if isinstance(usage, dict) else 0),
                cost_usd=cost_usd,
            )
            db.add(log_entry)
            await db.commit()
            
    except Exception as e:
        logger.error(
            "cost_logging_failed",
            workspace_id=workspace_id,
            model=model,
            error=str(e),
        )
