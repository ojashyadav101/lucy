"""Web search tool — real-time information via OpenRouter :online models.

Uses OpenRouter's web search plugin by appending ``:online`` to a fast
model slug.  Returns a synthesized answer plus source URLs so Lucy can
research unknown APIs, verify documentation, or answer current-events
questions without relying on stale training data.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from lucy.config import settings
from lucy.core.openclaw import ChatConfig, get_openclaw_client

logger = structlog.get_logger()

_SEARCH_MODEL = "google/gemini-2.5-flash:online"
_SEARCH_TIMEOUT = 30


def get_web_search_tool_definitions() -> list[dict[str, Any]]:
    """Return the lucy_web_search tool definition for the agent."""
    return [
        {
            "type": "function",
            "function": {
                "name": "lucy_web_search",
                "description": (
                    "Search the web for real-time information. Use this when "
                    "you don't know if an API exists, need current docs or "
                    "rate limits, need to verify facts, or the user asks "
                    "about recent events. Returns a synthesized answer with "
                    "source URLs. ALWAYS call this BEFORE guessing or giving "
                    "up on finding information about a service or API."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "The search query. Be specific — include "
                                "service names, 'API', 'documentation', "
                                "'rate limits', etc."
                            ),
                        },
                    },
                    "required": ["query"],
                },
            },
        },
    ]


async def execute_web_search(parameters: dict[str, Any]) -> dict[str, Any]:
    """Execute a web search via OpenRouter's :online model suffix."""
    query = parameters.get("query", "").strip()
    if not query:
        return {"error": "query parameter is required"}

    logger.info("web_search_start", query=query)

    client = await get_openclaw_client()

    config = ChatConfig(
        model=_SEARCH_MODEL,
        system_prompt=(
            "You are a research assistant. Answer the user's question "
            "using current web information. Include specific details: "
            "URLs, version numbers, code examples, rate limits — "
            "whatever is relevant. Be concise but thorough."
        ),
        max_tokens=4096,
        temperature=0.3,
    )

    try:
        response = await asyncio.wait_for(
            client.chat_completion(
                messages=[{"role": "user", "content": query}],
                config=config,
            ),
            timeout=_SEARCH_TIMEOUT,
        )

        answer = response.content or ""
        if not answer:
            return {
                "success": False,
                "error": "Web search returned empty response",
                "query": query,
            }

        logger.info(
            "web_search_complete",
            query=query,
            answer_length=len(answer),
        )

        return {
            "success": True,
            "query": query,
            "answer": answer,
        }

    except asyncio.TimeoutError:
        logger.warning("web_search_timeout", query=query)
        return {
            "success": False,
            "error": f"Web search timed out after {_SEARCH_TIMEOUT}s",
            "query": query,
        }
    except Exception as e:
        logger.error("web_search_failed", query=query, error=str(e))
        return {
            "success": False,
            "error": f"Web search failed: {e}",
            "query": query,
        }
