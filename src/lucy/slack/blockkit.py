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
MIN_NEWLINES_FOR_BLOCKS = 2
MAX_SECTION_TEXT_CHARS = 3000
MAX_HEADER_LENGTH = 120
MAX_HEADING_DISPLAY_LENGTH = 150
MAX_SECTION_FLUSH_CHARS = 2800
MAX_BLOCKS_PER_MESSAGE = 50
_TRUNCATION_BUFFER = 30
_TRUNCATION_SPLIT_RATIO = 0.5


def text_to_blocks(text: str) -> list[dict[str, Any]] | None:
    """Convert processed mrkdwn text into Slack Block Kit blocks.

    Returns None if the text is too short/simple to benefit from blocks,
    so the caller can fall back to plain text.
    """
    if not text or len(text) < MIN_BLOCKS_THRESHOLD:
        return None

    if text.count("\n") < MIN_NEWLINES_FOR_BLOCKS and not _BULLET_RE.search(text):
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
                "text": {"type": "mrkdwn", "text": _truncate(section_text, MAX_SECTION_TEXT_CHARS)},
            })
        current_section.clear()

    for line in lines:
        stripped = line.strip()

        if _DIVIDER_RE.match(stripped):
            _flush_section()
            blocks.append({"type": "divider"})
            continue

        header_match = _HEADER_RE.match(stripped)
        if header_match and len(stripped) < MAX_HEADER_LENGTH and not _BULLET_RE.match(stripped):
            heading = header_match.group(1).strip()
            if heading and not any(c in heading for c in ["—", "•", ":"]):
                _flush_section()
                blocks.append({
                    "type": "header",
                    "text": {"type": "plain_text", "text": heading[:MAX_HEADING_DISPLAY_LENGTH]},
                })
                continue

        current_section.append(line)

        if len("\n".join(current_section)) > MAX_SECTION_FLUSH_CHARS:
            _flush_section()

    _flush_section()

    if len(blocks) <= 1:
        return None

    if len(blocks) > MAX_BLOCKS_PER_MESSAGE:
        blocks = blocks[:MAX_BLOCKS_PER_MESSAGE - 1]
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
            "text": {"type": "mrkdwn", "text": _truncate(details, MAX_SECTION_TEXT_CHARS)},
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
    cutoff = max_len - _TRUNCATION_BUFFER
    last_para = text.rfind("\n\n", 0, cutoff)
    if last_para > cutoff * _TRUNCATION_SPLIT_RATIO:
        return text[:last_para] + "\n\n_...continued in next message_"
    last_newline = text.rfind("\n", 0, cutoff)
    if last_newline > cutoff * _TRUNCATION_SPLIT_RATIO:
        return text[:last_newline] + "\n\n_...continued in next message_"
    return text[:cutoff] + "\n\n_...continued in next message_"
