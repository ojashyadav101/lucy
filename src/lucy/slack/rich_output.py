"""Rich output pipeline for Lucy's Slack messages.

Enhances Lucy's existing Block Kit (blockkit.py) with:
1. Anchor text links — raw URLs become clickable display text
2. Section emojis — strategic emoji prefixes for headers
3. Block Kit enhancement — post-processing of blocks
4. Response splitting — long messages split at natural break points
"""

from __future__ import annotations

import re
from typing import Any

import structlog

logger = structlog.get_logger()

# ═══════════════════════════════════════════════════════════════════════════
# SECTION EMOJI MAP
# ═══════════════════════════════════════════════════════════════════════════

_SECTION_EMOJI: dict[str, str] = {
    # Data & metrics
    "summary": "\U0001f4ca",
    "metrics": "\U0001f4ca",
    "analytics": "\U0001f4ca",
    "data": "\U0001f4ca",
    "breakdown": "\U0001f4ca",
    "report": "\U0001f4ca",
    # Status
    "result": "\u2705",
    "success": "\u2705",
    "complete": "\u2705",
    "done": "\u2705",
    # Recommendations & actions
    "recommendation": "\U0001f3af",
    "action": "\U0001f3af",
    "next step": "\U0001f3af",
    "strategy": "\U0001f3af",
    # Insights
    "tip": "\U0001f4a1",
    "insight": "\U0001f4a1",
    "takeaway": "\U0001f4a1",
    "key finding": "\U0001f4a1",
    # Investigation
    "finding": "\U0001f50d",
    "analysis": "\U0001f50d",
    # Warnings & issues
    "warning": "\u26a0\ufe0f",
    "caution": "\u26a0\ufe0f",
    "issue": "\u26a0\ufe0f",
    "error": "\U0001f534",
    # Calendar & time
    "schedule": "\U0001f4c5",
    "calendar": "\U0001f4c5",
    "meeting": "\U0001f4c5",
    # Money & revenue
    "revenue": "\U0001f4b0",
    "pricing": "\U0001f4b0",
    "billing": "\U0001f4b0",
    # Other
    "overview": "\U0001f4cb",
    "detail": "\U0001f4c4",
    "update": "\U0001f4cc",
    "change": "\U0001f504",
    "note": "\U0001f4dd",
    "highlight": "\U0001f3c6",
}

_SECTION_EMOJI_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _SECTION_EMOJI) + r")\b",
    re.IGNORECASE,
)


def add_section_emoji(header: str) -> str:
    """Add a strategic emoji prefix to a section header if appropriate."""
    if any(ord(c) > 0x2600 for c in header):
        return header

    match = _SECTION_EMOJI_RE.search(header.lower())
    if match:
        emoji = _SECTION_EMOJI[match.group(1).lower()]
        return f"{emoji} {header}"

    return header


# ═══════════════════════════════════════════════════════════════════════════
# LINK FORMATTING
# ═══════════════════════════════════════════════════════════════════════════

_RAW_URL_RE = re.compile(
    r"(?<![<|])"
    r"(https?://[^\s)>\]\"']+)"
    r"(?![|>])"
)

_DOMAIN_NAMES: dict[str, str] = {
    "github.com": "GitHub",
    "linear.app": "Linear",
    "notion.so": "Notion",
    "docs.google.com": "Google Docs",
    "sheets.google.com": "Google Sheets",
    "drive.google.com": "Google Drive",
    "figma.com": "Figma",
    "slack.com": "Slack",
    "clickup.com": "ClickUp",
    "vercel.com": "Vercel",
    "stripe.com": "Stripe",
    "composio.dev": "Connect here",
    "connect.composio.dev": "Connect here",
    "auth.composio.dev": "Connect here",
    "zeeya.app": "Live App",
    "polar.sh": "Polar",
    "app.polar.sh": "Polar Dashboard",
    "getviktor.com": "Viktor",
    "app.getviktor.com": "Viktor Settings",
    "serprisingly.com": "Serprisingly",
}


def format_links(text: str) -> str:
    """Convert raw URLs to Slack anchor-text links."""
    # Pre-clean: strip bold markers that got attached to URLs
    text = re.sub(r"\*(https?://[^\s*|>]+)\*", r"\1", text)

    def _replace_url(match: re.Match[str]) -> str:
        url = match.group(1).rstrip("*")

        domain_match = re.search(r"https?://(?:www\.)?([^/]+)", url)
        if not domain_match:
            return url
        domain = domain_match.group(1)

        for domain_key, friendly_name in _DOMAIN_NAMES.items():
            if domain_key in domain:
                if "github.com" in domain:
                    pr_match = re.search(r"/pull/(\d+)", url)
                    if pr_match:
                        return f"<{url}|GitHub PR #{pr_match.group(1)}>"
                    issue_match = re.search(r"/issues/(\d+)", url)
                    if issue_match:
                        return f"<{url}|GitHub Issue #{issue_match.group(1)}>"
                    repo_match = re.search(r"github\.com/([^/]+/[^/]+)", url)
                    if repo_match:
                        return f"<{url}|{repo_match.group(1)} on GitHub>"

                if "linear.app" in domain:
                    issue_match = re.search(r"/issue/([A-Z]+-\d+)", url)
                    if issue_match:
                        return f"<{url}|{issue_match.group(1)} on Linear>"

                if "zeeya.app" in domain:
                    slug = domain.split(".")[0] if "." in domain else domain
                    readable = slug.replace("-", " ").rsplit(" ", 1)[0].strip().title()
                    if readable:
                        return f"<{url}|{readable} on Zeeya>"

                return f"<{url}|{friendly_name}>"

        return f"<{url}|{domain}>"

    text = _RAW_URL_RE.sub(_replace_url, text)

    text = re.sub(
        r"<(https?://[^|>]*composio\.dev[^|>]*)>",
        r"<\1|Connect here>",
        text,
    )

    return text

# ═══════════════════════════════════════════════════════════════════════════
# ENHANCED BLOCK KIT
# ═══════════════════════════════════════════════════════════════════════════

def enhance_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Post-process Block Kit blocks for richer formatting.

    Enhancements:
    - Adds emoji prefixes to header blocks based on content keywords
    - Converts raw URLs to anchor-text links in section blocks
    - Detects footer/context lines and converts to context blocks
    """
    enhanced = []
    for block in blocks:
        block_type = block.get("type")

        if block_type == "header":
            text_obj = block.get("text", {})
            if text_obj.get("type") == "plain_text":
                original = text_obj.get("text", "")
                text_obj["text"] = add_section_emoji(original)
            enhanced.append(block)

        elif block_type == "section":
            text_obj = block.get("text", {})
            if text_obj.get("type") == "mrkdwn":
                content = text_obj.get("text", "")
                content = format_links(content)
                # Detect context footer lines (e.g. "Live from Polar API • ...")
                content, footer = _extract_context_footer(content)
                text_obj["text"] = content
                enhanced.append(block)
                if footer:
                    enhanced.append({
                        "type": "context",
                        "elements": [
                            {"type": "mrkdwn", "text": footer},
                        ],
                    })
            else:
                enhanced.append(block)

        else:
            enhanced.append(block)

    return enhanced


def _extract_context_footer(text: str) -> tuple[str, str | None]:
    """Extract a context footer from the end of a section.

    Detects patterns like:
    - "Live from Polar API • Read-only • Feb 14, 2026"
    - "_Data from Clerk • Last synced 2 min ago_"

    Returns (main_text, footer_or_none).
    """
    lines = text.rstrip().split("\n")
    if len(lines) < 2:
        return text, None

    last_line = lines[-1].strip()
    # Match lines that look like metadata footers
    footer_patterns = [
        r"^_?(?:Live from|Data from|Source:|Last (?:synced|updated)|Pulled from|From) .+_?$",
        r"^_?.+\s+\u2022\s+.+\s+\u2022\s+.+_?$",  # "X • Y • Z" format
    ]
    for pattern in footer_patterns:
        if re.match(pattern, last_line, re.IGNORECASE):
            main_text = "\n".join(lines[:-1]).rstrip()
            footer = last_line.strip("_").strip()
            return main_text, f"_{footer}_"

    return text, None


# ═══════════════════════════════════════════════════════════════════════════
# RESPONSE LENGTH MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════

MAX_SINGLE_MESSAGE_CHARS = 6000
_SPLIT_MIN_RATIO = 0.3
_SPLIT_MIN_RATIO_NEWLINE = 0.2


def should_split_response(text: str) -> bool:
    """Check if a response needs to be split into multiple messages."""
    return len(text) > MAX_SINGLE_MESSAGE_CHARS


def _is_inside_code_block(text: str, position: int) -> bool:
    """Check if a position is inside an unclosed ``` code block."""
    count = text[:position].count("```")
    return count % 2 == 1


def split_response(text: str) -> list[str]:
    """Split a long response into chunks at natural break points.

    Avoids splitting inside code blocks (``` ... ```) to preserve
    formatting integrity.
    """
    if len(text) <= MAX_SINGLE_MESSAGE_CHARS:
        return [text]

    chunks: list[str] = []
    remaining = text

    while len(remaining) > MAX_SINGLE_MESSAGE_CHARS:
        limit = MAX_SINGLE_MESSAGE_CHARS
        search_text = remaining[:limit]

        def _try_split(pattern: str, min_ratio: float) -> int:
            idx = search_text.rfind(pattern)
            if idx > limit * min_ratio and not _is_inside_code_block(remaining, idx):
                return idx
            return -1

        split_idx = _try_split("\n*", _SPLIT_MIN_RATIO)
        if split_idx < 0:
            split_idx = _try_split("\n---", _SPLIT_MIN_RATIO)
        if split_idx < 0:
            split_idx = _try_split("\n\n", _SPLIT_MIN_RATIO)
        if split_idx < 0:
            split_idx = _try_split("\n", _SPLIT_MIN_RATIO_NEWLINE)

        if split_idx >= 0:
            chunks.append(remaining[:split_idx].rstrip())
            remaining = remaining[split_idx:].lstrip("\n")
        else:
            chunks.append(remaining[:limit])
            remaining = remaining[limit:]

    if remaining.strip():
        chunks.append(remaining.strip())

    return chunks
