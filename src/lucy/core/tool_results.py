"""Tool result processing utilities for Lucy's agent loop.

Contains pure functions for sanitizing, compacting, and summarizing
tool call outputs before feeding them back into the LLM context.

Also includes _trim_tool_results, which uses the fast LLM tier to
summarize older tool results when the context window is growing large.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from typing import Any

import structlog

logger = structlog.get_logger()


# ── Regex patterns ─────────────────────────────────────────────────────────────

_INTERNAL_PATH_RE = re.compile(r"/home/user/[^\s\"',}\]]+")
_WORKSPACE_PATH_RE = re.compile(r"workspaces?/[^\s\"',}\]]+")
_COMPOSIO_NAME_RE = re.compile(r"COMPOSIO_\w+")

_CONTROL_TOKEN_RE = re.compile(
    r"<\|[a-z_]+\|>"
    r"|<\|tool_call[^>]*\|>"
    r"|<\|tool_calls_section[^>]*\|>"
    r"|<\|im_[a-z]+\|>"
    r"|<\|end\|>"
    r"|<\|pad\|>"
    r"|<\|assistant\|>"
    r"|<\|user\|>"
    r"|<\|system\|>",
)

_TOOL_CALL_BLOCK_RE = re.compile(
    r"<\|tool_calls_section_begin\|>.*?(?:<\|tool_calls_section_end\|>|$)",
    re.DOTALL,
)

_NOISY_KEYS = frozenset(
    {
        "public_metadata",
        "private_metadata",
        "unsafe_metadata",
        "external_accounts",
        "phone_numbers",
        "web3_wallets",
        "saml_accounts",
        "passkeys",
        "totp_enabled",
        "backup_code_enabled",
        "two_factor_enabled",
        "create_organization_enabled",
        "delete_self_enabled",
        "legal_accepted_at",
        "last_active_at",
        "profile_image_url",
        "image_url",
        "has_image",
        "updated_at",
        "last_sign_in_at",
        "object",
        "verification",
        "linked_to",
        "reserved",
    }
)


# ── Sanitization ──────────────────────────────────────────────────────────────

def strip_control_tokens(text: str) -> str:
    """Remove raw model control tokens that leaked into output."""
    text = _TOOL_CALL_BLOCK_RE.sub("", text)
    text = _CONTROL_TOKEN_RE.sub("", text)
    return text.strip()


def sanitize_tool_output(text: str) -> str:
    """Remove internal file paths and tool names from tool output."""
    text = _INTERNAL_PATH_RE.sub("[file]", text)
    text = _WORKSPACE_PATH_RE.sub("[workspace]", text)
    text = _COMPOSIO_NAME_RE.sub("[action]", text)
    return text


# ── Compaction ────────────────────────────────────────────────────────────────

def compact_data(data: Any, depth: int = 0) -> Any:
    """Strip verbose/noisy fields from API results to fit more
    useful data within the context limit. Operates recursively
    on dicts and lists up to depth 4.
    """
    if depth > 4:
        return data
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            if k in _NOISY_KEYS:
                continue
            out[k] = compact_data(v, depth + 1)
        return out
    if isinstance(data, list):
        return [compact_data(item, depth + 1) for item in data]
    return data


# ── Structured summary ────────────────────────────────────────────────────────

def extract_structured_summary(
    data: Any,
    *,
    min_items: int = 10,
    sample_count: int = 5,
) -> dict[str, Any] | None:
    """Detect list-of-dicts in a tool result and compute aggregates.

    Instead of truncating large API responses (which forces the LLM to
    infer numbers from fragments), this computes exact counts, sums,
    averages, and distributions in Python and returns them alongside a
    small sample so the LLM can report accurate data.

    Returns ``None`` when the data isn't a summarizable collection.
    """
    items: list[dict[str, Any]] | None = None
    wrapper_keys: dict[str, Any] = {}

    if isinstance(data, list) and len(data) > min_items:
        items = data
    elif isinstance(data, dict):
        for key, val in data.items():
            if isinstance(val, list) and len(val) > min_items and items is None:
                items = val
            else:
                wrapper_keys[key] = val

    if not items or not isinstance(items[0], dict):
        return None

    sample_keys = list(items[0].keys())
    fields: dict[str, dict[str, Any]] = {}

    for key in sample_keys:
        values = [item[key] for item in items if item.get(key) is not None]
        if not values:
            continue

        if all(isinstance(v, (int, float)) for v in values):
            fields[key] = {
                "type": "numeric",
                "count": len(values),
                "sum": sum(values),
                "min": min(values),
                "max": max(values),
                "avg": round(sum(values) / len(values), 2),
            }
        elif all(isinstance(v, str) for v in values):
            counts = Counter(values)
            fields[key] = {
                "type": "categorical",
                "unique_count": len(counts),
                "top_values": dict(counts.most_common(10)),
            }

    return {
        "_summary": True,
        "total_count": len(items),
        "fields": fields,
        "sample_items": items[:sample_count],
        "wrapper": wrapper_keys,
    }


# ── LLM-assisted trimming ─────────────────────────────────────────────────────

async def trim_tool_results(
    messages: list[dict[str, Any]],
    max_result_chars: int = 2000,
) -> list[dict[str, Any]]:
    """Trim old tool results to reduce payload size.

    Uses the fast tier model via OpenClaw to summarize older tool outputs
    if they are large, keeping the narrative intact without exploding the
    context window.
    """
    from lucy.config import settings
    from lucy.core.openclaw import ChatConfig, get_openclaw_client

    trimmed: list[dict[str, Any]] = []
    total_tool_results = sum(1 for m in messages if m.get("role") == "tool")
    keep_last_n = min(2, total_tool_results)
    trim_threshold = total_tool_results - keep_last_n
    tool_idx = 0

    for msg in messages:
        if msg.get("role") == "tool":
            if tool_idx < trim_threshold:
                content = msg.get("content", "")
                if len(content) > max_result_chars:
                    try:
                        prompt = (
                            f"Summarize this tool output concisely, preserving "
                            f"key errors, file paths, and success/fail signals. "
                            f"Keep it under {max_result_chars} characters."
                            f"\n\n{content[:10000]}"
                        )
                        client = await get_openclaw_client()
                        result = await asyncio.wait_for(
                            client.chat_completion(
                                messages=[{"role": "user", "content": prompt}],
                                config=ChatConfig(
                                    model=settings.model_tier_fast,
                                    system_prompt="You are a concise summarizer.",
                                    max_tokens=500,
                                ),
                            ),
                            timeout=10.0,
                        )
                        summary = result.content or ""
                        prefix = "[LLM SUMMARIZED]: "
                        max_summary = max_result_chars - len(prefix)
                        if len(summary) > max_summary:
                            summary = summary[:max_summary]
                        msg = {**msg, "content": f"{prefix}{summary}"}
                    except Exception as e:
                        logger.warning("llm_condensation_failed", error=str(e))
                        msg = {**msg, "content": content[:max_result_chars] + "...(summarized)"}
            tool_idx += 1
            trimmed.append(msg)
        else:
            trimmed.append(msg)

    return trimmed
