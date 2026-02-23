"""Block Kit formatter for Lucy's Slack messages.

Converts Lucy's text responses into structured Slack Block Kit
payloads (sections, headers, dividers, context blocks, buttons).

Falls back to plain mrkdwn text if the response is short or simple.
"""

from __future__ import annotations

import re
from typing import Any


_HEADER_RE = re.compile(r"^\*([^*]+)\*$")
_BULLET_RE = re.compile(r"^[\u2022\-\*]\s+")
_DIVIDER_RE = re.compile(r"^-{3,}$")
_LINK_RE = re.compile(r"<(https?://[^|>]+)\|([^>]+)>")
_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```")

MIN_BLOCKS_THRESHOLD = 80


def text_to_blocks(text: str) -> list[dict[str, Any]] | None:
    """Convert processed mrkdwn text into Slack Block Kit blocks.

    Returns None if the text is too short/simple to benefit from blocks,
    so the caller can fall back to plain text.
    """
    if not text or len(text) < MIN_BLOCKS_THRESHOLD:
        return None

    if text.count("\n") < 2 and not _BULLET_RE.search(text):
        return None

    blocks: list[dict[str, Any]] = []
    lines = text.split("\n")
    current_section: list[str] = []

    def _flush_section() -> None:
        if not current_section:
            return
        section_text = "\n".join(current_section).strip()
        if section_text:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": _truncate(section_text, 3000)},
            })
        current_section.clear()

    for line in lines:
        stripped = line.strip()

        if _DIVIDER_RE.match(stripped):
            _flush_section()
            blocks.append({"type": "divider"})
            continue

        header_match = _HEADER_RE.match(stripped)
        if header_match and len(stripped) < 120 and not _BULLET_RE.match(stripped):
            heading = header_match.group(1).strip()
            if not any(c in heading for c in ["—", "•", ":"]):
                _flush_section()
                blocks.append({
                    "type": "header",
                    "text": {"type": "plain_text", "text": heading[:150]},
                })
                continue

        current_section.append(line)

        if len("\n".join(current_section)) > 2800:
            _flush_section()

    _flush_section()

    if len(blocks) <= 1:
        return None

    if len(blocks) > 50:
        blocks = blocks[:49]
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "_...continued_"},
        })

    return blocks


def approval_blocks(
    action_summary: str,
    action_id: str,
    details: str | None = None,
) -> list[dict[str, Any]]:
    """Build Block Kit blocks for a human-in-the-loop approval prompt.

    Includes Approve/Cancel buttons with action metadata.
    """
    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":warning: *Confirmation Required*\n\n{action_summary}",
            },
        },
    ]

    if details:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": _truncate(details, 3000)},
        })

    blocks.append({
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Approve"},
                "style": "primary",
                "action_id": f"lucy_action_approve_{action_id}",
                "value": action_id,
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Cancel"},
                "style": "danger",
                "action_id": f"lucy_action_cancel_{action_id}",
                "value": action_id,
            },
        ],
    })

    return blocks


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 20] + "\n\n_...truncated_"
