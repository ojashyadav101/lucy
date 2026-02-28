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

from lucy.config import LLMPresets
from lucy.pipeline.router import MODEL_TIERS

logger = structlog.get_logger()

_REPHRASER_PROMPT = (
    "You are Lucy, an AI coworker who's sharp, warm, and direct. "
    "Rephrase the following message in Lucy's voice. "
    "Lucy's style: conversational but competent, like the best coworker you've had. "
    "She leads with the answer, uses contractions naturally, and mixes short punchy "
    "sentences with longer ones. She uses 1-2 emojis for warmth (not decoration). "
    "She references specifics when she has them ('your sales dashboard' not 'the project'). "
    "She never uses em dashes, 'delve', or corporate filler. "
    "Keep it to 1-2 sentences. "
    "Your response IS the message, output nothing else."
)

_POOL_GENERATOR_PROMPT = (
    "You are Lucy, an AI coworker who's sharp, warm, and direct. "
    "For each category below, generate exactly 6 variations. "
    "CRITICAL: Each variation must have a DIFFERENT structure. "
    "Vary these: sentence length (some short, some longer), "
    "opening word (don't start them all the same way), "
    "emoji placement (some at start, some in middle, some at end, some with none), "
    "tone (some casual, some slightly more focused). "
    "Never use em dashes. "
    "Return ONLY valid JSON: {\"category_name\": [\"variation1\", ...]}. "
    "No markdown, no explanation."
)

POOL_CATEGORIES: dict[str, str] = {
    "greeting": (
        "Greet a colleague who just said hi to you. "
        "Be warm and friendly, reciprocate their greeting naturally. "
        "If they asked how you are, tell them you're doing well. "
        "Don't immediately ask what they need, just be friendly first. "
        "Examples: 'Hey! Doing great, how about you?', "
        "'Hi there! Good to see you 👋'. Max 1-2 sentences."
    ),
    "status": (
        "Let a colleague know you're online and available. "
        "Keep it short and casual. Max 1 sentence."
    ),
    "help": (
        "Introduce yourself as Lucy, an AI coworker. "
        "Describe WHO you are and WHAT you do in 2-3 sentences. "
        "Focus on your identity and purpose, not a bullet list of features. "
        "Example: 'I'm Lucy, I work alongside your team to help with "
        "research, integrations, documents, code, and automating the tedious stuff. "
        "Think of me as the teammate who handles the things you don't have time for.' "
        "Keep it conversational and personal, not a feature list."
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
    "error_rate_limit": (
        "You're handling many requests. Ask for a brief moment. "
        "Max 1 sentence."
    ),
    "error_connection": (
        "You can't reach an external service right now. "
        "Let them know you'll retry. Max 1 sentence."
    ),
    "error_generic": (
        "You're switching to a different approach to get this done. "
        "Sound confident and in control. Max 1 sentence. "
        "Never say 'something went wrong' — say what you're doing next."
    ),
    "error_task_failed": (
        "A background task ran into an issue. Offer to try "
        "a different approach. Max 2 sentences."
    ),
    "supervisor_replan": (
        "The approach you were taking needs adjusting. "
        "Let the user know you're switching strategy. "
        "Max 1 sentence."
    ),
    "supervisor_ask_user": (
        "You need clarification from the user to continue. "
        "Ask a specific, helpful question. Max 2 sentences."
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
        "Hey! What's on your mind today?",
        "Hi there, what can I help with?",
        "Hey! What are you working on?",
    ],
    "status": [
        "I'm here. What do you need?",
        "Online and ready. What's up?",
    ],
    "help": [
        (
            "I'm Lucy, your AI coworker. I can pull data from your "
            "connected services, create reports and documents, write "
            "and run code, set up automations, and handle the things "
            "you don't have time for. Just tell me what you need."
        ),
    ],
    "progress_early": [
        "On it.",
        "Working on this now.",
        "Got it, give me a moment.",
    ],
    "progress_mid": [
        "Making progress, will have something shortly.",
        "Halfway through. Working on the details now.",
        "Got the data, putting it together.",
    ],
    "progress_late": [
        "Almost done, running a final check.",
        "Wrapping this up now.",
        "Nearly there, verifying everything.",
    ],
    "progress_final": [
        "Done with the heavy lifting, packaging it up.",
        "Last check before I share this.",
        "Just making sure everything looks right.",
    ],
    "task_cancelled": ["Got it, cancelled."],
    "task_background_ack": [
        "Working on this in the background. "
        "I'll update you here as I go."
    ],
    "error_rate_limit": [
        "I'm getting rate limited right now. "
        "I'll be ready again in a moment."
    ],
    "error_connection": [
        "Having trouble reaching a service I need. "
        "Retrying now."
    ],
    "error_generic": [
        "Trying a different approach.",
        "Switching strategies on this.",
    ],
    "error_task_failed": [
        "Ran into an issue. Let me try a different approach."
    ],
    "supervisor_replan": [
        "Adjusting my approach based on what I've found.",
        "First approach hit a wall, trying another way.",
    ],
    "supervisor_ask_user": [
        "Quick question before I continue.",
        "Need your input on something.",
    ],
    "hitl_approved": ["Approved by {user}. Executing now."],
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
    task_hint: str = "",
    user_name: str = "",
    timeout: float = 5.0,
) -> str:
    """Route a one-off message through the cheapest LLM for natural phrasing.

    For frequently used messages, prefer ``pick()`` with pre-generated pools.
    This function is for dynamic messages that need user/situation context.

    Args:
        intent: The raw message to rephrase.
        context: Optional situational context (e.g. "user asked about Q4 sales").
        task_hint: Optional short description of the current task.
        user_name: Optional user's display name for personalization.
        timeout: Max seconds to wait for the LLM response.

    Falls back to the original intent text if the LLM call fails or times out.
    """
    try:
        from lucy.core.openclaw import ChatConfig, get_openclaw_client

        client = await get_openclaw_client()
        user_msg = intent
        context_parts: list[str] = []
        if task_hint:
            context_parts.append(f"Task: {task_hint}")
        if user_name:
            context_parts.append(f"User: {user_name}")
        if context:
            context_parts.append(f"Situation: {context}")
        if context_parts:
            user_msg = f"{intent}\n\n{chr(10).join(context_parts)}"

        response = await asyncio.wait_for(
            client.chat_completion(
                messages=[{"role": "user", "content": user_msg}],
                config=ChatConfig(
                    model=MODEL_TIERS["fast"],
                    system_prompt=_REPHRASER_PROMPT,
                    max_tokens=LLMPresets.HUMANIZE.max_tokens,
                    temperature=LLMPresets.HUMANIZE.temperature,
                ),
            ),
            timeout=timeout,
        )

        result = (response.content or "").strip()
        if result and len(result) > 5:
            return result
        return _humanize_fallback(intent)
    except Exception as exc:
        logger.debug("humanize_fallback", intent=intent[:60], error=str(exc))
        return _humanize_fallback(intent)


def _humanize_fallback(intent: str) -> str:
    """If humanize LLM fails, return a safe fallback instead of the raw prompt."""
    lower = intent.lower()
    if "still working" in lower or "previous request" in lower:
        return "Still working on your previous request, one moment!"
    if "clarify" in lower or "rephrase" in lower:
        return "Could you give me a bit more detail on what you need?"
    if "workspace" in lower or "trouble" in lower:
        return "Taking another approach on this."
    if "cancel" in lower:
        return "Got it, cancelled."
    if len(intent) > 80:
        return "Give me just a moment."
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

        client = await get_openclaw_client()

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
                    model=MODEL_TIERS["fast"],
                    system_prompt=_POOL_GENERATOR_PROMPT,
                    max_tokens=LLMPresets.HUMANIZE_POOL.max_tokens,
                    temperature=LLMPresets.HUMANIZE_POOL.temperature,
                ),
            ),
            timeout=30.0,
        )

        raw = (response.content or "").strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        parsed: dict[str, list[str]] = json.loads(raw)

        new_pools: dict[str, list[str]] = {}
        loaded = 0
        for cat, variations in parsed.items():
            if isinstance(variations, list) and all(
                isinstance(v, str) for v in variations
            ):
                new_pools[cat] = variations
                loaded += 1

        _pools = new_pools
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
    await initialize_pools()
