"""Web search tool for Lucy's self-learning capability.

Wraps Gemini (via OpenRouter) with grounding to let the agent research
unfamiliar APIs, error messages, and documentation on the fly.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from lucy.config import settings

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
                    "model": "google/gemini-2.5-flash",
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
                    "max_tokens": 2000,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            answer = data["choices"][0]["message"]["content"]

        logger.info(
            "web_search_executed",
            query=query[:100],
            response_length=len(answer),
        )
        return {"query": query, "answer": answer}

    except Exception as e:
        logger.warning("web_search_failed", query=query[:100], error=str(e))
        return {"error": f"Search failed: {e}", "query": query}
