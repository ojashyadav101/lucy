"""Implicit mention detection — respond when users say "Lucy" without @mention.

Two-layer approach:
1. Free regex gate: detect "lucy" (case-insensitive, word-boundary) in channel
   messages. Zero cost, runs on every message event.
2. Cheap LLM classifier: when the regex fires, send surrounding context to the
   fast model with a yes/no prompt. Only fires when the regex matches, so cost
   is negligible (~$0.00003/call on Gemini Flash).

If the classifier says YES → trigger the full agent response.
If NO → ignore silently.

Rate-limited per channel to prevent spam: max 1 implicit trigger per channel
per 30 seconds.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import structlog

logger = structlog.get_logger()

# ── Layer 1: Free regex gate ──────────────────────────────────────────────

# Match "lucy" as a standalone word, case-insensitive.
# Excludes common false positives like URLs, file paths, code blocks.
_LUCY_MENTION = re.compile(
    r"""
    (?<![`/\w@])      # not preceded by backtick, slash, word char, or @
    \blucy\b           # the word "lucy" with word boundaries
    (?![`/\w])         # not followed by backtick, slash, or word char
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Patterns that indicate the message is NOT directed at Lucy
# (talking ABOUT Lucy to someone else, or quoting)
_ABOUT_LUCY_NOT_TO_LUCY = re.compile(
    r"""(?ix)
    (?:
        # "Lucy is/was/has..." — describing Lucy, not addressing her
        # Excludes "Lucy is there..." (question) via negative lookahead
        lucy\s+(?:is(?!\s+there)|was|has\s+been|had|does|did|seems?|looks?|appears?)\s  |
        # "ask/tell/ping Lucy" — directing SOMEONE ELSE to contact Lucy
        (?:ask|tell|ping|mention|tag|@)\s+lucy                                          |
        # "Lucy's response/answer" — talking about Lucy's output
        lucy(?:'s|s)\s+(?:response|answer|reply|output|message|code|result)  |
        # "is/was lucy working/broken/down" — asking about Lucy's status
        (?:is|was|has)\s+lucy\s+(?:working|broken|down|up|slow|fast|ok|okay|running|responding|available)
    )
    """,
)

# Strong signals that the message IS directed at Lucy (shortcut to skip LLM)
_DIRECTED_AT_LUCY = re.compile(
    r"""(?ix)
    (?:
        # "Lucy, ..." (comma = direct address)
        ^(?:hey\s+)?lucy\s*,                              |
        # "hey lucy" at start (informal address even without comma)
        ^hey\s+lucy[!?\s]                                 |
        # "Lucy can you / could you / would you / will you" (request form)
        lucy[,]?\s+(?:can|could|would|will)\s+you         |
        # "Lucy please/help/do/show..." (imperative directed at Lucy)
        lucy[,]?\s+(?:please|help|do|show|find|create|make|get|check|look|run|generate|search|write|build|give)\b  |
        # "thanks Lucy" / "thank you Lucy"
        (?:thanks|thank\s+you|thx),?\s+lucy                |
        # "Lucy what/how/why..." (question directed at Lucy)
        lucy\s+(?:what|how|why|when|where|who)
    )
    """,
)


# ── Rate limiting ─────────────────────────────────────────────────────────

# Per-channel cooldown to prevent spam
_COOLDOWN_SECONDS = 30.0
_last_implicit_trigger: dict[str, float] = {}
_rate_lock = asyncio.Lock()


async def _check_rate_limit(channel_id: str, workspace_id: str = "") -> bool:
    """Return True if we're within cooldown (should NOT trigger).

    Keys include workspace_id to avoid cross-tenant rate limit collisions.
    """
    key = f"{workspace_id}:{channel_id}" if workspace_id else channel_id
    async with _rate_lock:
        last = _last_implicit_trigger.get(key, 0.0)
        if time.monotonic() - last < _COOLDOWN_SECONDS:
            return True  # rate limited
        return False


async def _record_trigger(channel_id: str, workspace_id: str = "") -> None:
    """Record that we triggered in this channel."""
    key = f"{workspace_id}:{channel_id}" if workspace_id else channel_id
    async with _rate_lock:
        _last_implicit_trigger[key] = time.monotonic()


# ── Layer 1: Regex check ─────────────────────────────────────────────────

def contains_lucy_mention(text: str) -> bool:
    """Check if text contains an implicit mention of Lucy.

    This is the FREE gate — no LLM cost. Returns True if "lucy" appears
    as a word (not inside a URL, code block, or @mention).
    """
    return bool(_LUCY_MENTION.search(text))


def is_obviously_directed(text: str) -> bool:
    """Check if the message is OBVIOUSLY directed at Lucy.

    These patterns are so clear we can skip the LLM classifier entirely:
    - "Lucy, can you..."
    - "Hey Lucy help me with..."
    - "Lucy what is..."
    """
    return bool(_DIRECTED_AT_LUCY.search(text))


def is_talking_about_lucy(text: str) -> bool:
    """Check if the message is talking ABOUT Lucy, not TO her.

    - "Lucy is down again"
    - "Lucy's response was weird"
    - "Ask Lucy about it"
    """
    return bool(_ABOUT_LUCY_NOT_TO_LUCY.search(text))


# ── Layer 2: LLM classifier ──────────────────────────────────────────────

_CLASSIFIER_PROMPT = """\
You are a message classifier for a Slack workspace. "Lucy" is an AI assistant \
bot in this workspace. Your job is to determine whether a message is directed \
AT Lucy (expecting Lucy to respond) or merely mentions Lucy in passing.

Rules:
- "directed at Lucy" = the user wants Lucy to do something, answer a question, \
or is talking TO Lucy
- "mentions Lucy" = the user is talking ABOUT Lucy to someone else, or Lucy is \
mentioned incidentally
- If uncertain, lean toward YES (better to respond unnecessarily than to ignore \
someone who needs help)

Respond with exactly one word: YES or NO"""


async def classify_intent(
    text: str,
    context_messages: list[dict[str, str]] | None = None,
) -> bool:
    """Use a cheap LLM call to classify whether the message is directed at Lucy.

    Args:
        text: The message text containing "lucy".
        context_messages: Optional list of recent messages for context.
            Each dict has "user" and "text" keys.

    Returns:
        True if the message appears directed at Lucy.
    """
    try:
        from lucy.core.openclaw import ChatConfig, get_openclaw_client
        from lucy.pipeline.router import MODEL_TIERS

        client = await get_openclaw_client()

        # Build context
        user_content = ""
        if context_messages:
            ctx_lines = []
            for msg in context_messages[-5:]:  # last 5 messages for context
                ctx_lines.append(f"[{msg.get('user', 'unknown')}]: {msg.get('text', '')}")
            user_content = "Recent conversation:\n" + "\n".join(ctx_lines) + "\n\n"

        user_content += f"New message to classify:\n{text}"

        response = await asyncio.wait_for(
            client.chat_completion(
                messages=[{"role": "user", "content": user_content}],
                config=ChatConfig(
                    model=MODEL_TIERS["fast"],
                    system_prompt=_CLASSIFIER_PROMPT,
                    max_tokens=5,  # just need "YES" or "NO"
                    temperature=0.0,
                ),
            ),
            timeout=3.0,  # hard 3s timeout — don't slow down the event loop
        )

        answer = (response.content or "").strip().upper()
        is_directed = answer.startswith("YES")

        logger.info(
            "implicit_mention_classified",
            text=text[:100],
            answer=answer,
            is_directed=is_directed,
        )

        return is_directed

    except asyncio.TimeoutError:
        logger.warning("implicit_mention_classifier_timeout", text=text[:100])
        # On timeout, assume directed (better to respond than ignore)
        return True
    except Exception as e:
        logger.warning("implicit_mention_classifier_error", error=str(e))
        # On error, don't trigger (avoid cascading failures)
        return False


# ── Main entry point ──────────────────────────────────────────────────────

async def should_respond_to_implicit_mention(
    text: str,
    channel_id: str,
    client: Any = None,
    channel_id_for_context: str | None = None,
    event_ts: str | None = None,
) -> bool:
    """Full pipeline: regex gate → rate limit → classifier → decision.

    Args:
        text: The message text.
        channel_id: Slack channel ID.
        client: Slack client for fetching context messages.
        channel_id_for_context: Channel to fetch context from (defaults to channel_id).
        event_ts: Timestamp of the message.

    Returns:
        True if Lucy should respond to this message.
    """
    # Layer 0: Must contain "lucy"
    if not contains_lucy_mention(text):
        return False

    # Quick reject: talking ABOUT Lucy, not TO her
    if is_talking_about_lucy(text) and not is_obviously_directed(text):
        logger.debug("implicit_mention_about_not_to", text=text[:100])
        return False

    # Quick accept: obviously directed at Lucy
    if is_obviously_directed(text):
        if await _check_rate_limit(channel_id):
            logger.debug("implicit_mention_rate_limited", channel=channel_id)
            return False
        await _record_trigger(channel_id)
        logger.info("implicit_mention_obvious_trigger", text=text[:100])
        return True

    # Rate limit check
    if await _check_rate_limit(channel_id):
        logger.debug("implicit_mention_rate_limited", channel=channel_id)
        return False

    # Layer 2: LLM classifier with context
    context_messages = None
    if client and channel_id_for_context:
        try:
            result = await client.conversations_history(
                channel=channel_id_for_context or channel_id,
                latest=event_ts,
                limit=5,
                inclusive=False,
            )
            context_messages = [
                {
                    "user": msg.get("user", "bot"),
                    "text": msg.get("text", "")[:200],
                }
                for msg in reversed(result.get("messages", []))
                if msg.get("text")
            ]
        except Exception as e:
            logger.warning("implicit_mention_context_fetch_failed", error=str(e))

    is_directed = await classify_intent(text, context_messages)

    if is_directed:
        await _record_trigger(channel_id)
        logger.info("implicit_mention_llm_trigger", text=text[:100])
    else:
        logger.debug("implicit_mention_llm_rejected", text=text[:100])

    return is_directed
