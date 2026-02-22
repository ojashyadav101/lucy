"""Memory synchronization module.

Responsible for writing facts from tasks/conversations into the memory layer.
"""

from __future__ import annotations

import asyncio
import structlog

from lucy.core.types import TaskContext
from lucy.memory.vector import get_vector_memory

logger = structlog.get_logger()


async def sync_task_to_memory(ctx: TaskContext, response_content: str) -> None:
    """Sync a completed task's interaction into memory.
    
    Extracts relevant facts and preferences from the conversation
    and stores them in Mem0.
    
    Args:
        ctx: The task context containing the original request.
        response_content: The final response sent to the user.
    """
    memory = get_vector_memory()
    if not memory.memory:
        logger.debug("memory_not_initialized_skipping_sync")
        return
        
    original_text = ctx.task.config.get("original_text", "") if ctx.task.config else ""
    if not original_text:
        return
        
    messages = [
        {"role": "user", "content": original_text},
        {"role": "assistant", "content": response_content}
    ]
    
    # Run the add operation in a separate thread since it is synchronous
    # and makes blocking HTTP calls to OpenAI for embedding/extraction.
    def _add_memory():
        return memory.add(
            content=messages,
            workspace_id=ctx.workspace.id,
            user_id=ctx.requester.id if ctx.requester else None,
            metadata={
                "task_id": str(ctx.task.id),
                "channel_id": ctx.slack_channel_id,
            }
        )
        
    async def _safe_add():
        try:
            await asyncio.to_thread(_add_memory)
            logger.info("task_memory_synced", task_id=str(ctx.task.id))
        except Exception as e:
            logger.error("task_memory_sync_failed", error=str(e), task_id=str(ctx.task.id))

    try:
        logger.info("syncing_task_to_memory", task_id=str(ctx.task.id))
        asyncio.create_task(_safe_add())
    except Exception as e:
        logger.error("task_memory_sync_schedule_failed", error=str(e), task_id=str(ctx.task.id))
