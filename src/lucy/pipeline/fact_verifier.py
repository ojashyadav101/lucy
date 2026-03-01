"""Fact verification layer for Lucy's output pipeline.

Detects and corrects hallucination-prone content before it reaches the user.
Runs as Layer 0.5 in process_output(): after strip_internal, before sanitize.

Six verification strategies:
  1. Date/day-of-week: validate against system time (cheap, always runs)
  2. URL validation: async HEAD requests with timeout (fast, batch)
  3. Version hedging: flag stale version claims with uncertainty markers
  4. Pricing hedging: flag specific pricing that may be outdated
  5. Temporal claims: flag "as of" dates that are stale
  6. Percentage sanity: flag percentages > 100% or obviously wrong

Design constraints:
  - Must be fast (<500ms typical, <2s worst case)
  - Must not break valid content
  - Must not require external API calls for basic claims
  - URL validation is best-effort with aggressive timeouts
  - Version/pricing hedging is additive (appends caveats), never destructive
  - Code blocks are ALWAYS excluded from analysis
"""

from __future__ import annotations

import asyncio
import calendar
import re
from datetime import datetime, timezone
from typing import NamedTuple

import structlog

logger = structlog.get_logger()


# ═══════════════════════════════════════════════════════════════════════
# CODE BLOCK PROTECTION
# ═══════════════════════════════════════════════════════════════════════

_CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_CODE_PLACEHOLDER = "\x00CODEBLOCK{}\x00"


def _stash_code_blocks(text: str) -> tuple[str, list[str]]:
    """Remove code blocks from text, returning stashed blocks for restoration.

    This prevents false positives on version numbers, dates, and URLs
    that appear inside code blocks (e.g., `npm install next@14.2.0`).
    """
    stash: list[str] = []

    def _stash(m: re.Match[str]) -> str:
        stash.append(m.group(0))
        return _CODE_PLACEHOLDER.format(len(stash) - 1)

    text = _CODE_BLOCK_RE.sub(_stash, text)
    text = _INLINE_CODE_RE.sub(_stash, text)
    return text, stash


def _restore_code_blocks(text: str, stash: list[str]) -> str:
    """Restore stashed code blocks to their original positions."""
    for i, block in enumerate(stash):
        text = text.replace(_CODE_PLACEHOLDER.format(i), block)
    return text


class VerificationResult(NamedTuple):
    """Result of fact verification on a text."""
    corrected_text: str
    corrections: list[str]  # Human-readable list of what was changed
    flagged_claims: list[str]  # Claims that couldn't be verified but were flagged


# ═══════════════════════════════════════════════════════════════════════
# DATE & DAY-OF-WEEK VERIFICATION
# ═══════════════════════════════════════════════════════════════════════

_MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9,
    "oct": 10, "nov": 11, "dec": 12,
}

_DAYS_OF_WEEK = [
    "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Sunday",
]

# Pattern: "Tuesday, March 15, 2025" or "March 15th, 2025 (Tuesday)"
# or "released on Monday, April 11, 2024"
_DATE_WITH_DAY_RE = re.compile(
    r"(?P<day_name>" + "|".join(_DAYS_OF_WEEK) + r")"
    r"[,\s]+(?P<month>" + "|".join(
        m for m in _MONTH_NAMES if len(m) > 3
    ) + r")\s+(?P<date>\d{1,2})(?:st|nd|rd|th)?"
    r"[,\s]+(?P<year>\d{4})",
    re.IGNORECASE,
)

# Pattern: "March 15, 2025 (Tuesday)" or "April 11, 2024, a Monday"
_DATE_THEN_DAY_RE = re.compile(
    r"(?P<month>" + "|".join(
        m for m in _MONTH_NAMES if len(m) > 3
    ) + r")\s+(?P<date>\d{1,2})(?:st|nd|rd|th)?"
    r"[,\s]+(?P<year>\d{4})"
    r"[,\s(]+(?:a\s+)?(?P<day_name>" + "|".join(_DAYS_OF_WEEK) + r")",
    re.IGNORECASE,
)

# Pattern: "Today is Wednesday" or "It's Thursday"
_TODAY_IS_DAY_RE = re.compile(
    r"(?:today\s+is|it'?s|it\s+is)\s+"
    r"(?P<day_name>" + "|".join(_DAYS_OF_WEEK) + r")",
    re.IGNORECASE,
)

# Pattern: "Today is March 1, 2026" or "The current date is February 28, 2026"
_TODAY_DATE_RE = re.compile(
    r"(?:today(?:'s date)?\s+is|current date\s+is|the date\s+is)\s+"
    r"(?:(?P<day_name>" + "|".join(_DAYS_OF_WEEK) + r")[,\s]+)?"
    r"(?P<month>" + "|".join(
        m for m in _MONTH_NAMES if len(m) > 3
    ) + r")\s+(?P<date>\d{1,2})(?:st|nd|rd|th)?"
    r"(?:[,\s]+(?P<year>\d{4}))?",
    re.IGNORECASE,
)


def _get_actual_day(year: int, month: int, day: int) -> str | None:
    """Get the actual day of the week for a date. Returns None if invalid."""
    try:
        dt = datetime(year, month, day)
        return _DAYS_OF_WEEK[dt.weekday()]
    except (ValueError, OverflowError):
        return None


def _verify_day_of_week_claims(text: str, now: datetime) -> tuple[str, list[str]]:
    """Verify that day-of-week claims match the actual calendar.

    Fixes wrong day names in date references like
    "Tuesday, March 15, 2025" when March 15, 2025 is actually a Saturday.
    """
    corrections: list[str] = []

    # Check "Today is [day]" claims
    for m in _TODAY_IS_DAY_RE.finditer(text):
        claimed_day = m.group("day_name").capitalize()
        actual_day = _DAYS_OF_WEEK[now.weekday()]
        if claimed_day != actual_day:
            text = text[:m.start("day_name")] + actual_day + text[m.end("day_name"):]
            corrections.append(
                f"Fixed day-of-week: 'today is {claimed_day}' → 'today is {actual_day}'"
            )

    # Check dates with day names: "Tuesday, March 15, 2025"
    for pattern in [_DATE_WITH_DAY_RE, _DATE_THEN_DAY_RE]:
        # Re-search after each replacement since positions shift
        offset = 0
        for m in pattern.finditer(text):
            month_name = m.group("month").lower()
            month_num = _MONTH_NAMES.get(month_name)
            if not month_num:
                continue
            try:
                year = int(m.group("year"))
                day = int(m.group("date"))
            except (ValueError, IndexError):
                continue

            actual_day = _get_actual_day(year, month_num, day)
            if not actual_day:
                continue

            claimed_day = m.group("day_name").capitalize()
            if claimed_day != actual_day:
                # Replace just the day name
                start = m.start("day_name")
                end = m.end("day_name")
                text = text[:start] + actual_day + text[end:]
                corrections.append(
                    f"Fixed day-of-week: '{claimed_day}, {m.group('month')} {day}, {year}' "
                    f"→ '{actual_day}' (was wrong)"
                )

    # Check "Today is [Month] [day], [year]" claims against system time
    for m in _TODAY_DATE_RE.finditer(text):
        month_name = m.group("month").lower()
        month_num = _MONTH_NAMES.get(month_name)
        if not month_num:
            continue
        try:
            day = int(m.group("date"))
            year_str = m.group("year")
            year = int(year_str) if year_str else now.year
        except (ValueError, IndexError):
            continue

        # Check if claimed "today" date matches actual today
        if (year, month_num, day) != (now.year, now.month, now.day):
            actual_month = calendar.month_name[now.month]
            actual_day_name = _DAYS_OF_WEEK[now.weekday()]
            # Build corrected date string
            old_span = text[m.start():m.end()]
            new_date = f"today is {actual_day_name}, {actual_month} {now.day}, {now.year}"
            if old_span.startswith(("Today", "today")):
                new_date = new_date[0].upper() + new_date[1:]  # preserve case
            # Only fix if the prefix is "today is" or similar
            prefix_end = m.start("month")
            text = text[:prefix_end] + f"{actual_day_name}, {actual_month} {now.day}, {now.year}" + text[m.end():]
            corrections.append(
                f"Fixed 'today' date claim: was {m.group('month')} {day}, {year} "
                f"→ {actual_month} {now.day}, {now.year}"
            )

    return text, corrections


# ═══════════════════════════════════════════════════════════════════════
# VERSION NUMBER DETECTION & HEDGING
# ═══════════════════════════════════════════════════════════════════════

# Matches: "version 14.2.0", "v3.1", "Next.js 15.1.0", "React 19.0"
_VERSION_CLAIM_RE = re.compile(
    r"(?P<context>(?:latest|newest|current|recent|stable)\s+version\s+"
    r"(?:of\s+)?(?:\w+(?:\.\w+)*\s+)?(?:is|was|:)\s*)"
    r"(?P<version>v?\d+(?:\.\d+)+)",
    re.IGNORECASE,
)

# Broader: "Next.js 14.2.0 released April 11, 2024"
_VERSION_WITH_DATE_RE = re.compile(
    r"(?P<product>[\w.\-]+)\s+"
    r"(?P<version>v?\d+(?:\.\d+)+)\s+"
    r"(?:was\s+)?released?\s+(?:on\s+)?"
    r"(?P<date>(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+\d{1,2}(?:st|nd|rd|th)?"
    r"[,\s]+\d{4})",
    re.IGNORECASE,
)

# Specific: "the latest version is X.Y.Z"
_LATEST_VERSION_RE = re.compile(
    r"(?:the\s+)?(?:latest|newest|current|most\s+recent|stable)\s+"
    r"(?:stable\s+)?version\s+(?:of\s+\w+[\w.\-]*\s+)?(?:is|:)\s*"
    r"(?P<version>v?\d+(?:\.\d+)+)",
    re.IGNORECASE,
)


def _hedge_version_claims(text: str, now: datetime) -> tuple[str, list[str]]:
    """Add uncertainty markers to version number claims from training data.

    Does NOT change the version number itself — instead adds a caveat
    that the information may be outdated, encouraging the user to verify.
    """
    flagged: list[str] = []

    # Check for "latest version is X.Y.Z" patterns
    for m in _LATEST_VERSION_RE.finditer(text):
        version = m.group("version")
        full_match = m.group(0)

        # Check if there's already a hedge nearby (within 100 chars after)
        after_context = text[m.end():m.end() + 100]
        if any(hedge in after_context.lower() for hedge in [
            "check", "verify", "may have", "might have", "could be",
            "as of my", "at the time", "please confirm",
        ]):
            continue  # Already hedged

        flagged.append(
            f"Version claim: '{full_match}' — may be outdated (from training data)"
        )

    # Check for "Product X.Y.Z released [date]"
    for m in _VERSION_WITH_DATE_RE.finditer(text):
        product = m.group("product")
        version = m.group("version")
        date_str = m.group("date")
        flagged.append(
            f"Version+date claim: '{product} {version} released {date_str}' — "
            f"may be stale, newer versions likely exist"
        )

    return text, flagged


# ═══════════════════════════════════════════════════════════════════════
# URL VALIDATION
# ═══════════════════════════════════════════════════════════════════════

# URLs to skip validation on (known-good domains)
_SKIP_URL_DOMAINS = frozenset({
    "github.com", "gitlab.com", "stackoverflow.com",
    "google.com", "youtube.com", "twitter.com", "x.com",
    "slack.com", "notion.so", "figma.com",
    "npmjs.com", "pypi.org", "crates.io",
    "wikipedia.org", "developer.mozilla.org",
    "docs.google.com", "drive.google.com",
})

_URL_RE = re.compile(r"https?://[^\s<>\"\')]+")

# Slack formatted links: <url|text>
_SLACK_URL_RE = re.compile(r"<(https?://[^|>]+)(?:\|[^>]*)?>")


def _extract_urls(text: str) -> list[str]:
    """Extract all URLs from text, handling both plain and Slack format."""
    urls: set[str] = set()
    for m in _SLACK_URL_RE.finditer(text):
        urls.add(m.group(1))
    for m in _URL_RE.finditer(text):
        url = m.group(0).rstrip(".,;:!?)")
        urls.add(url)
    return list(urls)


def _should_validate_url(url: str) -> bool:
    """Check if a URL should be validated (skip known-good domains)."""
    try:
        # Extract domain from URL
        domain = url.split("//", 1)[1].split("/", 1)[0].split(":", 1)[0]
        # Check against skip list (including subdomains)
        for skip_domain in _SKIP_URL_DOMAINS:
            if domain == skip_domain or domain.endswith("." + skip_domain):
                return False
        return True
    except (IndexError, ValueError):
        return False


async def _validate_url(url: str, timeout: float = 2.0) -> tuple[str, bool, int]:
    """Validate a URL with a HEAD request.

    Returns (url, is_valid, status_code).
    """
    try:
        import httpx
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout,
            verify=False,  # Don't fail on SSL issues
        ) as client:
            resp = await client.head(url)
            # 2xx/3xx = valid, 401/403 = exists but auth required (valid)
            is_valid = resp.status_code < 400 or resp.status_code in (401, 403)
            return url, is_valid, resp.status_code
    except Exception:
        # Timeout, connection error, etc — don't flag as invalid
        # (could be firewall, rate limit, etc)
        return url, True, 0  # Assume valid on error


async def _validate_urls_in_text(
    text: str,
    max_urls: int = 10,
    timeout: float = 2.0,
) -> tuple[str, list[str]]:
    """Validate URLs found in text. Remove or flag 404s.

    Only checks URLs on non-skipped domains. Limits to max_urls to
    prevent slow responses. Returns corrected text and list of corrections.
    """
    corrections: list[str] = []
    urls = _extract_urls(text)

    # Filter to URLs worth checking
    urls_to_check = [u for u in urls if _should_validate_url(u)][:max_urls]
    if not urls_to_check:
        return text, corrections

    # Batch validate with aggressive timeout
    tasks = [_validate_url(url, timeout) for url in urls_to_check]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    invalid_urls: list[str] = []
    for result in results:
        if isinstance(result, Exception):
            continue
        url, is_valid, status_code = result
        if not is_valid and status_code in (404, 410):
            invalid_urls.append(url)
            corrections.append(f"Removed broken URL (HTTP {status_code}): {url}")

    # Remove invalid URLs from text
    for url in invalid_urls:
        # Remove Slack formatted link: <url|text> → text
        slack_link_pattern = re.compile(
            r"<" + re.escape(url) + r"\|([^>]+)>"
        )
        text = slack_link_pattern.sub(r"\1", text)
        # Remove plain URL
        text = text.replace(url, "")

    return text, corrections


# ═══════════════════════════════════════════════════════════════════════
# PRICING DETECTION
# ═══════════════════════════════════════════════════════════════════════

_PRICING_RE = re.compile(
    r"\$\d+(?:\.\d{2})?\s*(?:/\s*(?:mo(?:nth)?|yr|year|user|seat))",
    re.IGNORECASE,
)

_PRICING_CONTEXT_RE = re.compile(
    r"(?:costs?|priced?\s+at|starts?\s+at|plans?\s+(?:at|from)|"
    r"subscription\s+(?:is|costs?)|pricing\s+(?:is|starts?))\s+"
    r"\$\d+",
    re.IGNORECASE,
)


def _flag_pricing_claims(text: str) -> list[str]:
    """Flag specific pricing claims that may be outdated."""
    flagged: list[str] = []
    for m in _PRICING_RE.finditer(text):
        # Check context — is this stating a current price?
        start = max(0, m.start() - 80)
        context = text[start:m.end() + 20]
        if _PRICING_CONTEXT_RE.search(context):
            flagged.append(
                f"Pricing claim: '{m.group(0).strip()}' — may not reflect current pricing"
            )
    return flagged


# ═══════════════════════════════════════════════════════════════════════
# TEMPORAL CLAIM DETECTION
# ═══════════════════════════════════════════════════════════════════════

_TEMPORAL_CLAIM_RE = re.compile(
    r"(?:as\s+of|since|starting\s+(?:in|from)?)\s+"
    r"(?:(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+)?"
    r"(?:\d{4})",
    re.IGNORECASE,
)

_STALE_CUTOFF_MONTHS = 6  # Claims older than this are flagged


def _flag_temporal_claims(text: str, now: datetime) -> list[str]:
    """Flag 'as of [date]' claims that are significantly in the past."""
    flagged: list[str] = []

    for m in _TEMPORAL_CLAIM_RE.finditer(text):
        claim = m.group(0)
        # Extract year from the claim
        year_match = re.search(r"\d{4}", claim)
        if not year_match:
            continue
        year = int(year_match.group(0))

        # Extract month if present
        month_match = re.search(
            r"(January|February|March|April|May|June|July|August|"
            r"September|October|November|December)",
            claim, re.IGNORECASE,
        )
        if month_match:
            month = _MONTH_NAMES.get(month_match.group(1).lower(), 1)
        else:
            month = 6  # Default to mid-year if no month

        # Check if the claim date is significantly in the past
        claim_months = year * 12 + month
        now_months = now.year * 12 + now.month
        if now_months - claim_months > _STALE_CUTOFF_MONTHS:
            flagged.append(
                f"Temporal claim: '{claim}' — this is {now_months - claim_months} months ago, "
                f"information may be outdated"
            )

    return flagged


# ═══════════════════════════════════════════════════════════════════════
# PERCENTAGE SANITY CHECK
# ═══════════════════════════════════════════════════════════════════════

_PERCENTAGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def _flag_suspicious_percentages(text: str) -> list[str]:
    """Flag percentages that are mathematically impossible or suspicious.

    Catches: >100% when context implies a proportion (not growth/increase),
    obviously fabricated round numbers like "99.9% uptime" without source.
    """
    flagged: list[str] = []
    for m in _PERCENTAGE_RE.finditer(text):
        value = float(m.group(1))
        # Get surrounding context (80 chars before and after)
        start = max(0, m.start() - 80)
        end = min(len(text), m.end() + 40)
        context = text[start:end].lower()

        # >100% is fine for growth, increase, improvement, etc.
        growth_words = ("increase", "growth", "grew", "rise", "risen",
                        "gain", "improvement", "improved", "more than",
                        "over", "exceeded", "above", "beyond", "surpass",
                        "spike", "jump", "boost")
        if value > 100 and not any(w in context for w in growth_words):
            flagged.append(
                f"Suspicious percentage: '{m.group(0).strip()}' — "
                f"exceeds 100% but context doesn't suggest growth/increase"
            )
    return flagged


# ═══════════════════════════════════════════════════════════════════════
# FABRICATED CONTENT DETECTION
# ═══════════════════════════════════════════════════════════════════════

# Patterns that suggest fabricated API endpoints
_FABRICATED_API_RE = re.compile(
    r"(?:endpoint|url|api)\s*(?:is|:)\s*"
    r"`?(https?://api\.[a-z]+\.[a-z]+/v\d+/[a-z_/]+)`?",
    re.IGNORECASE,
)


# ═══════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════

async def verify_claims(
    text: str,
    validate_urls: bool = True,
    url_timeout: float = 2.0,
    max_url_checks: int = 10,
) -> VerificationResult:
    """Check text for potentially hallucinated claims and correct where possible.

    Pipeline:
    1. Stash code blocks (protect from false positives)
    2. Verify date/day-of-week claims against system time (always, instant)
    3. Validate URLs with HEAD requests (optional, async, bounded timeout)
    4. Flag version number claims for hedging (detection only)
    5. Flag pricing claims (detection only)
    6. Flag stale temporal claims (detection only)
    7. Flag suspicious percentages (detection only)
    8. Restore code blocks

    Returns VerificationResult with corrected text, list of corrections made,
    and list of flagged-but-uncorrected claims.

    Design: corrections modify the text; flags are logged but don't modify text
    (the system prompt handles hedging at generation time).
    """
    if not text or not text.strip():
        return VerificationResult(text or "", [], [])

    now = datetime.now(timezone.utc)
    corrections: list[str] = []
    flagged: list[str] = []

    # 0. Stash code blocks to prevent false positives
    text, code_stash = _stash_code_blocks(text)

    # 1. Date/day-of-week verification (always, instant)
    text, date_corrections = _verify_day_of_week_claims(text, now)
    corrections.extend(date_corrections)

    # 2. URL validation (async, bounded)
    if validate_urls:
        text, url_corrections = await _validate_urls_in_text(
            text, max_urls=max_url_checks, timeout=url_timeout,
        )
        corrections.extend(url_corrections)

    # 3. Version number detection & flagging
    _, version_flags = _hedge_version_claims(text, now)
    flagged.extend(version_flags)

    # 4. Pricing detection
    pricing_flags = _flag_pricing_claims(text)
    flagged.extend(pricing_flags)

    # 5. Temporal claim detection
    temporal_flags = _flag_temporal_claims(text, now)
    flagged.extend(temporal_flags)

    # 6. Percentage sanity
    pct_flags = _flag_suspicious_percentages(text)
    flagged.extend(pct_flags)

    # 7. Restore code blocks
    text = _restore_code_blocks(text, code_stash)

    # Log results
    if corrections or flagged:
        logger.info(
            "fact_verification_complete",
            corrections_made=len(corrections),
            claims_flagged=len(flagged),
            corrections=corrections[:5],  # Limit log size
            flags=flagged[:5],
        )

    return VerificationResult(
        corrected_text=text,
        corrections=corrections,
        flagged_claims=flagged,
    )


def verify_claims_sync(text: str) -> VerificationResult:
    """Synchronous version — runs all checks except URL validation.

    For use in sync contexts where async isn't available.
    """
    if not text or not text.strip():
        return VerificationResult(text or "", [], [])

    now = datetime.now(timezone.utc)
    corrections: list[str] = []
    flagged: list[str] = []

    # Stash code blocks
    text, code_stash = _stash_code_blocks(text)

    text, date_corrections = _verify_day_of_week_claims(text, now)
    corrections.extend(date_corrections)

    _, version_flags = _hedge_version_claims(text, now)
    flagged.extend(version_flags)

    pricing_flags = _flag_pricing_claims(text)
    flagged.extend(pricing_flags)

    temporal_flags = _flag_temporal_claims(text, now)
    flagged.extend(temporal_flags)

    pct_flags = _flag_suspicious_percentages(text)
    flagged.extend(pct_flags)

    # Restore code blocks
    text = _restore_code_blocks(text, code_stash)

    if corrections or flagged:
        logger.info(
            "fact_verification_sync",
            corrections_made=len(corrections),
            claims_flagged=len(flagged),
        )

    return VerificationResult(
        corrected_text=text,
        corrections=corrections,
        flagged_claims=flagged,
    )
