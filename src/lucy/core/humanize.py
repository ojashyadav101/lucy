"""Route every user-facing message through a lightweight LLM pass.

Problem:
    Hardcoded template messages feel robotic and break the illusion that
    Lucy is an intelligent coworker. A real teammate never says the same
    thing word-for-word twice.

Solution:
    - Pre-generate varied message pools at startup using the cheapest model.
    - Provide a real-time `humanize()` function for dynamic, context-aware
      messages that don't fit a predefined category.
    - Always fall back to the original intent text if the LLM is unavailable.

Cost:
    minimax at $0.30/M input → ~$0.00006 per humanize call.
    Pool initialization: single batch call at startup → ~$0.002.
"""

from __future__ import annotations

import asyncio
import json
import random
from typing import Any

import structlog

logger = structlog.get_logger()

_REPHRASER_PROMPT = (
    "You are Lucy, a warm, approachable AI coworker. "
    "Rephrase the following message naturally. "
    "Keep it to 1-2 sentences max. Sound like a helpful colleague, "
    "not a chatbot or template. Never use emojis unless the user did. "
    "Your response IS the message — output nothing else."
)

_POOL_GENERATOR_PROMPT = (
    "You are Lucy, a warm, approachable AI coworker. "
    "For each category below, generate exactly 6 unique, natural "
    "variations of the described message. Each variation should feel "
    "like it was written by a real person — slightly different tone, "
    "phrasing, and word choice. No emojis. "
    "Return ONLY valid JSON: {\"category_name\": [\"variation1\", ...]}. "
    "No markdown, no explanation."
)

POOL_CATEGORIES: dict[str, str] = {
    "greeting": (
        "Say hi to a colleague who just messaged you. "
        "Be welcoming and ask what they need help with. Max 1 sentence."
    ),
    "status": (
        "Let a colleague know you're online and available. "
        "Keep it short and casual. Max 1 sentence."
    ),
    "help": (
        "Briefly explain your core capabilities: research, integrations "
        "(calendar, email, GitHub, etc), document creation, code help, "
        "and automation. 2-4 bullet points. Keep it friendly."
    ),
    "progress_early": (
        "You just started working on someone's request. "
        "Let them know you're on it. Max 1 sentence."
    ),
    "progress_mid": (
        "You're making good progress on a task. "
        "Give a brief update. Max 1 sentence."
    ),
    "progress_late": (
        "A task is taking longer than usual but you're close. "
        "Reassure them. Max 1 sentence."
    ),
    "progress_final": (
        "A thorough task is almost done. "
        "Let them know you're wrapping up. Max 1 sentence."
    ),
    "task_cancelled": (
        "Confirm you've stopped working on the task they cancelled. "
        "Max 1 sentence."
    ),
    "task_background_ack": (
        "Acknowledge you're starting a background task. Let them know "
        "you'll post updates and they can keep chatting. Max 2 sentences."
    ),
    "error_timeout": (
        "A request is taking longer than expected. Reassure them "
        "you're still on it. Max 1 sentence."
    ),
    "error_rate_limit": (
        "You're handling many requests. Ask for a brief moment. "
        "Max 1 sentence."
    ),
    "error_connection": (
        "You can't reach an external service right now. "
        "Let them know you'll retry. Max 1 sentence."
    ),
    "error_generic": (
        "Something went wrong but you're handling it. "
        "Keep it vague and reassuring. Max 1 sentence."
    ),
    "error_task_timeout": (
        "A background task hit a time limit. Let them know "
        "and offer to continue. Max 2 sentences."
    ),
    "error_task_failed": (
        "A background task ran into an issue. Offer to try "
        "a different approach. Max 2 sentences."
    ),
    "hitl_approved": (
        "Confirm that a user approved an action and you're executing it. "
        "Include a placeholder {user} for the approver's name. Max 1 sentence."
    ),
    "hitl_expired": (
        "An action approval has expired or was already handled. "
        "Max 1 sentence."
    ),
    "hitl_cancelled": (
        "Confirm a user cancelled a pending action. "
        "Include a placeholder {user} for their name. Max 1 sentence."
    ),
}

_FALLBACKS: dict[str, list[str]] = {
    "greeting": [
        "Hey! What can I help with?",
        "Hi there — what are you working on?",
        "Hey! What do you need?",
        "Hi! Ready when you are.",
    ],
    "status": [
        "I'm here — what do you need?",
        "Online and ready. What's up?",
        "Yep, I'm around! What can I help with?",
    ],
    "help": [
        (
            "I can help with a lot — here's a quick rundown:\n\n"
            "• *Search & research* — web, competitors, market data\n"
            "• *Integrations* — Google Calendar, Gmail, GitHub, Linear, Sheets\n"
            "• *Documents* — create PDFs, spreadsheets, presentations\n"
            "• *Code* — review PRs, debug, write scripts, deploy\n"
            "• *Automate* — set up recurring tasks, workflows, alerts\n\n"
            "Just tell me what you need and I'll figure out the best way to do it."
        ),
    ],
    "progress_early": ["On it — pulling that together now."],
    "progress_mid": [
        "Still working through this — almost have what you need."
    ],
    "progress_late": [
        "Taking a bit longer than usual, but I'm making good headway."
    ],
    "progress_final": [
        "This is a thorough one — hang tight, wrapping up now."
    ],
    "task_cancelled": ["Got it — I've cancelled that."],
    "task_background_ack": [
        "Working on this in the background — I'll post updates here "
        "as I make progress. You can keep chatting in the meantime."
    ],
    "error_timeout": [
        "That one's taking longer than expected — I'm still "
        "working on it and will follow up here shortly."
    ],
    "error_rate_limit": [
        "I'm getting a lot of requests right now — give me "
        "a moment and I'll get back to you on this."
    ],
    "error_connection": [
        "Having a bit of trouble reaching one of the services "
        "I need. Let me retry in a moment."
    ],
    "error_generic": [
        "Working on getting that sorted — I'll follow up "
        "right here in a moment."
    ],
    "error_task_timeout": [
        "This research is taking longer than expected. "
        "I've hit the time limit — want me to continue?"
    ],
    "error_task_failed": [
        "Ran into an issue with that. "
        "Let me try a different approach — what specifically are you looking for?"
    ],
    "hitl_approved": ["Approved by {user}. Executing now..."],
    "hitl_expired": ["That action has already been handled or expired."],
    "hitl_cancelled": ["Cancelled by {user}."],
}

_pools: dict[str, list[str]] = {}
_pools_ready = False


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════


def pick(category: str, **format_kwargs: Any) -> str:
    """Pick a pre-generated message from a category pool.

    Fast (0ms) — no LLM call at runtime.
    Uses pools if available, falls back to hardcoded defaults.
    Supports format kwargs like ``pick("hitl_approved", user="Alice")``.
    """
    pool = _pools.get(category) or _FALLBACKS.get(category)
    if not pool:
        return category
    msg = random.choice(pool)
    if format_kwargs:
        try:
            msg = msg.format(**format_kwargs)
        except (KeyError, IndexError):
            pass
    return msg


async def humanize(
    intent: str,
    *,
    context: str = "",
    timeout: float = 2.0,
) -> str:
    """Route a one-off message through the cheapest LLM for natural phrasing.

    For frequently used messages, prefer ``pick()`` with pre-generated pools.
    This function is for dynamic messages that need user/situation context.

    Falls back to the original intent text if the LLM call fails or times out.
    """
    try:
        from lucy.core.openclaw import ChatConfig, get_openclaw_client

        client = get_openclaw_client()
        user_msg = intent
        if context:
            user_msg = f"{intent}\n\nSituation: {context}"

        response = await asyncio.wait_for(
            client.chat_completion(
                messages=[{"role": "user", "content": user_msg}],
                config=ChatConfig(
                    model="minimax/minimax-m2.5",
                    system_prompt=_REPHRASER_PROMPT,
                    max_tokens=120,
                    temperature=0.9,
                ),
            ),
            timeout=timeout,
        )

        result = (response.content or "").strip()
        if result and len(result) > 5:
            return result
        return intent
    except Exception as exc:
        logger.debug("humanize_fallback", intent=intent[:60], error=str(exc))
        return intent


# ═══════════════════════════════════════════════════════════════════════════
# POOL INITIALIZATION (called once at startup)
# ═══════════════════════════════════════════════════════════════════════════


async def initialize_pools() -> None:
    """Pre-generate message pools via a single batched LLM call.

    Should be called once during Lucy's startup (non-blocking).
    If it fails, ``pick()`` degrades to the hardcoded fallbacks.
    """
    global _pools, _pools_ready

    if _pools_ready:
        return

    try:
        from lucy.core.openclaw import ChatConfig, get_openclaw_client

        client = get_openclaw_client()

        categories_block = "\n".join(
            f"- {name}: {desc}" for name, desc in POOL_CATEGORIES.items()
        )
        prompt = (
            f"Generate 6 unique message variations for each category:\n\n"
            f"{categories_block}\n\n"
            f"Return ONLY valid JSON. Keys are category names, values are "
            f"arrays of 6 strings. No markdown fences."
        )

        response = await asyncio.wait_for(
            client.chat_completion(
                messages=[{"role": "user", "content": prompt}],
                config=ChatConfig(
                    model="minimax/minimax-m2.5",
                    system_prompt=_POOL_GENERATOR_PROMPT,
                    max_tokens=4000,
                    temperature=0.9,
                ),
            ),
            timeout=30.0,
        )

        raw = (response.content or "").strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        parsed: dict[str, list[str]] = json.loads(raw)

        loaded = 0
        for cat, variations in parsed.items():
            if isinstance(variations, list) and all(
                isinstance(v, str) for v in variations
            ):
                _pools[cat] = variations
                loaded += 1

        _pools_ready = loaded > 0
        logger.info(
            "humanize_pools_initialized",
            categories_loaded=loaded,
            total_variations=sum(len(v) for v in _pools.values()),
        )

    except json.JSONDecodeError as exc:
        logger.warning("humanize_pool_json_parse_failed", error=str(exc))
    except Exception as exc:
        logger.warning("humanize_pool_init_failed", error=str(exc))


async def refresh_pools() -> None:
    """Force-regenerate all pools. Call periodically (e.g. every 6 hours)."""
    global _pools_ready
    _pools_ready = False
    _pools.clear()
    await initialize_pools()
