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
_NUMBERED_RE = re.compile(r"^\d+[\.\)]\s+")
_DIVIDER_RE = re.compile(r"^-{3,}$")
_LINK_RE = re.compile(r"<(https?://[^|>]+)\|([^>]+)>")
_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```")
_CONTEXT_RE = re.compile(r"^_([^_]+)_$")

MIN_BLOCKS_THRESHOLD = 40

_RICH_SIGNAL_RE = re.compile(
    r"\*[^*]+\*|[\u2022\-\*]\s+|\d+[\.\)]\s+|```|^-{3,}$",
    re.MULTILINE,
)


def text_to_blocks(text: str) -> list[dict[str, Any]] | None:
    """Convert processed mrkdwn text into Slack Block Kit blocks.

    Returns None only for truly simple one-liners with no formatting.
    """
    if not text or len(text) < MIN_BLOCKS_THRESHOLD:
        return None

    has_rich_signals = bool(_RICH_SIGNAL_RE.search(text))
    has_structure = text.count("\n") >= 2

    if not has_rich_signals and not has_structure:
        return None

    blocks: list[dict[str, Any]] = []
    lines = text.split("\n")
    current_section: list[str] = []

    def _flush_section() -> None:
        if not current_section:
            return
        section_text = "\n".join(current_section).strip()
        if not section_text:
            current_section.clear()
            return

        context_match = _CONTEXT_RE.match(section_text)
        if context_match and "\n" not in section_text:
            blocks.append({
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": section_text},
                ],
            })
        else:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": _truncate(section_text, 3000),
                },
            })
        current_section.clear()

    for line in lines:
        stripped = line.strip()

        if _DIVIDER_RE.match(stripped):
            _flush_section()
            blocks.append({"type": "divider"})
            continue

        header_match = _HEADER_RE.match(stripped)
        if header_match and len(stripped) < 120:
            heading = header_match.group(1).strip()
            if (
                not _BULLET_RE.match(stripped)
                and not any(c in heading for c in ["\u2014", "\u2022"])
            ):
                _flush_section()
                blocks.append({
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": heading[:150],
                    },
                })
                continue

        current_section.append(line)

        if len("\n".join(current_section)) > 2800:
            _flush_section()

    _flush_section()

    if not blocks:
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
