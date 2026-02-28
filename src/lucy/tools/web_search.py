"""Web search tool for Lucy's self-learning capability.

Wraps Gemini (via OpenRouter) with grounding to let the agent research
unfamiliar APIs, error messages, and documentation on the fly.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from lucy.config import settings
from lucy.infra.circuit_breaker import openrouter_breaker

logger = structlog.get_logger()

_WEB_SEARCH_TOOL_NAME = "lucy_web_search"


def get_web_search_tool_definitions() -> list[dict[str, Any]]:
    """Return OpenAI-format tool definition for web search."""
    return [
        {
            "type": "function",
            "function": {
                "name": _WEB_SEARCH_TOOL_NAME,
                "description": (
                    "Search the web for real-time information. Use this when "
                    "you encounter an unfamiliar API, need to look up "
                    "documentation, debug an error message you don't "
                    "recognize, or need current information that may not be "
                    "in your training data. Returns a concise answer."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "The search query. Be specific: include API "
                                "names, error codes, library versions, etc."
                            ),
                        },
                    },
                    "required": ["query"],
                },
            },
        },
    ]


def is_web_search_tool(tool_name: str) -> bool:
    return tool_name == _WEB_SEARCH_TOOL_NAME


async def execute_web_search(
    parameters: dict[str, Any],
) -> dict[str, Any]:
    """Execute a web search via Gemini on OpenRouter."""
    query = parameters.get("query", "").strip()
    if not query:
        return {"error": "No search query provided."}

    if not openrouter_breaker.should_allow_request():
        return {"error": "Search temporarily unavailable.", "query": query}

    api_key = settings.openrouter_api_key
    if not api_key:
        return {"error": "Search not available (no API key configured)."}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{settings.openrouter_base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "X-Title": "Lucy Web Search",
                },
                json={
                    "model": settings.model_tier_fast,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are a technical research assistant. "
                                "Provide accurate, concise answers to "
                                "technical queries. Include code examples "
                                "when relevant. Cite sources when possible."
                            ),
                        },
                        {"role": "user", "content": query},
                    ],
                    "max_tokens": 4096,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            choices = data.get("choices")
            if not choices:
                return {"error": "No results from search.", "query": query}
            answer = choices[0].get("message", {}).get("content", "")
            if not answer:
                return {"error": "Empty search result.", "query": query}

        logger.info(
            "web_search_executed",
            query=query[:100],
            response_length=len(answer),
        )
        openrouter_breaker.record_success()
        return {"query": query, "answer": answer}

    except Exception as e:
        openrouter_breaker.record_failure()
        logger.warning("web_search_failed", query=query[:100], error=str(e))
        return {"error": f"Search failed: {e}", "query": query}
