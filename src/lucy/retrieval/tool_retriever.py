"""TopKRetriever — retrieval-based tool selection for Lucy.

Two-phase process:

Phase A — Index population (on integration connect, or when stale)
  1. Fetch tool schemas for all connected apps from Composio.
  2. Register them into the workspace's BM25 CapabilityIndex with app_slug.

Phase B — Top-K selection (every request, < 1 ms)
  1. Score every indexed tool against the user query with BM25.
  2. Return top-K schemas with scores for threshold-based decisions.

The retriever is a latency optimization. When it finds strong matches
(top_score > MIN_RELEVANCE_SCORE), the agent can skip Composio's
SEARCH_TOOLS meta-tool and go straight to direct execution.
When it doesn't match, the agent falls back to meta-tools.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import structlog

from lucy.retrieval.capability_index import (
    CapabilityIndex,
    RetrievalResult,
    MIN_INDEXED_TOOLS,
    get_capability_index,
)

logger = structlog.get_logger()

INITIAL_K: int = 15
EXPANDED_K: int = 30

# BM25 score below which we consider "no strong match found" and
# the agent should fall back to Composio's SEARCH_TOOLS meta-tool.
MIN_RELEVANCE_SCORE: float = 0.5


class TopKRetriever:
    """Retrieve the K most relevant tool schemas for a user query."""

    def __init__(self, index: CapabilityIndex | None = None) -> None:
        self._index = index or get_capability_index()
        self._populate_locks: dict[str, asyncio.Lock] = {}

    async def retrieve(
        self,
        workspace_id: UUID,
        query: str,
        connected_apps: set[str] | None = None,
        k: int = INITIAL_K,
    ) -> RetrievalResult | None:
        """Return top-K tool schemas with scores, or None if index not ready.

        Returns None when the index is too small (signals fallback to meta-tools).
        Returns a RetrievalResult with tools and top_score otherwise.
        """
        import time as _t
        from lucy.observability.metrics import get_metrics

        ws_id = str(workspace_id)
        workspace_index = self._index.get(ws_id)

        if workspace_index.is_stale or workspace_index.size < MIN_INDEXED_TOOLS:
            await self._populate(ws_id, connected_apps or set())

        if workspace_index.size < MIN_INDEXED_TOOLS:
            logger.info(
                "retrieval_index_too_small",
                workspace_id=ws_id,
                indexed=workspace_index.size,
                min_required=MIN_INDEXED_TOOLS,
            )
            return None

        _t0 = _t.monotonic()
        result = workspace_index.retrieve(
            query=query,
            k=k,
            connected_apps=connected_apps,
        )
        _retrieval_ms = (_t.monotonic() - _t0) * 1000

        asyncio.create_task(
            get_metrics().record("tool_retrieval_latency_ms", _retrieval_ms)
        )

        logger.info(
            "retrieval_top_k_selected",
            workspace_id=ws_id,
            query_preview=query[:60],
            k=k,
            returned=len(result.tools),
            top_score=round(result.top_score, 2),
            retrieval_ms=round(_retrieval_ms, 2),
            tool_names=[t.get("function", {}).get("name", "?") for t in result.tools],
        )
        return result

    def record_tool_usage(self, workspace_id: UUID, tool_name: str) -> None:
        """Increment the usage counter for a tool after successful execution."""
        ws_id = str(workspace_id)
        workspace_index = self._index.get(ws_id)
        workspace_index.record_usage(tool_name)

    async def populate(self, workspace_id: UUID, connected_apps: set[str]) -> int:
        """Force-populate the capability index for a workspace.

        Called when a new integration is connected.
        Returns the total number of indexed tools after population.
        """
        ws_id = str(workspace_id)
        await self._populate(ws_id, connected_apps)
        return self._index.get(ws_id).size

    async def invalidate(self, workspace_id: UUID) -> None:
        """Drop the index for a workspace."""
        await self._index.invalidate(str(workspace_id))

    async def _populate(self, ws_id: str, connected_apps: set[str]) -> int:
        """Fetch tool schemas from Composio and index them.

        Uses a per-workspace lock so concurrent calls don't double-fetch.
        Returns the number of newly added tools.
        """
        if ws_id not in self._populate_locks:
            self._populate_locks[ws_id] = asyncio.Lock()
        lock = self._populate_locks[ws_id]

        if lock.locked():
            return 0

        async with lock:
            apps_list = list(connected_apps) if connected_apps else []
            if not apps_list:
                return 0

            from lucy.integrations.composio_client import get_composio_client
            client = get_composio_client()

            try:
                app_schema_pairs = await client.fetch_app_tool_schemas(
                    user_id=ws_id,
                    apps=apps_list,
                )
            except Exception as e:
                logger.warning("capability_index_populate_failed", workspace_id=ws_id, error=str(e))
                return 0

            workspace_index = self._index.get(ws_id)
            total_added = 0
            total_schemas = 0
            for app_slug, schemas in app_schema_pairs:
                total_schemas += len(schemas)
                added = await workspace_index.add_tools(schemas, app_slug=app_slug)
                total_added += added

            if total_added > 0:
                logger.info(
                    "capability_index_populated",
                    workspace_id=ws_id,
                    schemas_fetched=total_schemas,
                    new_tools_indexed=total_added,
                    total_indexed=workspace_index.size,
                )
            return total_added


_retriever: TopKRetriever | None = None


def get_retriever() -> TopKRetriever:
    global _retriever
    if _retriever is None:
        _retriever = TopKRetriever()
    return _retriever
