"""3-Tier web search tool for Lucy.

Tier 1 — Quick Grounded Search (Perplexity via OpenRouter)
    Fast web search with citations. Best for general questions,
    quick facts, and information that Perplexity can easily find.
    Cost: ~$0.003/search

Tier 2 — Deep Search (Perplexity + page scraping + verification)
    Web search + scrape source pages + cross-reference. Best for
    version numbers, pricing, API docs, and specific factual data
    that needs verification from primary sources.
    Cost: ~$0.01-0.03/search

Tier 3 — Multi-LLM Consensus (Perplexity + Gemini + GPT-4o)
    Query multiple models, identify agreement/disagreement, synthesize
    consensus answer. Best for complex analysis, comparisons, and
    strategic research where multiple perspectives add value.
    Cost: ~$0.02-0.05/search

Tier selection is automatic based on query analysis.
"""

from __future__ import annotations

import re
import time
from typing import Any

import httpx
import structlog

from lucy.config import settings
from lucy.infra.circuit_breaker import openrouter_breaker

logger = structlog.get_logger()

_WEB_SEARCH_TOOL_NAME = "lucy_web_search"

# ── Perplexity models ────────────────────────────────────────────────────
_PERPLEXITY_FAST = "perplexity/sonar"
_PERPLEXITY_PRO = "perplexity/sonar-pro"


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
                    "in your training data. Returns a concise answer with "
                    "sources. Automatically selects the best search strategy "
                    "based on query complexity."
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


# ═══════════════════════════════════════════════════════════════════════════
# TIER SELECTION
# ═══════════════════════════════════════════════════════════════════════════

# Patterns that suggest Tier 2 (need source verification)
_TIER2_PATTERNS = [
    # Version/release queries
    r"\b(?:latest|current|newest|recent)\s+(?:version|release|update)\b",
    r"\bversion\s+(?:of|number)\b",
    r"\bwhat\s+version\b",
    r"\bv\d+\.\d+",
    # Pricing/cost queries
    r"\bpric(?:e|ing|es)\b",
    r"\bcost(?:s|ing)?\b",
    r"\bplan(?:s)?\b.*?\b(?:free|pro|enterprise|business|team)\b",
    r"\bhow\s+much\b",
    # Specific data that needs primary sources
    r"\bAPI\s+(?:key|endpoint|documentation|docs|reference)\b",
    r"\bchangelog\b",
    r"\brelease\s+notes?\b",
    r"\bcompatib(?:le|ility)\b",
    r"\bdeprecated?\b",
    r"\bsystem\s+requirements?\b",
]

# Patterns that suggest Tier 3 (need multi-perspective analysis)
_TIER3_PATTERNS = [
    # Comparison queries
    r"\b(?:compare|comparison|vs|versus)\b",
    r"\b(?:better|best|which\s+(?:is|should|one))\b.*?\b(?:for|vs|or)\b",
    r"\bpros?\s+(?:and|&)\s+cons?\b",
    r"\balternatives?\s+to\b",
    # Complex research
    r"\bresearch\b.*?\b(?:market|landscape|tools|options)\b",
    r"\banalyze?\b.*?\b(?:strategy|approach|architecture)\b",
    r"\bwhat\s+(?:tools?|services?|platforms?)\s+(?:similar|like|exist)\b",
    # Strategic questions
    r"\bshould\s+(?:I|we)\b.*?\b(?:use|choose|pick|switch)\b",
    r"\badvantages?\b.*?\bdisadvantages?\b",
]


def _classify_tier(query: str) -> int:
    """Classify a query into search tier (1, 2, or 3).

    Uses pattern matching on the query text. Fast and deterministic.
    """
    query_lower = query.lower().strip()

    # Check Tier 3 patterns first (more specific)
    for pattern in _TIER3_PATTERNS:
        if re.search(pattern, query_lower, re.IGNORECASE):
            return 3

    # Check Tier 2 patterns
    for pattern in _TIER2_PATTERNS:
        if re.search(pattern, query_lower, re.IGNORECASE):
            return 2

    # Default: Tier 1
    return 1


# ═══════════════════════════════════════════════════════════════════════════
# TIER 1: QUICK GROUNDED SEARCH (Perplexity)
# ═══════════════════════════════════════════════════════════════════════════

async def _tier1_search(query: str) -> dict[str, Any]:
    """Tier 1: Quick web search via Perplexity on OpenRouter.

    Uses Perplexity's native web search capability for grounded answers
    with citations. Fast and accurate for most queries.
    """
    api_key = settings.openrouter_api_key
    if not api_key:
        return {"error": "Search not available (no API key configured)."}

    try:
        async with httpx.AsyncClient(timeout=40.0) as client:
            resp = await client.post(
                f"{settings.openrouter_base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "X-Title": "Lucy Web Search",
                },
                json={
                    "model": _PERPLEXITY_PRO,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are a precise technical research assistant. "
                                "Provide accurate, concise answers with specific "
                                "facts, numbers, and dates. Include code examples "
                                "when relevant. Cite sources with [1], [2] etc."
                            ),
                        },
                        {"role": "user", "content": query},
                    ],
                    "max_tokens": 4096,
                    "temperature": 0.1,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            choices = data.get("choices")
            if not choices:
                return {"error": "No results from search.", "query": query}

            answer = choices[0].get("message", {}).get("content", "")
            citations = data.get("citations", [])

            if not answer:
                return {"error": "Empty search result.", "query": query}

            return {
                "query": query,
                "answer": answer,
                "citations": citations,
                "tier": 1,
                "model": _PERPLEXITY_PRO,
            }

    except Exception as e:
        logger.warning("tier1_search_failed", query=query[:80], error=str(e))
        return {"error": f"Search failed: {e}", "query": query}


# ═══════════════════════════════════════════════════════════════════════════
# TIER 2 & 3: DELEGATE TO SPECIALIZED MODULES
# ═══════════════════════════════════════════════════════════════════════════

async def _tier2_search(query: str) -> dict[str, Any]:
    """Tier 2: Deep search with source verification."""
    try:
        from lucy.tools.bright_data_search import deep_search

        result = await deep_search(query)

        if "error" in result:
            # Fallback to Tier 1 on error
            logger.warning("tier2_fallback_to_tier1", query=query[:80], error=result["error"])
            return await _tier1_search(query)

        # Format the result for the agent
        answer = result.get("verified_answer") or result.get("initial_answer", "")
        citations = result.get("citations", [])

        return {
            "query": query,
            "answer": answer,
            "citations": citations,
            "tier": 2,
            "sources_verified": len(result.get("scraped_sources", [])),
        }

    except ImportError:
        logger.warning("tier2_module_not_available")
        return await _tier1_search(query)
    except Exception as e:
        logger.warning("tier2_search_failed", query=query[:80], error=str(e))
        return await _tier1_search(query)


async def _tier3_search(query: str) -> dict[str, Any]:
    """Tier 3: Multi-LLM consensus research."""
    try:
        from lucy.tools.deep_research import multi_llm_research

        result = await multi_llm_research(query)

        if "error" in result:
            # Fallback to Tier 2 on error
            logger.warning("tier3_fallback_to_tier2", query=query[:80], error=result["error"])
            return await _tier2_search(query)

        answer = result.get("consensus_answer", "")
        citations = result.get("citations", [])
        agreements = result.get("key_agreements", [])
        disagreements = result.get("key_disagreements", [])

        # Append consensus metadata to answer
        if disagreements:
            answer += "\n\n⚠️ *Note: Models disagreed on:*\n"
            for d in disagreements[:3]:
                answer += f"• {d}\n"

        return {
            "query": query,
            "answer": answer,
            "citations": citations,
            "tier": 3,
            "agreement_level": result.get("agreement_level", "unknown"),
            "models_queried": len(result.get("model_responses", [])),
        }

    except ImportError:
        logger.warning("tier3_module_not_available")
        return await _tier2_search(query)
    except Exception as e:
        logger.warning("tier3_search_failed", query=query[:80], error=str(e))
        return await _tier2_search(query)


# ═══════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

async def execute_web_search(
    parameters: dict[str, Any],
) -> dict[str, Any]:
    """Execute a web search with automatic tier selection.

    This is the main entry point called by the agent. It:
    1. Classifies the query complexity
    2. Routes to the appropriate search tier
    3. Returns the result with graceful fallbacks
    """
    query = parameters.get("query", "").strip()
    if not query:
        return {"error": "No search query provided."}

    if not openrouter_breaker.should_allow_request():
        return {"error": "Search temporarily unavailable.", "query": query}

    api_key = settings.openrouter_api_key
    if not api_key:
        return {"error": "Search not available (no API key configured)."}

    # Classify query
    tier = _classify_tier(query)
    t0 = time.monotonic()

    logger.info("web_search_start", query=query[:80], tier=tier)

    # Route to appropriate tier
    if tier == 3:
        result = await _tier3_search(query)
    elif tier == 2:
        result = await _tier2_search(query)
    else:
        result = await _tier1_search(query)

    # Record success/failure for circuit breaker
    if "error" in result:
        openrouter_breaker.record_failure()
    else:
        openrouter_breaker.record_success()

    duration = round((time.monotonic() - t0) * 1000, 1)
    logger.info(
        "web_search_complete",
        query=query[:80],
        tier=result.get("tier", tier),
        has_error="error" in result,
        response_length=len(result.get("answer", "")),
        duration_ms=duration,
    )

    return result
