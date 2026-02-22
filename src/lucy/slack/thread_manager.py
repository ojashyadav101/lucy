"""Smart thread conversation management for Lucy.

This module handles:
1. Tracking threads where Lucy is actively participating
2. Smart message classification - determining if a message is for Lucy
3. Conversation shift detection - knowing when to step back
4. Auto-closing inactive threads

Usage:
    from lucy.slack.thread_manager import ThreadManager, get_thread_manager
    
    # Record that Lucy responded in a thread
    await thread_manager.record_lucy_response(
        workspace_id=workspace_id,
        channel_id=channel_id,
        thread_ts=thread_ts,
        user_id=user_id,
        slack_channel_id=slack_channel_id,
    )
    
    # Check if we should respond to a message
    should_respond = await thread_manager.should_respond_to_message(
        workspace_id=workspace_id,
        channel_id=channel_id,
        thread_ts=thread_ts,
        user_id=user_id,
        message_text=text,
    )
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select, update

from lucy.db.models import ThreadConversation, User, Channel
from lucy.db.session import AsyncSessionLocal

logger = structlog.get_logger()


class ThreadManager:
    """Manages thread conversations for smart auto-response."""
    
    # Messages that indicate the user is done with Lucy
    EXIT_PHRASES = [
        "thanks", "thank you", "got it", "understood", "that\'s all",
        "that is all", "nothing else", "bye", "goodbye", "talk later",
        "catch you later", "see you", "later", "done", "finished",
    ]
    
    # Phrases that clearly indicate talking TO Lucy
    DIRECT_ADDRESS_PATTERNS = [
        r"\bhey\s+lucy\b",
        r"\bok\s+lucy\b",
        r"\blucy[,\s]",
        r"\bcan\s+you\b",
        r"\bwhat\s+about\b",
        r"\band\s+(what|how|when|where|why)\b",
        r"\bwhat\s+(else|other)\b",
        r"\bhow\s+about\b",
        r"\bwhat\s+if\b",
        r"\b(make|schedule|book|create|find|get|check|show|tell|add)\s+(me|us|it|that|this)",
        r"\bplease\s+(make|schedule|book|create|find|get|check|show|tell|add|send|do)",
        # "let's" only when followed by action verbs that Lucy can do
        r"\b(let\'s|let us)\s+(schedule|book|create|find|get|check|add|make|do)",
    ]
    
    # Patterns suggesting the user is talking to SOMEONE ELSE
    THIRD_PARTY_PATTERNS = [
        r"<@[A-Z0-9]+>",  # @mention of someone else
        r"\b(you\s+guys?|y\'all|everyone|team|folks|people)\b",
        r"\bwhat\s+do\s+(you\s+all|y\'all|you\s+guys?)\s+think\b",
        r"\b(who\s+wants|anyone\s+want|does\s+anyone)\b",
        r"\b(let\'s\s+(discuss|talk|meet|sync))\b",
        r"\b(agree|disagree|thoughts|opinion)\?",
        r"\b(sounds\s+good|works\s+for\s+me|i\s+think\s+so)\b",
    ]
    
    def __init__(self):
        self._logger = structlog.get_logger()
    
    async def get_or_create_thread(
        self,
        workspace_id: UUID,
        channel_id: UUID,
        thread_ts: str,
        initiator_user_id: UUID | None,
        slack_channel_id: str,
        slack_initiator_id: str,
    ) -> ThreadConversation:
        """Get existing thread or create new one."""
        async with AsyncSessionLocal() as db:
            # Try to get existing thread
            result = await db.execute(
                select(ThreadConversation).where(
                    ThreadConversation.channel_id == channel_id,
                    ThreadConversation.thread_ts == thread_ts,
                )
            )
            thread = result.scalar_one_or_none()
            
            if thread:
                return thread
            
            # Create new thread
            thread = ThreadConversation(
                workspace_id=workspace_id,
                channel_id=channel_id,
                thread_ts=thread_ts,
                initiator_user_id=initiator_user_id,
                slack_channel_id=slack_channel_id,
                slack_initiator_id=slack_initiator_id,
                is_active=True,
                status="active",
                participant_slack_ids=[slack_initiator_id],
                message_count=1,
                last_message_at=datetime.now(timezone.utc),
            )
            db.add(thread)
            await db.commit()
            await db.refresh(thread)
            
            logger.info(
                "thread_conversation_created",
                thread_id=str(thread.id),
                workspace_id=str(workspace_id),
                thread_ts=thread_ts,
                initiator=slack_initiator_id,
            )
            return thread
    
    async def record_lucy_response(
        self,
        workspace_id: UUID,
        channel_id: UUID,
        thread_ts: str,
        user_id: UUID | None,
        slack_channel_id: str,
        slack_user_id: str | None = None,
        task_id: UUID | None = None,
        intent: str | None = None,
    ) -> ThreadConversation | None:
        """Record that Lucy responded in a thread.
        
        This marks the thread as "active" - Lucy will auto-respond
        to follow-up messages without requiring @mentions.
        """
        thread = await self.get_or_create_thread(
            workspace_id=workspace_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            initiator_user_id=user_id,
            slack_channel_id=slack_channel_id,
            slack_initiator_id=slack_user_id or "unknown",
        )
        
        async with AsyncSessionLocal() as db:
            thread.lucy_last_responded_at = datetime.now(timezone.utc)
            if task_id:
                thread.last_task_id = task_id
            if intent:
                thread.last_intent = intent
            thread.is_active = True
            thread.status = "active"
            
            await db.commit()
        
        logger.info(
            "thread_lucy_response_recorded",
            thread_id=str(thread.id),
            thread_ts=thread_ts,
            task_id=str(task_id) if task_id else None,
        )
        return thread
    
    async def record_message(
        self,
        thread: ThreadConversation,
        slack_user_id: str,
        message_text: str,
    ) -> None:
        """Record a message in the thread, updating participant tracking."""
        async with AsyncSessionLocal() as db:
            # Add to participants if new
            if slack_user_id not in thread.participant_slack_ids:
                thread.participant_slack_ids.append(slack_user_id)
            
            thread.message_count += 1
            thread.last_message_at = datetime.now(timezone.utc)
            
            await db.commit()
        
        logger.debug(
            "thread_message_recorded",
            thread_id=str(thread.id),
            slack_user_id=slack_user_id,
            message_length=len(message_text),
            participant_count=len(thread.participant_slack_ids),
        )
    
    async def should_respond_to_message(
        self,
        workspace_id: UUID,
        channel_id: UUID,
        thread_ts: str,
        user_slack_id: str,
        message_text: str,
        message_ts: str | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """Determine if Lucy should respond to a message in a thread.
        
        Returns:
            Tuple of (should_respond: bool, context: dict)
            Context includes:
                - reason: Why the decision was made
                - confidence: How confident we are (high|medium|low)
                - thread_active: Whether thread is in active state
        """
        context = {
            "reason": "unknown",
            "confidence": "low",
            "thread_active": False,
            "classification": "unknown",
        }
        
        # Clean message
        clean_text = message_text.strip().lower()
        
        # 1. Check for explicit @mention - always respond
        if f"<@{self._get_bot_user_id()}>" in message_text:
            context["reason"] = "explicit_mention"
            context["confidence"] = "high"
            return True, context
        
        # 2. Check for exit phrases - don't respond
        if self._is_exit_phrase(clean_text):
            context["reason"] = "exit_phrase_detected"
            context["confidence"] = "high"
            # Also mark thread as paused
            await self._pause_thread(channel_id, thread_ts)
            return False, context
        
        # 3. Get thread state
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(ThreadConversation).where(
                    ThreadConversation.channel_id == channel_id,
                    ThreadConversation.thread_ts == thread_ts,
                )
            )
            thread = result.scalar_one_or_none()
        
        if not thread:
            # No active thread, don't respond
            context["reason"] = "no_active_thread"
            return False, context
        
        # Record this message
        await self.record_message(thread, user_slack_id, message_text)
        context["thread_active"] = thread.is_active
        
        # 4. If thread is not active, don't respond
        if not thread.is_active or thread.status != "active":
            context["reason"] = "thread_not_active"
            return False, context
        
        # 5. Check for conversation shift (third party joined)
        shift_detected = await self._detect_conversation_shift(thread, user_slack_id, clean_text)
        if shift_detected:
            context["reason"] = "conversation_shift_detected"
            context["classification"] = "third_party_focus"
            context["confidence"] = "medium"
            # Pause the thread - Lucy will require @mention from now on
            await self._pause_thread(channel_id, thread_ts)
            return False, context
        
        # 6. Smart classification
        classification = self._classify_message_intent(
            clean_text, 
            user_slack_id, 
            thread.slack_initiator_id,
            thread.participant_slack_ids,
        )
        context["classification"] = classification["type"]
        context["confidence"] = classification["confidence"]
        
        # 7. Make decision based on classification
        if classification["for_lucy"]:
            context["reason"] = "smart_classification"
            return True, context
        else:
            context["reason"] = "not_directed_at_lucy"
            return False, context
    
    def _is_exit_phrase(self, text: str) -> bool:
        """Check if message contains exit phrases."""
        for phrase in self.EXIT_PHRASES:
            if phrase in text:
                return True
        return False
    
    def _classify_message_intent(
        self,
        text: str,
        sender_slack_id: str,
        initiator_slack_id: str,
        all_participants: list[str],
    ) -> dict[str, Any]:
        """Classify if message is directed at Lucy.
        
        Returns dict with:
            - for_lucy: bool
            - confidence: "high" | "medium" | "low"
            - type: "direct_question" | "follow_up" | "third_party" | "ambiguous"
        """
        # FIRST: Check for clear third-party indicators (blocking patterns)
        # These take precedence - if present, definitely NOT for Lucy
        
        # 1. Direct mention of another user (Slack user mention format: <@U12345>)
        if re.search(r"<@[A-Z0-9]+>", text):
            return {"for_lucy": False, "confidence": "high", "type": "third_party_mention"}
        
        # 2. Addressing the group when others are present
        if len(all_participants) > 1:
            group_patterns = [
                r"\b(you\s+guys?|y\'all|everyone|team|folks|people)\b",
                r"\bwhat\s+do\s+(you\s+all|y\'all|you\s+guys?)\s+think\b",
                r"\b(who\s+wants|anyone\s+want|does\s+anyone)\b",
                r"\b(let\'s\s+(discuss|talk|meet|sync|schedule))\b",
                r"\b(agree|disagree|thoughts|opinion)[\?\s]",
                r"\b(sounds\s+good|works\s+for\s+me|i\s+think\s+so)\b",
            ]
            for pattern in group_patterns:
                if re.search(pattern, text, re.IGNORECASE):
                    return {"for_lucy": False, "confidence": "high", "type": "group_address"}
        
        # 3. Same person who started the conversation
        if sender_slack_id == initiator_slack_id:
            # Check for direct address patterns to Lucy
            if self._is_direct_address(text):
                return {"for_lucy": True, "confidence": "high", "type": "direct_address"}
            
            # Initiator questions in an active Lucy thread are usually meant for Lucy.
            # Keep this broad, since third-party/group patterns were already excluded above.
            if "?" in text and len(text) <= 200:
                return {"for_lucy": True, "confidence": "high", "type": "initiator_question"}
            if self._is_question_word(text) and len(text) <= 200:
                return {"for_lucy": True, "confidence": "medium", "type": "initiator_question_word"}
            
            # Continuation phrase
            if self._is_continuation(text):
                return {"for_lucy": True, "confidence": "medium", "type": "continuation"}
        
        # 4. Other participant speaking (not initiator) - conservative
        if sender_slack_id != initiator_slack_id:
            return {"for_lucy": False, "confidence": "medium", "type": "other_participant"}
        
        # 5. Ambiguous - default to NOT responding (conservative)
        return {"for_lucy": False, "confidence": "low", "type": "ambiguous"}
    
    def _is_direct_address(self, text: str) -> bool:
        """Check if text contains direct address patterns to Lucy."""
        for pattern in self.DIRECT_ADDRESS_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        return False
    
    def _is_third_party_address(self, text: str) -> bool:
        """Check if text is addressing third parties."""
        for pattern in self.THIRD_PARTY_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        return False
    
    def _is_question_word(self, text: str) -> bool:
        """Check if text starts with question words."""
        question_starters = [
            "what", "how", "when", "where", "why", "who", "which",
            "can", "could", "would", "will", "should", "is", "are",
            "do", "does", "did", "has", "have", "was", "were",
        ]
        for starter in question_starters:
            if text.startswith(starter + " "):
                return True
        return False
    
    def _is_continuation(self, text: str) -> bool:
        """Check if text is a continuation of previous topic."""
        continuation_patterns = [
            "also", "and", "plus", "additionally", "moreover",
            "what about", "how about", "one more", "another",
            "wait", "actually", "hold on", "i forgot",
        ]
        for pattern in continuation_patterns:
            if pattern in text:
                return True
        return False
    
    async def _detect_conversation_shift(
        self,
        thread: ThreadConversation,
        sender_slack_id: str,
        text: str,
    ) -> bool:
        """Detect if conversation has shifted away from Lucy.
        
        Signals of shift:
        - Third person responds after Lucy
        - User asks question that sounds like it's for the group
        - Direct mention of other users
        """
        # Only trigger shift detection if:
        # 1. Lucy has responded at least once
        # 2. Someone other than initiator is now speaking
        if not thread.lucy_last_responded_at:
            return False
        
        if sender_slack_id == thread.slack_initiator_id:
            # Still the original user - check if they're asking group questions
            if self._is_third_party_address(text):
                return True
            return False
        
        # New person joined the conversation
        if sender_slack_id not in thread.participant_slack_ids:
            # This is a new participant - likely conversation shift
            return True
        
        # Existing other participant speaking after Lucy
        if sender_slack_id != thread.slack_initiator_id:
            # Check if message looks like it's for the group
            if self._is_third_party_address(text):
                return True
        
        return False
    
    async def _pause_thread(self, channel_id: UUID, thread_ts: str) -> None:
        """Pause a thread - Lucy will stop auto-responding."""
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(ThreadConversation)
                .where(
                    ThreadConversation.channel_id == channel_id,
                    ThreadConversation.thread_ts == thread_ts,
                )
                .values(
                    is_active=False,
                    status="paused",
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await db.commit()
        
        logger.info(
            "thread_paused",
            channel_id=str(channel_id),
            thread_ts=thread_ts,
        )
    
    async def close_inactive_threads(self, max_age_minutes: int = 30) -> int:
        """Close threads that have been inactive for too long.
        
        Returns number of threads closed.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)
        
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                update(ThreadConversation)
                .where(
                    ThreadConversation.is_active == True,
                    ThreadConversation.last_message_at < cutoff,
                )
                .values(
                    is_active=False,
                    status="closed",
                    closed_at=datetime.now(timezone.utc),
                )
            )
            await db.commit()
            
            closed_count = result.rowcount
            if closed_count > 0:
                logger.info("threads_auto_closed", count=closed_count, max_age_minutes=max_age_minutes)
            return closed_count
    
    def _get_bot_user_id(self) -> str:
        """Get Lucy's bot user ID from settings."""
        from lucy.config import settings
        # This would typically come from settings or be cached
        # For now return a placeholder that won't match real @mentions
        return getattr(settings, "slack_bot_user_id", "LUCY_BOT")


# Singleton instance
_thread_manager: ThreadManager | None = None


def get_thread_manager() -> ThreadManager:
    """Get or create singleton ThreadManager."""
    global _thread_manager
    if _thread_manager is None:
        _thread_manager = ThreadManager()
    return _thread_manager