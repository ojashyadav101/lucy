"""Complexity classifier for model tier routing.

Determines which model tier to use based on query complexity.
Does NOT decide whether tools are needed — that is determined by
BM25 retrieval and Composio meta-tools at runtime.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import structlog

from lucy.routing.tiers import ModelTier

logger = structlog.get_logger()


@dataclass
class Classification:
    """The result of task classification."""

    tier: ModelTier
    intent: str
    confidence: float


class TaskClassifier:
    """Regex-based complexity classifier for instant tier routing.

    Routes short/simple questions to TIER_1 (gemini-flash),
    medium tasks to TIER_2 (kimi-k2.5), and complex code/reasoning
    to TIER_3 (claude-3.5-sonnet).

    This classifier NEVER returns "tool_use" — tool availability is
    decided by the BM25 index and Composio meta-tools, not by keyword
    matching. This keeps the classifier integration-agnostic.
    """

    async def classify(self, text: str, **kwargs) -> Classification:
        """Classify query complexity to determine model tier.

        Pure regex — zero network calls. Runs in <1ms.
        """
        if not text:
            return Classification(ModelTier.TIER_1_FAST, "chat", 0.95)

        lower = text.lower().strip()
        word_count = len(text.split())

        if word_count < 3:
            return Classification(ModelTier.TIER_1_FAST, "chat", 0.95)

        if re.search(r'\b(code|script|python|bash|debug|refactor|function|class|import|docker|kubernetes|cicd|ci/cd|sql|html|css|regex)\b', lower):
            return Classification(ModelTier.TIER_3_FRONTIER, "code", 0.85)

        if re.search(r'\b(plan|analyze|strategize|evaluate|compare|research|investigate|architecture|design|pros\s+and\s+cons)\b', lower):
            return Classification(ModelTier.TIER_3_FRONTIER, "reasoning", 0.8)

        multi_step_signals = len(re.findall(r'\b(?:then|after that|next|also|and then)\b', lower))
        numbered_steps = len(re.findall(r'\d+[\.\)]\s', text))
        if multi_step_signals >= 2 or numbered_steps >= 2:
            return Classification(ModelTier.TIER_3_FRONTIER, "multi_step", 0.85)

        if re.search(r'\b(report|summary|summarize|breakdown|audit)\b', lower):
            return Classification(ModelTier.TIER_2_STANDARD, "report", 0.8)

        if re.search(r'\b(write|draft|compose|letter|essay|article|blog)\b', lower):
            return Classification(ModelTier.TIER_2_STANDARD, "writing", 0.8)

        if word_count <= 15:
            return Classification(ModelTier.TIER_1_FAST, "chat", 0.85)

        return Classification(ModelTier.TIER_2_STANDARD, "chat", 0.8)


_classifier: TaskClassifier | None = None


def get_classifier() -> TaskClassifier:
    """Get singleton TaskClassifier."""
    global _classifier
    if _classifier is None:
        _classifier = TaskClassifier()
    return _classifier
