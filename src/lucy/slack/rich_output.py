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
    "summary": "\U0001f4ca",
    "result": "\u2705",
    "finding": "\U0001f50d",
    "warning": "\u26a0\ufe0f",
    "error": "\U0001f534",
    "success": "\u2705",
    "next step": "\u27a1\ufe0f",
    "action": "\U0001f3af",
    "recommendation": "\U0001f4a1",
    "tip": "\U0001f4a1",
    "overview": "\U0001f4cb",
    "detail": "\U0001f4c4",
    "update": "\U0001f4cc",
    "change": "\U0001f504",
    "note": "\U0001f4dd",
}

_SECTION_EMOJI_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _SECTION_EMOJI) + r")\b",
    re.IGNORECASE,
)


def add_section_emoji(header: str) -> str:
    """Add a strategic emoji prefix to a section header if appropriate."""
    if any(ord(c) > 0x1F000 for c in header):
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
}


def format_links(text: str) -> str:
    """Convert raw URLs to Slack anchor-text links."""
    def _replace_url(match: re.Match[str]) -> str:
        url = match.group(1)

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

                return f"<{url}|{friendly_name}>"

        return f"<{url}|{domain}>"

    return _RAW_URL_RE.sub(_replace_url, text)


# ═══════════════════════════════════════════════════════════════════════════
# ENHANCED BLOCK KIT
# ═══════════════════════════════════════════════════════════════════════════

def enhance_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Post-process Block Kit blocks for richer formatting."""
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
                text_obj["text"] = format_links(text_obj.get("text", ""))
            enhanced.append(block)

        else:
            enhanced.append(block)

    return enhanced


# ═══════════════════════════════════════════════════════════════════════════
# RESPONSE LENGTH MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════

MAX_SINGLE_MESSAGE_CHARS = 3000


def should_split_response(text: str) -> bool:
    """Check if a response needs to be split into multiple messages."""
    return len(text) > MAX_SINGLE_MESSAGE_CHARS


def split_response(text: str) -> list[str]:
    """Split a long response into chunks at natural break points."""
    if len(text) <= MAX_SINGLE_MESSAGE_CHARS:
        return [text]

    chunks: list[str] = []
    remaining = text

    while len(remaining) > MAX_SINGLE_MESSAGE_CHARS:
        limit = MAX_SINGLE_MESSAGE_CHARS
        search_text = remaining[:limit]

        split_idx = search_text.rfind("\n*")
        if split_idx > limit * 0.3:
            chunks.append(remaining[:split_idx].rstrip())
            remaining = remaining[split_idx:].lstrip("\n")
            continue

        split_idx = search_text.rfind("\n---")
        if split_idx > limit * 0.3:
            chunks.append(remaining[:split_idx].rstrip())
            remaining = remaining[split_idx:].lstrip("\n")
            continue

        split_idx = search_text.rfind("\n\n")
        if split_idx > limit * 0.3:
            chunks.append(remaining[:split_idx].rstrip())
            remaining = remaining[split_idx:].lstrip("\n")
            continue

        split_idx = search_text.rfind("\n")
        if split_idx > limit * 0.2:
            chunks.append(remaining[:split_idx].rstrip())
            remaining = remaining[split_idx:].lstrip("\n")
            continue

        chunks.append(remaining[:limit])
        remaining = remaining[limit:]

    if remaining.strip():
        chunks.append(remaining.strip())

    return chunks
