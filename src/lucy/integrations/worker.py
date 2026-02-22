"""Integration worker for tool execution.

Manages running multi-step actions across various integrations
using the Composio SDK.
"""

from __future__ import annotations

import structlog
from typing import Any, Dict
from uuid import UUID

from lucy.integrations.composio_client import get_composio_client

logger = structlog.get_logger()


class IntegrationWorker:
    """Worker for executing integration tools safely.
    
    In V1, it executes a single action. Later can be extended
    to rollback or multi-step execution.
    """

    async def execute(self, action: str, parameters: dict[str, Any], workspace_id: UUID | None = None) -> dict[str, Any]:
        """Execute a single tool action.
        
        Args:
            action: Action name (e.g. 'GITHUB_CREATE_ISSUE')
            parameters: Action parameters
            workspace_id: For authentication/scoping
            
        Returns:
            Dict containing result or error.
        """
        client = get_composio_client()
        entity_id = str(workspace_id) if workspace_id else None
        
        logger.info("integration_worker_executing", action=action, workspace_id=entity_id)
        
        try:
            result = await client.execute_action(
                action=action,
                params=parameters,
                entity_id=entity_id,
            )
            
            if "error" in result:
                logger.error("integration_worker_failed", action=action, error=result["error"])
                
            return result
        except Exception as e:
            logger.error("integration_worker_crash", action=action, error=str(e))
            return {"error": str(e)}


# Singleton
_worker: IntegrationWorker | None = None

def get_worker() -> IntegrationWorker:
    """Get singleton IntegrationWorker."""
    global _worker
    if _worker is None:
        _worker = IntegrationWorker()
    return _worker
