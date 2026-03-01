"""Tier 2: Deep web search with page scraping and source verification.

Combines Perplexity web search (for URL discovery + initial answer) with
direct page scraping (for source verification and detailed content extraction).

Also includes Bright Data SERP/Unlocker integration that activates when
zones are configured in the Bright Data dashboard.

Workflow:
    1. Perplexity search → answer + citation URLs
    2. Scrape cited URLs → extract full page content
    3. Synthesize verified answer from scraped sources + Perplexity result
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any
from urllib.parse import quote_plus, urlparse

import httpx
import structlog
from bs4 import BeautifulSoup

from lucy.config import settings

logger = structlog.get_logger()

# ── Bright Data configuration (activates when zones exist) ──────────────
BRIGHT_DATA_API_KEY = os.environ.get(
    "LUCY_BRIGHT_DATA_API_KEY",
    "db753300-891e-4cac-8989-10084f1582d5",
)
BRIGHT_DATA_BASE_URL = "https://api.brightdata.com"
BRIGHT_DATA_SERP_ZONE = os.environ.get("LUCY_BRIGHT_DATA_SERP_ZONE", "")
BRIGHT_DATA_UNLOCKER_ZONE = os.environ.get("LUCY_BRIGHT_DATA_UNLOCKER_ZONE", "")

# Perplexity model for web-grounded search
_PERPLEXITY_MODEL = "perplexity/sonar-pro"
_PERPLEXITY_MODEL_FAST = "perplexity/sonar"

# Page scraping limits
_MAX_PAGES_TO_SCRAPE = 3
_MAX_PAGE_CHARS = 15_000
_SCRAPE_TIMEOUT = 20.0


# ═══════════════════════════════════════════════════════════════════════════
# BRIGHT DATA (activates when zones are configured)
# ═══════════════════════════════════════════════════════════════════════════

_bd_zones_checked = False
_bd_zones_available = False


async def _check_bright_data_zones() -> bool:
    """Check if Bright Data zones are configured and usable."""
    global _bd_zones_checked, _bd_zones_available
    global BRIGHT_DATA_SERP_ZONE, BRIGHT_DATA_UNLOCKER_ZONE

    if _bd_zones_checked:
        return _bd_zones_available

    _bd_zones_checked = True

    if not BRIGHT_DATA_API_KEY:
        return False

    # If zones are explicitly set in env, trust them
    if BRIGHT_DATA_SERP_ZONE and BRIGHT_DATA_UNLOCKER_ZONE:
        _bd_zones_available = True
        return True

    # Try to discover zones from API
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{BRIGHT_DATA_BASE_URL}/zone/get_active_zones",
                headers={"Authorization": f"Bearer {BRIGHT_DATA_API_KEY}"},
            )
            if resp.status_code == 200:
                zones = resp.json()
                if isinstance(zones, list) and zones:
                    for zone in zones:
                        name = zone.get("name", "").lower()
                        if "serp" in name and not BRIGHT_DATA_SERP_ZONE:
                            BRIGHT_DATA_SERP_ZONE = zone["name"]
                        elif ("unlocker" in name or "unblocker" in name) and not BRIGHT_DATA_UNLOCKER_ZONE:
                            BRIGHT_DATA_UNLOCKER_ZONE = zone["name"]

                    _bd_zones_available = bool(BRIGHT_DATA_SERP_ZONE)
                    logger.info(
                        "bright_data_zones",
                        serp=BRIGHT_DATA_SERP_ZONE or "none",
                        unlocker=BRIGHT_DATA_UNLOCKER_ZONE or "none",
                        available=_bd_zones_available,
                    )
    except Exception as e:
        logger.debug("bright_data_zone_check_failed", error=str(e))

    return _bd_zones_available


async def bright_data_serp(query: str, num_results: int = 8) -> dict[str, Any]:
    """Search Google via Bright Data SERP API.

    Returns structured search results. Only works when SERP zone is configured.
    """
    if not BRIGHT_DATA_SERP_ZONE:
        return {"error": "No SERP zone configured"}

    search_url = f"https://www.google.com/search?q={quote_plus(query)}&num={num_results}"

    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(
                f"{BRIGHT_DATA_BASE_URL}/serp/req",
                headers={
                    "Authorization": f"Bearer {BRIGHT_DATA_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "zone": BRIGHT_DATA_SERP_ZONE,
                    "url": search_url,
                    "format": "json",
                },
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.warning("bright_data_serp_failed", error=str(e))
        return {"error": str(e)}


async def bright_data_scrape(url: str) -> dict[str, Any]:
    """Scrape a URL via Bright Data Web Unlocker (markdown output)."""
    if not BRIGHT_DATA_UNLOCKER_ZONE:
        return {"error": "No unlocker zone configured", "url": url}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{BRIGHT_DATA_BASE_URL}/request",
                headers={
                    "Authorization": f"Bearer {BRIGHT_DATA_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "zone": BRIGHT_DATA_UNLOCKER_ZONE,
                    "url": url,
                    "format": "raw",
                    "data_format": "markdown",
                },
            )
            resp.raise_for_status()
            content = resp.text[:_MAX_PAGE_CHARS]
            return {"url": url, "content": content, "source": "bright_data"}
    except Exception as e:
        return {"url": url, "error": str(e), "source": "bright_data"}


# ═══════════════════════════════════════════════════════════════════════════
# DIRECT SCRAPING (httpx + BeautifulSoup fallback)
# ═══════════════════════════════════════════════════════════════════════════

_SKIP_DOMAINS = {
    "youtube.com", "twitter.com", "x.com", "facebook.com",
    "instagram.com", "linkedin.com", "reddit.com",
    "tiktok.com", "pinterest.com",
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def _should_skip_url(url: str) -> bool:
    """Check if a URL is from a domain we shouldn't scrape."""
    try:
        domain = urlparse(url).netloc.lower()
        return any(skip in domain for skip in _SKIP_DOMAINS)
    except Exception:
        return True


def _extract_text_from_html(html: str, url: str = "") -> str:
    """Extract readable text content from HTML using BeautifulSoup."""
    try:
        soup = BeautifulSoup(html, "html.parser")

        # Remove scripts, styles, nav, footer, etc.
        for tag in soup.find_all(["script", "style", "nav", "footer", "header",
                                   "aside", "noscript", "iframe", "svg"]):
            tag.decompose()

        # Try to find the main content area
        main = (
            soup.find("main")
            or soup.find("article")
            or soup.find("div", {"role": "main"})
            or soup.find("div", class_=re.compile(r"content|article|post|entry", re.I))
        )

        target = main if main else soup.body if soup.body else soup

        # Extract text with structure
        lines = []
        for element in target.find_all(["h1", "h2", "h3", "h4", "p", "li", "td", "pre", "code"]):
            text = element.get_text(separator=" ", strip=True)
            if not text or len(text) < 3:
                continue
            if element.name.startswith("h"):
                level = int(element.name[1])
                lines.append(f"\n{'#' * level} {text}\n")
            elif element.name == "li":
                lines.append(f"• {text}")
            elif element.name in ("pre", "code"):
                lines.append(f"```\n{text}\n```")
            else:
                lines.append(text)

        result = "\n".join(lines)

        # Fallback: if very little structured content, just get all text
        if len(result) < 200:
            result = target.get_text(separator="\n", strip=True)

        return result[:_MAX_PAGE_CHARS]

    except Exception as e:
        logger.debug("html_extraction_failed", url=url[:80], error=str(e))
        return ""


async def scrape_url_direct(url: str) -> dict[str, Any]:
    """Scrape a URL directly using httpx + BeautifulSoup."""
    if _should_skip_url(url):
        return {"url": url, "content": "", "source": "skipped", "error": "Domain not scrapeable"}

    try:
        async with httpx.AsyncClient(
            timeout=_SCRAPE_TIMEOUT,
            follow_redirects=True,
            headers=_HEADERS,
        ) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return {
                    "url": url,
                    "content": "",
                    "source": "direct",
                    "error": f"HTTP {resp.status_code}",
                }

            ct = resp.headers.get("content-type", "")
            if "json" in ct:
                # JSON response — return as-is (truncated)
                return {
                    "url": url,
                    "content": resp.text[:_MAX_PAGE_CHARS],
                    "source": "direct_json",
                }

            # HTML response — extract text
            content = _extract_text_from_html(resp.text, url)
            return {
                "url": url,
                "content": content,
                "source": "direct",
            }

    except httpx.TimeoutException:
        return {"url": url, "content": "", "source": "direct", "error": "Timeout"}
    except Exception as e:
        return {"url": url, "content": "", "source": "direct", "error": str(e)}


async def scrape_url(url: str) -> dict[str, Any]:
    """Scrape a URL using Bright Data (if available) or direct httpx."""
    if await _check_bright_data_zones() and BRIGHT_DATA_UNLOCKER_ZONE:
        result = await bright_data_scrape(url)
        if not result.get("error"):
            return result

    # Fallback to direct scraping
    return await scrape_url_direct(url)


# ═══════════════════════════════════════════════════════════════════════════
# PERPLEXITY WEB SEARCH
# ═══════════════════════════════════════════════════════════════════════════

async def perplexity_search(
    query: str,
    model: str = _PERPLEXITY_MODEL,
) -> dict[str, Any]:
    """Search the web via Perplexity on OpenRouter.

    Returns answer text + citation URLs for source verification.
    """
    api_key = settings.openrouter_api_key
    if not api_key:
        return {"error": "No OpenRouter API key configured"}

    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(
                f"{settings.openrouter_base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "X-Title": "Lucy Deep Search",
                },
                json={
                    "model": model,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are a precise research assistant. "
                                "Answer with specific facts, numbers, and dates. "
                                "Always cite your sources with [1], [2] etc. "
                                "If information is uncertain, say so explicitly."
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

            answer = data["choices"][0]["message"]["content"]
            citations = data.get("citations", [])

            return {
                "answer": answer,
                "citations": citations,
                "model": model,
            }
    except Exception as e:
        logger.warning("perplexity_search_failed", error=str(e))
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════
# TIER 2: COMBINED DEEP SEARCH
# ═══════════════════════════════════════════════════════════════════════════

def _extract_urls_from_answer(answer: str, citations: list[str]) -> list[str]:
    """Extract URLs from Perplexity answer text and citations."""
    urls = list(citations) if citations else []

    # Also extract URLs from markdown links in the answer
    url_pattern = r'https?://[^\s\)\]>"]+'
    found = re.findall(url_pattern, answer)
    for u in found:
        if u not in urls:
            urls.append(u)

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            unique.append(u)

    return unique


async def deep_search(
    query: str,
    scrape_sources: bool = True,
    max_scrape: int = _MAX_PAGES_TO_SCRAPE,
) -> dict[str, Any]:
    """Execute Tier 2 deep search.

    1. Perplexity web search → answer + citations
    2. Optionally scrape cited sources for verification
    3. If Bright Data SERP is available, also get Google SERP results
    4. Synthesize verified answer

    Returns:
        {
            "query": str,
            "tier": "deep_search",
            "initial_answer": str,        # Perplexity's web-searched answer
            "citations": [...],            # Source URLs
            "scraped_sources": [...],      # Content from scraped pages
            "verified_answer": str,        # Final synthesized answer
        }
    """
    t0 = time.monotonic()

    # Step 1: Perplexity web search
    search_result = await perplexity_search(query)

    if "error" in search_result:
        return {
            "query": query,
            "tier": "deep_search",
            "error": search_result["error"],
        }

    initial_answer = search_result["answer"]
    citations = search_result.get("citations", [])

    # Step 2: Optionally use Bright Data SERP for additional results
    bd_results = None
    if await _check_bright_data_zones() and BRIGHT_DATA_SERP_ZONE:
        bd_results = await bright_data_serp(query)

    # Step 3: Extract URLs and scrape sources
    urls = _extract_urls_from_answer(initial_answer, citations)
    scraped_sources = []

    if scrape_sources and urls:
        # Scrape top N URLs in parallel
        scrape_tasks = [scrape_url(u) for u in urls[:max_scrape] if not _should_skip_url(u)]
        if scrape_tasks:
            results = await asyncio.gather(*scrape_tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, dict) and r.get("content"):
                    scraped_sources.append(r)

    # Step 4: Synthesize verified answer
    verified_answer = await _synthesize_verified_answer(
        query=query,
        initial_answer=initial_answer,
        scraped_sources=scraped_sources,
        bd_results=bd_results,
    )

    duration = round((time.monotonic() - t0) * 1000, 1)
    logger.info(
        "deep_search_complete",
        query=query[:80],
        citations=len(citations),
        scraped=len(scraped_sources),
        bright_data_used=bd_results is not None and "error" not in (bd_results or {}),
        duration_ms=duration,
    )

    return {
        "query": query,
        "tier": "deep_search",
        "initial_answer": initial_answer,
        "citations": citations,
        "scraped_sources": [
            {"url": s["url"], "source": s.get("source", ""), "content_length": len(s.get("content", ""))}
            for s in scraped_sources
        ],
        "verified_answer": verified_answer,
        "duration_ms": duration,
    }


async def _synthesize_verified_answer(
    query: str,
    initial_answer: str,
    scraped_sources: list[dict],
    bd_results: dict | None = None,
) -> str:
    """Synthesize a verified answer from all available sources.

    If we have scraped source content, uses it to verify and enhance
    the initial Perplexity answer. Otherwise returns the initial answer.
    """
    if not scraped_sources:
        # No scraped content — return Perplexity answer as-is
        return initial_answer

    # Build source context
    source_context = []
    for src in scraped_sources:
        content = src.get("content", "")[:6000]
        if content:
            source_context.append(
                f"--- Source: {src['url']} ---\n{content}\n"
            )

    if not source_context:
        return initial_answer

    sources_text = "\n".join(source_context)

    prompt = f"""I have a web search result and the actual source pages. 
Synthesize a verified, accurate answer.

QUESTION: {query}

INITIAL WEB SEARCH ANSWER:
{initial_answer}

ACTUAL SOURCE CONTENT:
{sources_text}

INSTRUCTIONS:
- Cross-reference the web search answer against the actual source content
- If the source content has more specific/current data (version numbers, dates, prices), USE THOSE instead
- If the web search answer and sources disagree, note the discrepancy  
- Cite specific URLs when quoting facts
- Be concise but comprehensive
- If the sources don't contain relevant info, rely on the web search answer"""

    api_key = settings.openrouter_api_key
    if not api_key:
        return initial_answer

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{settings.openrouter_base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "X-Title": "Lucy Source Verification",
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
            return data["choices"][0]["message"]["content"]
    except Exception as e:
        logger.warning("synthesis_failed", error=str(e))
        return initial_answer
