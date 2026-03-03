"""Response escalation and quality-gate correction for Lucy's agent loop.

When the heuristic quality gate flags an issue in the agent's response,
`escalate_response` sends the response to the frontier model for correction.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

logger = structlog.get_logger()


def strip_quality_gate_meta(text: str) -> str:
    """Strip meta-commentary from quality gate / self-critique output.

    Quality checkers sometimes prepend explanations like:
    - "Here is the corrected version:"
    - "The original response had issues with..."
    - "I've fixed the following problems:"

    This strips those so only the actual corrected text remains.
    """
    preamble_patterns = [
        re.compile(
            r"^(?:Here(?:'s| is) (?:the |a )?(?:corrected|improved|fixed|updated)"
            r"(?: version| response| text)?[:\s]*\n+)",
            re.IGNORECASE | re.MULTILINE,
        ),
        re.compile(
            r"^(?:(?:The |My )?(?:original|previous|initial) response"
            r"[^.]*\.?\s*\n+)",
            re.IGNORECASE | re.MULTILINE,
        ),
        re.compile(
            r"^(?:I(?:'ve| have) (?:corrected|fixed|updated|improved)"
            r"[^.]*\.?\s*\n+)",
            re.IGNORECASE | re.MULTILINE,
        ),
        re.compile(
            r"^(?:Corrected (?:version|response|text)[:\s]*\n+)",
            re.IGNORECASE | re.MULTILINE,
        ),
    ]
    for pattern in preamble_patterns:
        text = pattern.sub("", text, count=1)

    trailing_patterns = [
        re.compile(
            r"\n+(?:Note|Changes?|I (?:changed|replaced|fixed))[:\s][^\n]+$",
            re.IGNORECASE,
        ),
    ]
    for pattern in trailing_patterns:
        text = pattern.sub("", text)

    return text.strip()


async def escalate_response(
    user_message: str,
    original_response: str,
    issues: str,
    workspace_id: str,
) -> str | None:
    """Ask frontier model to correct a response flagged by the quality gate.

    Single LLM call (~$0.003) — only triggered when heuristics detect
    a likely error. Returns corrected text, or None if original is fine.
    """
    from lucy.core.openclaw import ChatConfig, get_openclaw_client
    from lucy.pipeline.router import MODEL_TIERS

    _utc = datetime.now(UTC)
    _ist = _utc.replace(tzinfo=None) + timedelta(hours=5, minutes=30)
    time_ctx = (
        f"Current date: {_utc.strftime('%A, %B %d, %Y')}. "
        f"Current time: {_utc.strftime('%I:%M %p UTC')} / "
        f"{_ist.strftime('%I:%M %p IST')}."
    )

    correction_prompt = (
        f'A user sent this message:\n"{user_message}"\n\n'
        f'An AI assistant (Lucy) responded:\n"{original_response}"\n\n'
        f"Quality check detected these issues:\n{issues}\n\n"
        f"Context Lucy has access to:\n"
        f"- {time_ctx}\n"
        f"- Lucy has 100+ tools for calendar, email, search, etc.\n"
        f"- Lucy should NEVER say 'As an AI, I can't' — she is a "
        f"high-agency assistant that always finds a way.\n\n"
        f"If the response has real problems, provide a CORRECTED "
        f"response that addresses the user's actual request. "
        f"Keep the same warm, sharp tone. Do NOT prefix with "
        f"'CORRECTED RESPONSE:' — just write the corrected text.\n\n"
        f"If the original response is actually fine and the issues "
        f"are false positives, respond with exactly: RESPONSE_OK\n\n"
        f"CRITICAL: Output ONLY the corrected text — no "
        f"meta-commentary, no explanations of what you changed, "
        f"no 'Here is the corrected version:', no references to "
        f"'the original response'. Just the clean, corrected text "
        f"that a user would see."
    )

    try:
        client = await get_openclaw_client()
        result = await asyncio.wait_for(
            client.chat_completion(
                messages=[{"role": "user", "content": correction_prompt}],
                config=ChatConfig(
                    model=MODEL_TIERS["frontier"],
                    system_prompt=(
                        "You are a quality auditor for an AI assistant "
                        "named Lucy. Your job is to catch and fix errors "
                        "in her responses, especially service name "
                        "confusion (e.g., Clerk ≠ MoonClerk), wrong "
                        "suggestions, and hallucinated capabilities. "
                        "Be concise. If the response is fine, say "
                        "RESPONSE_OK. "
                        "IMPORTANT: When providing corrections, output "
                        "ONLY the corrected user-facing text. Never "
                        "include meta-commentary like 'The original "
                        "response was incorrect' or 'I've corrected "
                        "the following'. The output goes directly to "
                        "the user."
                    ),
                    max_tokens=4096,
                ),
            ),
            timeout=15.0,
        )

        corrected = (result.content or "").strip()
        if corrected.startswith("RESPONSE_OK"):
            logger.info("quality_gate_original_ok", workspace_id=workspace_id)
            return None

        corrected = strip_quality_gate_meta(corrected)

        logger.info(
            "quality_gate_corrected",
            original_len=len(original_response),
            corrected_len=len(corrected),
            workspace_id=workspace_id,
        )
        return corrected

    except Exception as exc:
        logger.warning(
            "quality_gate_escalation_failed",
            error=str(exc) or type(exc).__name__,
            workspace_id=workspace_id,
        )
        return None
