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
    "not a chatbot or template. Use 1-2 emojis where they add warmth "
    "(greetings, task completion, section headers) but don't overdo it. "
    "Never use em dashes. "
    "Your response IS the message, output nothing else."
)

_POOL_GENERATOR_PROMPT = (
    "You are Lucy, a warm, approachable AI coworker. "
    "For each category below, generate exactly 6 unique, natural "
    "variations of the described message. Each variation should feel "
    "like it was written by a real person, with slightly different tone, "
    "phrasing, and word choice. Use 1-2 emojis per variation to add "
    "warmth and personality (e.g. \U0001f4ca \u2705 \U0001f680 \U0001f4e1 \u2692\ufe0f \U0001f44b \U0001f4a1 \U0001f3af). "
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
        "Reference the specific first step you're taking, like "
        "'Pulling the data you asked about' or 'Setting up the project now'. "
        "Never just say 'working on it'. Max 1 sentence."
    ),
    "progress_mid": (
        "You're making progress on a task. Reference what specific "
        "step you're on, like 'Pulled the data, putting together the summary now' "
        "or 'Found 3 results, digging into the details.' "
        "Never say just 'working on it' or 'still at it'. Max 1 sentence."
    ),
    "progress_late": (
        "A task is taking longer than usual but you're close. "
        "Reference what's left, like 'Just running a final check' "
        "or 'Almost there, formatting the results.' "
        "Don't be vague. Max 1 sentence."
    ),
    "progress_final": (
        "A thorough task is wrapping up. Reference the final step, "
        "like 'Putting the finishing touches on the summary' or "
        "'Running one last verification before sharing.' Max 1 sentence."
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
        "Hey! Doing well, thanks for asking \U0001f44b How are you doing?",
        "Hi there! Good to see you \U0001f60a How's your day going?",
        "Hey! I'm good, what's on your mind today? \U0001f4ac",
        "Hi! Always nice to chat \u2728 How are things going?",
    ],
    "status": [
        "I'm here \U0001f7e2 What's going on?",
        "Online and ready! What's up? \U0001f44b",
        "Yep, I'm around! How can I help? \U0001f4a1",
    ],
    "help": [
        (
            "I'm Lucy, I work alongside your team as an AI coworker \U0001f91d "
            "I can help with research, manage your integrations "
            "(calendar, email, GitHub, and more), create documents, "
            "write and review code, and automate recurring tasks. "
            "Think of me as the teammate who handles the things "
            "you don't have time for. Just let me know what you need!"
        ),
    ],
    "progress_early": [
        "\U0001f680 On it, pulling that together now.",
        "\u2699\ufe0f Started on this, gathering what I need first.",
        "\U0001f3af Got it, diving in now.",
    ],
    "progress_mid": [
        "\U0001f4ca Got the initial data, putting it all together now.",
        "\U0001f527 Made good headway, just refining the details.",
        "\U0001f4e1 Data's in, processing and formatting it.",
    ],
    "progress_late": [
        "\u23f3 Almost there, running a final check before I share.",
        "\U0001f3c1 Just about done, polishing up the last piece.",
        "\U0001f50d Running one more verification pass.",
    ],
    "progress_final": [
        "\u2705 Finishing touches, wrapping this up for you now.",
        "\U0001f3af Last step, should have this to you in a moment.",
        "\U0001f4e6 Packaging everything up for you.",
    ],
    "task_cancelled": ["Got it, I've cancelled that \U0001f44d"],
    "task_background_ack": [
        "\u2699\ufe0f Working on this in the background. I'll post updates here "
        "as I make progress. You can keep chatting in the meantime!"
    ],
    "error_timeout": [
        "\u23f3 Taking a bit longer than expected. "
        "Still working on it and will follow up here shortly."
    ],
    "error_rate_limit": [
        "\u26a0\ufe0f Getting a lot of requests right now, "
        "give me a moment and I'll get back to you."
    ],
    "error_connection": [
        "\U0001f50c Having a bit of trouble reaching one of the services "
        "I need. Let me retry in a moment."
    ],
    "error_generic": [
        "\U0001f527 Working on getting that sorted. I'll follow up "
        "right here in a moment."
    ],
    "error_task_timeout": [
        "\u23f0 This research is taking longer than expected. "
        "I've hit the time limit, want me to continue?"
    ],
    "error_task_failed": [
        "\U0001f6a7 Ran into an issue with that. "
        "Let me try a different approach. What specifically are you looking for?"
    ],
    "hitl_approved": ["\u2705 Approved by {user}. Executing now..."],
    "hitl_expired": ["That action has already been handled or expired."],
    "hitl_cancelled": ["\u274c Cancelled by {user}."],
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
        return "Something went wrong on my end. Could you try again?"
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
