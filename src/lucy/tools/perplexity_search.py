"""Perplexity web search module for Lucy.

Provides grounded web search via Perplexity models on OpenRouter.
Returns real-time answers with citations — no training-data hallucinations.

Used by:
- web_search.py (Tier 1: quick grounded search)
- bright_data_search.py (Tier 2: initial search before source verification)
- deep_research.py (Tier 3: as one of the multi-LLM models)
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import structlog

from lucy.config import settings

logger = structlog.get_logger()

# ── Model IDs on OpenRouter ─────────────────────────────────────────────
MODEL_SONAR = "perplexity/sonar"           # Fast, cheaper (~$1/M tokens)
MODEL_SONAR_PRO = "perplexity/sonar-pro"   # Better quality, web grounded

# ── Defaults ─────────────────────────────────────────────────────────────
_DEFAULT_MODEL = MODEL_SONAR_PRO
_DEFAULT_TIMEOUT = 45.0
_DEFAULT_MAX_TOKENS = 4096
_DEFAULT_TEMPERATURE = 0.1


async def search(
    query: str,
    *,
    model: str = _DEFAULT_MODEL,
    system_prompt: str | None = None,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    temperature: float = _DEFAULT_TEMPERATURE,
    timeout: float = _DEFAULT_TIMEOUT,
    search_recency_filter: str | None = None,
) -> dict[str, Any]:
    """Execute a web-grounded search via Perplexity on OpenRouter.

    Args:
        query: The search query — be specific for best results.
        model: Perplexity model ID (sonar or sonar-pro).
        system_prompt: Override the default system prompt.
        max_tokens: Maximum response tokens.
        temperature: Sampling temperature (lower = more factual).
        timeout: HTTP request timeout in seconds.
        search_recency_filter: Perplexity recency filter — one of
            "day", "week", "month", "year". Only supported on sonar models.
            Useful for queries about recent events or latest versions.

    Returns:
        {
            "answer": str,        # The web-grounded answer text
            "citations": [str],   # Source URLs from Perplexity
            "model": str,         # Model used
            "duration_ms": float, # Response time
        }
        On error: {"error": str, "query": str}
    """
    api_key = settings.openrouter_api_key
    if not api_key:
        return {"error": "No OpenRouter API key configured", "query": query}

    if not system_prompt:
        system_prompt = (
            "You are a precise technical research assistant. "
            "Provide accurate, concise answers with specific "
            "facts, numbers, and dates. Include code examples "
            "when relevant. Cite sources with [1], [2] etc. "
            "If information is uncertain, say so explicitly."
        )

    # Build request payload
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": query},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    # Add recency filter if specified (Perplexity-specific via provider routing)
    if search_recency_filter and search_recency_filter in ("day", "week", "month", "year"):
        payload["provider"] = {
            "order": ["Perplexity"],
            "allow_fallbacks": False,
        }
        # OpenRouter passes through provider-specific params
        payload["search_recency_filter"] = search_recency_filter

    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{settings.openrouter_base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "X-Title": "Lucy Web Search",
                },
                json=payload,
            )
            elapsed = round((time.monotonic() - t0) * 1000, 1)

            if resp.status_code == 429:
                logger.warning("perplexity_rate_limited", query=query[:80])
                return {"error": "Rate limited — try again in a moment", "query": query}

            resp.raise_for_status()
            data = resp.json()

            choices = data.get("choices")
            if not choices:
                return {"error": "No results from search", "query": query}

            answer = choices[0].get("message", {}).get("content", "")
            if not answer:
                return {"error": "Empty search result", "query": query}

            # Perplexity returns citations at the top level
            citations = data.get("citations", [])

            logger.info(
                "perplexity_search_ok",
                query=query[:80],
                model=model,
                citations=len(citations),
                answer_len=len(answer),
                duration_ms=elapsed,
            )

            return {
                "answer": answer,
                "citations": citations,
                "model": model,
                "duration_ms": elapsed,
            }

    except httpx.TimeoutException:
        elapsed = round((time.monotonic() - t0) * 1000, 1)
        logger.warning("perplexity_timeout", query=query[:80], duration_ms=elapsed)
        return {"error": f"Search timed out after {timeout}s", "query": query}

    except httpx.HTTPStatusError as e:
        elapsed = round((time.monotonic() - t0) * 1000, 1)
        logger.warning(
            "perplexity_http_error",
            query=query[:80],
            status=e.response.status_code,
            duration_ms=elapsed,
        )
        return {"error": f"Search HTTP error: {e.response.status_code}", "query": query}

    except Exception as e:
        elapsed = round((time.monotonic() - t0) * 1000, 1)
        logger.warning("perplexity_search_failed", query=query[:80], error=str(e))
        return {"error": f"Search failed: {e}", "query": query}


async def quick_search(query: str) -> dict[str, Any]:
    """Shortcut: fast search using sonar (cheaper, faster).

    Use for simple fact checks where sonar-pro quality isn't needed.
    """
    return await search(query, model=MODEL_SONAR, max_tokens=2048, timeout=30.0)


async def research_search(query: str) -> dict[str, Any]:
    """Shortcut: thorough search using sonar-pro with higher token limit.

    Use for detailed research where comprehensive answers matter.
    """
    return await search(
        query,
        model=MODEL_SONAR_PRO,
        max_tokens=8192,
        timeout=60.0,
        system_prompt=(
            "You are a thorough research analyst. Provide comprehensive, "
            "well-structured answers with specific facts, statistics, and dates. "
            "Include relevant context, comparisons, and nuances. "
            "Cite every factual claim with numbered source references [1], [2] etc. "
            "If there's uncertainty or conflicting information, note it explicitly."
        ),
    )


async def recent_search(query: str, recency: str = "week") -> dict[str, Any]:
    """Shortcut: search with recency filter for time-sensitive queries.

    Useful for "latest version of X", "recent changes to Y", etc.

    Args:
        query: Search query
        recency: One of "day", "week", "month", "year"
    """
    return await search(
        query,
        model=MODEL_SONAR_PRO,
        search_recency_filter=recency,
    )
