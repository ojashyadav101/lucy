"""Tier 2: Deep web search with source scraping and verification.

Combines Perplexity web search (for URL discovery + initial answer) with
direct page scraping (for source verification and content extraction).

Also integrates Bright Data SERP/Unlocker APIs when configured — these
provide reliable Google SERP results and anti-bot page scraping for
sites that block direct requests.

Workflow:
    1. Perplexity search → answer + citation URLs
    2. (Optional) Bright Data SERP → additional Google results
    3. Scrape top cited URLs → extract page content
    4. Synthesize verified answer from sources + Perplexity result
"""

from __future__ import annotations

import asyncio
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

# ── Bright Data configuration ───────────────────────────────────────────
# Activates automatically when zones are configured via env or API discovery.
BRIGHT_DATA_API_KEY = os.environ.get(
    "LUCY_BRIGHT_DATA_API_KEY",
    "db753300-891e-4cac-8989-10084f1582d5",
)
BRIGHT_DATA_BASE_URL = "https://api.brightdata.com"
BRIGHT_DATA_SERP_ZONE = os.environ.get("LUCY_BRIGHT_DATA_SERP_ZONE", "")
BRIGHT_DATA_UNLOCKER_ZONE = os.environ.get("LUCY_BRIGHT_DATA_UNLOCKER_ZONE", "")

# ── Scraping limits ─────────────────────────────────────────────────────
_MAX_PAGES_TO_SCRAPE = 3
_MAX_PAGE_CHARS = 15_000
_SCRAPE_TIMEOUT = 20.0
_SYNTHESIS_CONTEXT_PER_SOURCE = 6000

# Domains we skip (social media, auth walls, etc.)
_SKIP_DOMAINS = frozenset({
    "youtube.com", "twitter.com", "x.com", "facebook.com",
    "instagram.com", "linkedin.com", "reddit.com",
    "tiktok.com", "pinterest.com",
})

_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


# ═══════════════════════════════════════════════════════════════════════════
# BRIGHT DATA ZONE DISCOVERY
# ═══════════════════════════════════════════════════════════════════════════

_bd_zones_checked = False
_bd_zones_available = False


async def _check_bright_data_zones() -> bool:
    """Check if Bright Data zones are configured and usable.

    Caches result after first call. Discovers zones from API if not set
    in environment variables.
    """
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

    # Try to discover zones from Bright Data API
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{BRIGHT_DATA_BASE_URL}/zone/get_active_zones",
                headers={"Authorization": f"Bearer {BRIGHT_DATA_API_KEY}"},
            )
            if resp.status_code == 200:
                zones = resp.json()
                if isinstance(zones, list):
                    for zone in zones:
                        name = zone.get("name", "").lower()
                        if "serp" in name and not BRIGHT_DATA_SERP_ZONE:
                            BRIGHT_DATA_SERP_ZONE = zone["name"]
                        elif ("unlocker" in name or "unblocker" in name) and not BRIGHT_DATA_UNLOCKER_ZONE:
                            BRIGHT_DATA_UNLOCKER_ZONE = zone["name"]

                    _bd_zones_available = bool(BRIGHT_DATA_SERP_ZONE)
                    logger.info(
                        "bright_data_zones_discovered",
                        serp=BRIGHT_DATA_SERP_ZONE or "none",
                        unlocker=BRIGHT_DATA_UNLOCKER_ZONE or "none",
                    )
    except Exception as e:
        logger.debug("bright_data_zone_check_failed", error=str(e))

    return _bd_zones_available


# ═══════════════════════════════════════════════════════════════════════════
# BRIGHT DATA SERP & UNLOCKER
# ═══════════════════════════════════════════════════════════════════════════

async def bright_data_serp(query: str, num_results: int = 8) -> dict[str, Any]:
    """Search Google via Bright Data SERP API.

    Returns structured search results with titles, URLs, and snippets.
    Only works when a SERP zone is configured.
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
            data = resp.json()
            logger.info("bright_data_serp_ok", query=query[:80], results=len(data.get("organic", [])))
            return data
    except Exception as e:
        logger.warning("bright_data_serp_failed", query=query[:60], error=str(e))
        return {"error": str(e)}


async def bright_data_scrape(url: str) -> dict[str, Any]:
    """Scrape a URL via Bright Data Web Unlocker.

    Handles anti-bot protections that block direct requests.
    Falls back gracefully if unlocker zone isn't configured.
    """
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
# DIRECT SCRAPING (httpx + BeautifulSoup)
# ═══════════════════════════════════════════════════════════════════════════

def _should_skip_url(url: str) -> bool:
    """Check if a URL is from a domain we shouldn't scrape."""
    try:
        domain = urlparse(url).netloc.lower()
        return any(skip in domain for skip in _SKIP_DOMAINS)
    except Exception:
        return True


def _extract_text_from_html(html: str, url: str = "") -> str:
    """Extract readable text from HTML using BeautifulSoup.

    Focuses on main content area, strips boilerplate (nav, footer, ads).
    Returns markdown-ish text with headers and code blocks preserved.
    """
    try:
        soup = BeautifulSoup(html, "html.parser")

        # Remove noise elements
        for tag in soup.find_all([
            "script", "style", "nav", "footer", "header",
            "aside", "noscript", "iframe", "svg", "form",
        ]):
            tag.decompose()

        # Try to find the main content area (prefer specific containers)
        main = (
            soup.find("main")
            or soup.find("article")
            or soup.find("div", {"role": "main"})
            or soup.find("div", class_=re.compile(r"content|article|post|entry", re.I))
        )

        target = main if main else soup.body if soup.body else soup

        # Extract text with structure preservation
        lines: list[str] = []
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

        # Fallback: if structured extraction yielded very little, get all text
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
            headers=_HTTP_HEADERS,
        ) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return {
                    "url": url, "content": "",
                    "source": "direct", "error": f"HTTP {resp.status_code}",
                }

            ct = resp.headers.get("content-type", "")
            if "json" in ct:
                return {
                    "url": url,
                    "content": resp.text[:_MAX_PAGE_CHARS],
                    "source": "direct_json",
                }

            content = _extract_text_from_html(resp.text, url)
            return {"url": url, "content": content, "source": "direct"}

    except httpx.TimeoutException:
        return {"url": url, "content": "", "source": "direct", "error": "Timeout"}
    except Exception as e:
        return {"url": url, "content": "", "source": "direct", "error": str(e)}


async def scrape_url(url: str) -> dict[str, Any]:
    """Scrape a URL — tries Bright Data first, falls back to direct.

    This is the primary scraping entry point. It:
    1. Checks if Bright Data Unlocker is available
    2. If yes, uses it (handles anti-bot protections)
    3. If not, falls back to direct httpx + BeautifulSoup
    """
    if await _check_bright_data_zones() and BRIGHT_DATA_UNLOCKER_ZONE:
        result = await bright_data_scrape(url)
        if not result.get("error"):
            return result
        # Bright Data failed → fall through to direct

    return await scrape_url_direct(url)


# ═══════════════════════════════════════════════════════════════════════════
# URL EXTRACTION
# ═══════════════════════════════════════════════════════════════════════════

def _extract_urls_from_answer(answer: str, citations: list[str]) -> list[str]:
    """Extract unique URLs from Perplexity citations and answer text."""
    urls = list(citations) if citations else []

    # Also extract inline URLs from markdown links
    url_pattern = r'https?://[^\s\)\]>"\']+'
    found = re.findall(url_pattern, answer)
    for u in found:
        # Clean trailing punctuation
        u = u.rstrip(".,;:!?)")
        if u not in urls:
            urls.append(u)

    # Deduplicate preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            unique.append(u)

    return unique


# ═══════════════════════════════════════════════════════════════════════════
# SYNTHESIS (cross-reference sources with initial answer)
# ═══════════════════════════════════════════════════════════════════════════

async def _synthesize_verified_answer(
    query: str,
    initial_answer: str,
    scraped_sources: list[dict],
    bd_results: dict | None = None,
) -> str:
    """Synthesize a verified answer from Perplexity + scraped sources.

    Cross-references the web search answer against actual source page
    content. Prefers source content for specific facts (versions, dates,
    prices) since it's from the primary source.
    """
    if not scraped_sources:
        return initial_answer

    # Build source context for the synthesizer
    source_context: list[str] = []
    for src in scraped_sources:
        content = src.get("content", "")[:_SYNTHESIS_CONTEXT_PER_SOURCE]
        if content:
            source_context.append(f"--- Source: {src['url']} ---\n{content}\n")

    if not source_context:
        return initial_answer

    # Add Bright Data SERP snippets if available
    bd_context = ""
    if bd_results and "organic" in bd_results:
        snippets = []
        for r in bd_results.get("organic", [])[:5]:
            title = r.get("title", "")
            snippet = r.get("description", r.get("snippet", ""))
            url = r.get("link", r.get("url", ""))
            if title and snippet:
                snippets.append(f"• [{title}]({url}): {snippet}")
        if snippets:
            bd_context = "\n\nGOOGLE SEARCH SNIPPETS:\n" + "\n".join(snippets)

    sources_text = "\n".join(source_context)

    prompt = f"""I have a web search result and the actual source pages. 
Synthesize a verified, accurate answer.

QUESTION: {query}

INITIAL WEB SEARCH ANSWER:
{initial_answer}

ACTUAL SOURCE CONTENT:
{sources_text}{bd_context}

INSTRUCTIONS:
- Cross-reference the web search answer against the actual source content
- If the source content has more specific/current data (version numbers, dates, prices), USE THOSE
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


# ═══════════════════════════════════════════════════════════════════════════
# TIER 2: COMBINED DEEP SEARCH (main entry point)
# ═══════════════════════════════════════════════════════════════════════════

async def deep_search(
    query: str,
    scrape_sources: bool = True,
    max_scrape: int = _MAX_PAGES_TO_SCRAPE,
) -> dict[str, Any]:
    """Execute Tier 2 deep search with source verification.

    1. Perplexity web search → answer + citation URLs
    2. (If available) Bright Data SERP → additional Google results
    3. Scrape top cited URLs → extract full page content
    4. Cross-reference and synthesize verified answer

    Returns:
        {
            "query": str,
            "tier": "deep_search",
            "initial_answer": str,
            "citations": [str],
            "scraped_sources": [{url, source, content_length}],
            "verified_answer": str,
            "duration_ms": float,
        }
    """
    t0 = time.monotonic()

    # Step 1: Perplexity web search (using the standalone module)
    try:
        from lucy.tools.perplexity_search import search as perplexity_search
        search_result = await perplexity_search(query)
    except ImportError:
        # Fallback: inline Perplexity call if module not available yet
        search_result = await _inline_perplexity_search(query)

    if "error" in search_result:
        return {
            "query": query,
            "tier": "deep_search",
            "error": search_result["error"],
        }

    initial_answer = search_result["answer"]
    citations = search_result.get("citations", [])

    # Step 2: Optionally query Bright Data SERP for additional results
    bd_results = None
    if await _check_bright_data_zones() and BRIGHT_DATA_SERP_ZONE:
        bd_results = await bright_data_serp(query)
        if "error" in (bd_results or {}):
            bd_results = None

    # Step 3: Extract URLs and scrape source pages
    urls = _extract_urls_from_answer(initial_answer, citations)

    # Add URLs from Bright Data SERP if available
    if bd_results and "organic" in bd_results:
        for r in bd_results.get("organic", [])[:5]:
            url = r.get("link", r.get("url", ""))
            if url and url not in urls:
                urls.append(url)

    scraped_sources: list[dict] = []
    if scrape_sources and urls:
        scrape_candidates = [u for u in urls[:max_scrape] if not _should_skip_url(u)]
        if scrape_candidates:
            tasks = [scrape_url(u) for u in scrape_candidates]
            results = await asyncio.gather(*tasks, return_exceptions=True)
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
        bright_data_used=bd_results is not None,
        duration_ms=duration,
    )

    return {
        "query": query,
        "tier": "deep_search",
        "initial_answer": initial_answer,
        "citations": citations,
        "scraped_sources": [
            {
                "url": s["url"],
                "source": s.get("source", ""),
                "content_length": len(s.get("content", "")),
            }
            for s in scraped_sources
        ],
        "verified_answer": verified_answer,
        "duration_ms": duration,
    }


async def _inline_perplexity_search(query: str) -> dict[str, Any]:
    """Fallback Perplexity search when perplexity_search module isn't available.

    This ensures bright_data_search.py works even before perplexity_search.py
    is deployed, maintaining backwards compatibility.
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
                    "model": "perplexity/sonar-pro",
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
            return {
                "answer": data["choices"][0]["message"]["content"],
                "citations": data.get("citations", []),
                "model": "perplexity/sonar-pro",
            }
    except Exception as e:
        logger.warning("inline_perplexity_failed", error=str(e))
        return {"error": str(e)}
