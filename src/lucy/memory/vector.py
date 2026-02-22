"""Vector memory layer using Mem0 + Qdrant.

Provides a unified interface for storing and retrieving facts 
scoped to a specific workspace.
"""

from __future__ import annotations

import os
from typing import Any, List
from uuid import UUID

import structlog
from mem0 import Memory

from lucy.config import settings

logger = structlog.get_logger()


class VectorMemory:
    """Vector memory layer using Mem0 + Qdrant."""

    def __init__(self) -> None:
        """Initialize Mem0 client with Qdrant vector store."""
        from urllib.parse import urlparse

        # Determine embedding provider (OpenAI or OpenRouter)
        use_openrouter = settings.embedding_provider.lower() == "openrouter"

        if use_openrouter:
            api_key = settings.openrouter_api_key or os.environ.get("OPENROUTER_API_KEY", "")
            embedding_model = "openai/text-embedding-3-small"
            embedding_api_base = "https://openrouter.ai/api/v1"
            provider_name = "openrouter"
        else:
            api_key = settings.openai_api_key or os.environ.get("OPENAI_API_KEY", "")
            embedding_model = "text-embedding-3-small"
            embedding_api_base = None  # Default OpenAI endpoint
            provider_name = "openai"

        if not api_key:
            logger.warning(
                "vector_memory_disabled",
                reason=f"No {provider_name} API key. Set LUCY_{provider_name.upper()}_API_KEY for long-term memory.",
            )
            self.memory = None
            return

        # Robust URL parsing for Qdrant
        parsed = urlparse(settings.qdrant_url)
        qdrant_host = parsed.hostname or "localhost"
        qdrant_port = parsed.port or 6333

        # Embedder config - using OpenAI-compatible format
        # OpenRouter provides OpenAI-compatible API for embeddings
        embedder_config: dict[str, Any] = {
            "provider": "openai",  # Mem0 uses OpenAI client under the hood
            "config": {
                "model": embedding_model,
                "api_key": api_key,
            },
        }

        # If using OpenRouter, we need to override the base URL
        # Mem0's OpenAI provider supports openai_base config option
        if use_openrouter and embedding_api_base:
            embedder_config["config"]["openai_base"] = embedding_api_base

        config: dict[str, Any] = {
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "host": qdrant_host,
                    "port": qdrant_port,
                },
            },
            # Note: We don't use Mem0's LLM for chat - Lucy uses its own LLM routing
            # But Mem0 needs an LLM config for memory extraction/summarization
            "llm": {
                "provider": "openai",
                "config": {
                    "model": "gpt-4o-mini" if not use_openrouter else "openai/gpt-4o-mini",
                    "api_key": api_key,
                },
            },
            "embedder": embedder_config,
        }

        # If using OpenRouter for LLM too, set the base URL
        if use_openrouter:
            config["llm"]["config"]["openai_base"] = embedding_api_base

        if settings.qdrant_api_key:
            config["vector_store"]["config"]["api_key"] = settings.qdrant_api_key

        try:
            # Set env var for Mem0 internals
            if use_openrouter:
                os.environ["OPENROUTER_API_KEY"] = api_key
            else:
                os.environ["OPENAI_API_KEY"] = api_key
            self.memory = Memory.from_config(config)
            logger.info("vector_memory_initialized", url=settings.qdrant_url, provider=provider_name)
        except Exception as e:
            logger.error("vector_memory_init_failed", error=str(e), qdrant=f"{qdrant_host}:{qdrant_port}", provider=provider_name)
            self.memory = None

    def add(
        self,
        content: str | list[dict[str, str]],
        workspace_id: UUID,
        user_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Add a memory or conversation to the workspace memory.
        
        Args:
            content: A string fact or a list of message dicts.
            workspace_id: The workspace this memory belongs to.
            user_id: Optional user who generated this memory.
            metadata: Additional metadata.
            
        Returns:
            List of extracted memory dictionaries.
        """
        if not self.memory:
            logger.warning("memory_not_initialized_skipping_add")
            return []
            
        try:
            # We map workspace_id to agent_id in Mem0, and user_id to user_id.
            # This allows us to search either by workspace (agent) or user.
            result = self.memory.add(
                messages=content,
                user_id=str(user_id) if user_id else None,
                agent_id=str(workspace_id),
                metadata=metadata
            )
            
            logger.info(
                "memory_added",
                workspace_id=str(workspace_id),
                user_id=str(user_id) if user_id else None,
            )
            return result
        except Exception as e:
            logger.error("memory_add_failed", error=str(e))
            return []

    def search(
        self,
        query: str,
        workspace_id: UUID,
        user_id: UUID | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Search workspace memory for a query.
        
        Args:
            query: The search query.
            workspace_id: The workspace to search in.
            user_id: Optional user to filter by.
            limit: Max results to return.
            
        Returns:
            List of matching memories.
        """
        if not self.memory:
            logger.warning("memory_not_initialized_skipping_search")
            return []
            
        try:
            results = self.memory.search(
                query=query,
                user_id=str(user_id) if user_id else None,
                agent_id=str(workspace_id),
                limit=limit
            )
            
            logger.info(
                "memory_searched",
                workspace_id=str(workspace_id),
                query=query,
                result_count=len(results) if results else 0,
            )
            return results or []
        except Exception as e:
            logger.error("memory_search_failed", error=str(e))
            return []


# Singleton instance
_vector_memory: VectorMemory | None = None


def get_vector_memory() -> VectorMemory:
    """Get or create the singleton VectorMemory instance."""
    global _vector_memory
    if _vector_memory is None:
        _vector_memory = VectorMemory()
    return _vector_memory
