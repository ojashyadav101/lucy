"""Tier 3: Multi-LLM deep research with consensus verification.

Queries multiple LLMs simultaneously via OpenRouter, identifies areas
of agreement and disagreement, and synthesizes a consensus answer.

Best for:
- Complex comparative analysis (e.g., "Compare Stripe vs Paddle vs LemonSqueezy")
- Strategic advice that benefits from multiple perspectives
- Controversial or evolving topics where models may disagree
- Research that requires both web search and deep reasoning
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import structlog

from lucy.config import settings

logger = structlog.get_logger()

# ── Model roster for multi-LLM research ─────────────────────────────────
# Each entry: (model_id, has_web_search, role_description)
_RESEARCH_MODELS = [
    ("perplexity/sonar-pro", True, "web-search specialist with citations"),
    ("google/gemini-2.5-flash", False, "fast reasoning with broad knowledge"),
    ("openai/gpt-4o-mini", False, "cost-effective with strong reasoning"),
]

# For high-stakes research, use stronger models
_RESEARCH_MODELS_PREMIUM = [
    ("perplexity/sonar-pro", True, "web-search specialist with citations"),
    ("google/gemini-2.5-flash", False, "fast reasoning with broad knowledge"),
    ("openai/gpt-4o", False, "frontier model with deep reasoning"),
]

_MAX_RESPONSE_CHARS = 3000
_QUERY_TIMEOUT = 45.0


async def _query_model(
    model: str,
    query: str,
    system_prompt: str,
    api_key: str,
) -> dict[str, Any]:
    """Query a single model via OpenRouter."""
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=_QUERY_TIMEOUT) as client:
            resp = await client.post(
                f"{settings.openrouter_base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "X-Title": "Lucy Multi-LLM Research",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": query},
                    ],
                    "max_tokens": 4096,
                    "temperature": 0.15,
                },
            )
            elapsed = round((time.monotonic() - t0) * 1000, 1)

            if resp.status_code != 200:
                error_data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"raw": resp.text[:300]}
                return {
                    "model": model,
                    "status": "error",
                    "error": error_data.get("error", {}).get("message", str(error_data)),
                    "duration_ms": elapsed,
                }

            data = resp.json()
            answer = data["choices"][0]["message"]["content"]
            citations = data.get("citations", [])

            return {
                "model": model,
                "status": "ok",
                "answer": answer[:_MAX_RESPONSE_CHARS],
                "answer_full": answer,
                "citations": citations,
                "duration_ms": elapsed,
            }
    except Exception as e:
        elapsed = round((time.monotonic() - t0) * 1000, 1)
        return {
            "model": model,
            "status": "error",
            "error": str(e),
            "duration_ms": elapsed,
        }


async def multi_llm_research(
    query: str,
    premium: bool = False,
    custom_models: list[str] | None = None,
) -> dict[str, Any]:
    """Execute Tier 3 multi-LLM research.

    Queries multiple models simultaneously, then synthesizes consensus.

    Args:
        query: The research question
        premium: Use premium (more expensive) model roster
        custom_models: Override model list (list of OpenRouter model IDs)

    Returns:
        {
            "query": str,
            "tier": "multi_llm",
            "model_responses": [...],     # Individual model answers
            "consensus_answer": str,       # Synthesized consensus
            "agreement_level": str,        # "high" / "moderate" / "low"
            "key_agreements": [...],       # Points all models agree on
            "key_disagreements": [...],    # Points models disagree on
            "citations": [...],            # URLs from web-search models
        }
    """
    t0 = time.monotonic()
    api_key = settings.openrouter_api_key
    if not api_key:
        return {"query": query, "tier": "multi_llm", "error": "No API key"}

    # Select model roster
    if custom_models:
        models = [(m, "perplexity" in m.lower(), "custom model") for m in custom_models]
    elif premium:
        models = _RESEARCH_MODELS_PREMIUM
    else:
        models = _RESEARCH_MODELS

    system_prompt = (
        "You are a research analyst. Answer the following question with specific, "
        "verifiable facts. Include version numbers, dates, prices, and other concrete "
        "details when relevant. Structure your answer clearly. If you're unsure about "
        "something, explicitly say so rather than guessing."
    )

    # Query all models in parallel
    tasks = [
        _query_model(model_id, query, system_prompt, api_key)
        for model_id, _, _ in models
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Process results
    model_responses = []
    successful_answers = []
    all_citations = []

    for i, result in enumerate(results):
        if isinstance(result, Exception):
            model_responses.append({
                "model": models[i][0],
                "status": "error",
                "error": str(result),
            })
        elif isinstance(result, dict):
            model_responses.append(result)
            if result.get("status") == "ok":
                successful_answers.append(result)
                all_citations.extend(result.get("citations", []))

    if not successful_answers:
        return {
            "query": query,
            "tier": "multi_llm",
            "error": "All models failed",
            "model_responses": model_responses,
        }

    # Synthesize consensus
    consensus = await _synthesize_consensus(query, successful_answers, api_key)

    duration = round((time.monotonic() - t0) * 1000, 1)
    logger.info(
        "multi_llm_research_complete",
        query=query[:80],
        models_queried=len(models),
        models_succeeded=len(successful_answers),
        agreement_level=consensus.get("agreement_level", "unknown"),
        duration_ms=duration,
    )

    # Deduplicate citations
    seen_citations = set()
    unique_citations = []
    for c in all_citations:
        if c not in seen_citations:
            seen_citations.add(c)
            unique_citations.append(c)

    return {
        "query": query,
        "tier": "multi_llm",
        "model_responses": [
            {
                "model": r["model"],
                "status": r["status"],
                "answer": r.get("answer", ""),
                "duration_ms": r.get("duration_ms"),
            }
            for r in model_responses
        ],
        "consensus_answer": consensus.get("answer", ""),
        "agreement_level": consensus.get("agreement_level", "unknown"),
        "key_agreements": consensus.get("agreements", []),
        "key_disagreements": consensus.get("disagreements", []),
        "citations": unique_citations,
        "duration_ms": duration,
    }


async def _synthesize_consensus(
    query: str,
    model_answers: list[dict],
    api_key: str,
) -> dict[str, Any]:
    """Synthesize consensus from multiple model answers.

    Identifies agreements, disagreements, and produces a balanced answer.
    """
    # Build comparison context
    responses_text = []
    for i, ans in enumerate(model_answers, 1):
        model_name = ans["model"].split("/")[-1]
        has_web = "perplexity" in ans["model"].lower()
        web_tag = " [HAS WEB SEARCH]" if has_web else " [NO WEB SEARCH]"
        responses_text.append(
            f"--- Response {i} from {model_name}{web_tag} ---\n"
            f"{ans.get('answer_full', ans.get('answer', ''))}\n"
        )

    all_responses = "\n".join(responses_text)

    prompt = f"""You are a senior research analyst synthesizing multiple AI model responses.

QUESTION: {query}

{all_responses}

TASK:
Analyze ALL responses above and produce a synthesis. Pay special attention to:
1. Models WITH web search ([HAS WEB SEARCH]) are more likely to have current data
2. When models disagree on facts (versions, dates, prices), prefer web-searched data
3. When models agree, that's a strong signal of accuracy

Return your analysis as:

## Answer
[Your synthesized, accurate answer to the question]

## Agreement Level
[HIGH / MODERATE / LOW - how much the models agree]

## Key Agreements
[Bullet points of facts all or most models agree on]

## Key Disagreements  
[Bullet points of facts models disagree on, noting which is likely correct]

Be specific with facts. If web-searched models have more current data, prefer that."""

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{settings.openrouter_base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "X-Title": "Lucy Consensus Synthesis",
                },
                json={
                    "model": settings.model_tier_research,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 4096,
                    "temperature": 0.1,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            synthesis = data["choices"][0]["message"]["content"]

            # Parse structured sections from synthesis
            return _parse_consensus(synthesis)

    except Exception as e:
        logger.warning("consensus_synthesis_failed", error=str(e))
        # Fallback: return the web-searched model's answer
        for ans in model_answers:
            if "perplexity" in ans["model"].lower():
                return {
                    "answer": ans.get("answer_full", ans.get("answer", "")),
                    "agreement_level": "unknown",
                    "agreements": [],
                    "disagreements": [],
                }
        return {
            "answer": model_answers[0].get("answer_full", model_answers[0].get("answer", "")),
            "agreement_level": "unknown",
            "agreements": [],
            "disagreements": [],
        }


def _parse_consensus(text: str) -> dict[str, Any]:
    """Parse structured consensus output into components."""
    result: dict[str, Any] = {
        "answer": "",
        "agreement_level": "unknown",
        "agreements": [],
        "disagreements": [],
    }

    # Extract Answer section
    answer_match = _extract_section(text, "Answer")
    if answer_match:
        result["answer"] = answer_match.strip()
    else:
        # No structured output — use the whole thing
        result["answer"] = text.strip()

    # Extract Agreement Level
    level_match = _extract_section(text, "Agreement Level")
    if level_match:
        level = level_match.strip().upper()
        if "HIGH" in level:
            result["agreement_level"] = "high"
        elif "LOW" in level:
            result["agreement_level"] = "low"
        else:
            result["agreement_level"] = "moderate"

    # Extract Key Agreements
    agreements = _extract_section(text, "Key Agreements")
    if agreements:
        result["agreements"] = _extract_bullet_points(agreements)

    # Extract Key Disagreements
    disagreements = _extract_section(text, "Key Disagreements")
    if disagreements:
        result["disagreements"] = _extract_bullet_points(disagreements)

    return result


def _extract_section(text: str, heading: str) -> str | None:
    """Extract content under a ## heading."""
    import re
    pattern = rf"##\s*{re.escape(heading)}\s*\n(.*?)(?=\n##|\Z)"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else None


def _extract_bullet_points(text: str) -> list[str]:
    """Extract bullet points from text."""
    import re
    points = []
    for line in text.split("\n"):
        line = line.strip()
        if line and (line.startswith("-") or line.startswith("•") or line.startswith("*")):
            point = line.lstrip("-•* ").strip()
            if point:
                points.append(point)
    return points
