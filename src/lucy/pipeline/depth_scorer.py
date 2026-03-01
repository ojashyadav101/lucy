"""Fast-path response depth scorer — prevents shallow knowledge responses.

Designed for the fast intent path in agent.py. Zero LLM calls.
Scores a response 0-10 and provides regeneration instructions when
the response is too shallow for a knowledge/educational question.

Pipeline position: after LLM response in fast path, before return.
If score < threshold, agent regenerates with enhanced instructions.

Key design constraints:
- Must be <5ms (regex only, no LLM)
- Only triggers on knowledge/educational questions (not casual chat)
- Max 1 regeneration attempt (latency budget)
- Works with the existing depth_enhancer patterns where applicable
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "score_response",
    "is_knowledge_question",
    "build_regeneration_prompt",
    "DepthScore",
    "DEPTH_THRESHOLD",
]

# ═══════════════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════════════

# Minimum acceptable score for knowledge questions on the fast path.
# Below this → regenerate with stronger depth instructions.
DEPTH_THRESHOLD = 5

# Minimum word count for knowledge questions. Even a high-structure
# response needs enough substance.
MIN_KNOWLEDGE_WORDS = 120


# ═══════════════════════════════════════════════════════════════════════════
# Knowledge question detection
# ═══════════════════════════════════════════════════════════════════════════

# Patterns that indicate the user wants a substantive, educational answer
# — NOT a quick lookup or casual chat.
_KNOWLEDGE_PATTERNS = [
    # Explicit explanation requests
    re.compile(
        r"\b(?:explain|walk\s+me\s+through|break\s*(?:it\s+)?down"
        r"|how\s+does|how\s+do\s+(?:you|I|we)|what\s+(?:is|are)\s+the\s+"
        r"(?:difference|differences|best|key|main|top|pros?\s+and\s+cons?)"
        r"|what\s+makes|what\s+happens\s+when)\b",
        re.IGNORECASE,
    ),
    # Comparison requests
    re.compile(
        r"\b(?:compare|comparison|vs\.?|versus)\b"
        r"|\b\w+\s+(?:vs\.?|versus|or)\s+\w+",
        re.IGNORECASE,
    ),
    # Best practices / guide requests
    re.compile(
        r"\b(?:best\s+practices?|strategies?\s+for|guide\s+(?:me|to|for)"
        r"|how\s+(?:should|would|to)\s+(?:I|we|you)"
        r"|architecture|design\s+pattern"
        r"|when\s+(?:should|would|to)\s+(?:I|we|you)\s+use)\b",
        re.IGNORECASE,
    ),
    # Deep-dive / analytical requests
    re.compile(
        r"\b(?:deep\s*dive|in[\s-]depth|thorough|comprehensive"
        r"|detailed|advantages?\s+and\s+disadvantages?"
        r"|pros?\s+and\s+cons?|trade[\s-]?offs?|implications?)\b",
        re.IGNORECASE,
    ),
    # Technical concept questions
    re.compile(
        r"\b(?:how\s+(?:does|do)\s+\w+\s+work"
        r"|what\s+is\s+(?:the\s+)?(?:concept|idea|principle|theory)\s+(?:of|behind)"
        r"|under\s+the\s+hood|internally)\b",
        re.IGNORECASE,
    ),
]

# Questions that should NOT trigger depth enforcement even if they
# match a knowledge pattern (simple factual lookups, yes/no questions).
# NOTE: These only reject if NO strong knowledge pattern matches —
# "What are the best practices for X" has both "what are the" AND
# "best practices", and the knowledge signal should win.
_SHALLOW_OK_PATTERNS = [
    re.compile(r"^what\s+(?:is|are)\s+(?:my|our|the)\s+", re.IGNORECASE),  # "what is my plan"
    re.compile(r"^(?:is|are|do|does|did|can|could|will|would|have|has)\s+", re.IGNORECASE),  # yes/no
    re.compile(r"\b(?:status|weather|time|date|price|cost|how\s+much)\b", re.IGNORECASE),  # lookups
    re.compile(r"^(?:hi|hey|hello|thanks|good\s+(?:morning|afternoon|evening))", re.IGNORECASE),
]


def is_knowledge_question(message: str) -> bool:
    """Detect whether a message is a knowledge/educational question.

    Returns True for questions that deserve substantive, structured answers
    with examples, comparisons, and recommendations. Returns False for
    casual chat, simple lookups, and yes/no questions.

    Priority: knowledge patterns > shallow-ok patterns. If a question
    triggers both (e.g., "What are the best practices for X"), knowledge wins.
    """
    if not message or len(message.strip()) < 10:
        return False

    # Quick rejection: short messages are rarely knowledge questions
    word_count = len(message.split())
    if word_count < 3:
        return False

    # Check knowledge patterns first (they take priority)
    knowledge_matches = sum(1 for pat in _KNOWLEDGE_PATTERNS if pat.search(message))

    # Strong knowledge signal overrides shallow-ok patterns
    if knowledge_matches >= 2:
        return True

    # If we have exactly 1 knowledge match, check if shallow-ok rejects it
    if knowledge_matches == 1:
        for pat in _SHALLOW_OK_PATTERNS:
            if pat.search(message):
                return False
        return True

    # No knowledge patterns matched
    return False


# ═══════════════════════════════════════════════════════════════════════════
# Response depth scoring
# ═══════════════════════════════════════════════════════════════════════════

# Structural signals that indicate depth
_STRUCTURE_PATTERNS = {
    "headers": re.compile(r"(?:^|\n)#+\s+|(?:^|\n)\*\*[^*]+\*\*\s*\n", re.MULTILINE),
    "bullet_points": re.compile(r"(?:^|\n)\s*[-•*]\s+", re.MULTILINE),
    "numbered_list": re.compile(r"(?:^|\n)\s*\d+[.)]\s+", re.MULTILINE),
    "code_blocks": re.compile(r"```[\s\S]*?```"),
    "bold_emphasis": re.compile(r"\*\*[^*]+\*\*"),
}

# Content depth signals
_DEPTH_SIGNALS = {
    "examples": re.compile(
        r"\b(?:for\s+example|e\.g\.|such\s+as|like\s+when|consider\s+a"
        r"|imagine\s+(?:a|you)|here'?s?\s+(?:an?\s+)?example|instance)\b",
        re.IGNORECASE,
    ),
    "explanations": re.compile(
        r"\b(?:because|therefore|this\s+means|the\s+reason|this\s+is\s+because"
        r"|in\s+other\s+words|essentially|fundamentally|under\s+the\s+hood)\b",
        re.IGNORECASE,
    ),
    "comparisons": re.compile(
        r"\b(?:compared\s+to|unlike|whereas|on\s+the\s+other\s+hand"
        r"|in\s+contrast|however|while\s+\w+\s+(?:is|are|does|has)"
        r"|the\s+(?:key\s+)?difference)\b",
        re.IGNORECASE,
    ),
    "recommendations": re.compile(
        r"\b(?:recommend|suggest|consider\s+using|best\s+(?:practice|approach|choice)"
        r"|(?:you|I)\s+should|go\s+with|prefer|opt\s+for|ideal\s+for"
        r"|start\s+with|use\s+\w+\s+when)\b",
        re.IGNORECASE,
    ),
    "tradeoffs": re.compile(
        r"\b(?:trade[\s-]?off|downside|drawback|caveat|limitation"
        r"|the\s+catch|keep\s+in\s+mind|be\s+aware|worth\s+noting"
        r"|however|that\s+said|on\s+the\s+flip\s+side)\b",
        re.IGNORECASE,
    ),
    "practical_context": re.compile(
        r"\b(?:in\s+practice|in\s+production|at\s+scale|real[\s-]?world"
        r"|day[\s-]?to[\s-]?day|common\s+(?:use\s+case|pattern|scenario)"
        r"|typically|most\s+teams|in\s+my\s+experience)\b",
        re.IGNORECASE,
    ),
    "multiple_perspectives": re.compile(
        r"\b(?:pros?\s+and\s+cons?|advantages?\s+and\s+disadvantages?"
        r"|strengths?\s+and\s+weaknesses?|benefits?\s+and\s+drawbacks?"
        r"|on\s+one\s+hand|from\s+(?:a|the)\s+\w+\s+perspective)\b",
        re.IGNORECASE,
    ),
}


@dataclass
class DepthScore:
    """Result of scoring a response's depth."""
    score: int              # 0-10: overall depth quality
    word_count: int
    has_structure: bool     # Headers, bullets, numbered lists
    has_examples: bool
    has_explanations: bool
    has_comparisons: bool
    has_recommendations: bool
    has_tradeoffs: bool
    has_practical_context: bool
    missing_elements: list[str]
    is_knowledge_question: bool
    needs_regeneration: bool


def score_response(
    text: str,
    intent: str,
    question: str,
) -> DepthScore:
    """Score a response's depth on a 0-10 scale.

    Args:
        text: The LLM response text
        intent: The classified intent (e.g., "chat", "lookup")
        question: The original user question

    Returns:
        DepthScore with detailed breakdown and regeneration flag.

    Scoring rubric:
        0-2: Empty or near-empty response
        3-4: Superficial — answers the question but no depth
        5-6: Adequate — has some structure and detail
        7-8: Good — structured with examples/comparisons
        9-10: Excellent — comprehensive with tradeoffs and practical context
    """
    if not text or not text.strip():
        return DepthScore(
            score=0, word_count=0, has_structure=False,
            has_examples=False, has_explanations=False,
            has_comparisons=False, has_recommendations=False,
            has_tradeoffs=False, has_practical_context=False,
            missing_elements=["everything"],
            is_knowledge_question=is_knowledge_question(question),
            needs_regeneration=is_knowledge_question(question),
        )

    is_knowledge = is_knowledge_question(question)
    word_count = len(text.split())

    # ── Structure detection ──
    structure_count = 0
    for name, pat in _STRUCTURE_PATTERNS.items():
        matches = pat.findall(text)
        if len(matches) >= 2:  # Need at least 2 structural elements
            structure_count += 1
    has_structure = structure_count >= 1

    # ── Depth signal detection ──
    signals: dict[str, bool] = {}
    for name, pat in _DEPTH_SIGNALS.items():
        signals[name] = bool(pat.search(text))

    # ── Scoring ──
    score = 0

    # Base score from word count (0-3 points)
    if word_count >= 300:
        score += 3
    elif word_count >= 200:
        score += 2
    elif word_count >= 100:
        score += 1

    # Structure bonus (0-2 points)
    if has_structure:
        score += 1
    if structure_count >= 2:
        score += 1

    # Depth signals (0-5 points, 1 each)
    depth_elements = [
        ("examples", signals.get("examples", False)),
        ("explanations", signals.get("explanations", False)),
        ("comparisons", signals.get("comparisons", False)),
        ("recommendations", signals.get("recommendations", False)),
        ("tradeoffs", signals.get("tradeoffs", False)),
    ]
    for name, present in depth_elements:
        if present:
            score += 1

    # Cap at 10
    score = min(10, score)

    # ── Missing elements ──
    missing: list[str] = []
    if not signals.get("examples"):
        missing.append("examples")
    if not signals.get("explanations"):
        missing.append("explanations")
    if is_knowledge and not signals.get("comparisons"):
        missing.append("comparisons")
    if not signals.get("recommendations"):
        missing.append("practical_recommendations")
    if is_knowledge and not signals.get("tradeoffs"):
        missing.append("tradeoffs_or_caveats")
    if not has_structure:
        missing.append("structured_sections")
    if word_count < MIN_KNOWLEDGE_WORDS:
        missing.append("sufficient_detail")

    # ── Regeneration decision ──
    # Only enforce depth for knowledge questions on chat intent
    needs_regen = False
    if is_knowledge and intent in ("chat", "lookup", "followup"):
        if score < DEPTH_THRESHOLD:
            needs_regen = True
        elif word_count < MIN_KNOWLEDGE_WORDS:
            needs_regen = True

    return DepthScore(
        score=score,
        word_count=word_count,
        has_structure=has_structure,
        has_examples=signals.get("examples", False),
        has_explanations=signals.get("explanations", False),
        has_comparisons=signals.get("comparisons", False),
        has_recommendations=signals.get("recommendations", False),
        has_tradeoffs=signals.get("tradeoffs", False),
        has_practical_context=signals.get("practical_context", False),
        missing_elements=missing,
        is_knowledge_question=is_knowledge,
        needs_regeneration=needs_regen,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Regeneration prompt builder
# ═══════════════════════════════════════════════════════════════════════════


def build_regeneration_prompt(
    question: str,
    shallow_response: str,
    depth_result: DepthScore,
) -> str:
    """Build a follow-up instruction that pushes the LLM to go deeper.

    This message gets appended as a user turn after the shallow response,
    creating a 2-turn flow: [question] → [shallow answer] → [depth nudge].
    The LLM then regenerates with the nudge.

    Returns the nudge message text.
    """
    missing = depth_result.missing_elements

    parts: list[str] = []
    parts.append(
        "Your response above is too brief for this question. "
        "Please provide a comprehensive, well-structured answer. "
        "Aim for 300+ words with clear sections."
    )

    if "examples" in missing:
        parts.append(
            "• Include concrete examples — code snippets, real-world "
            "scenarios, or specific use cases that illustrate your points."
        )

    if "explanations" in missing:
        parts.append(
            "• Explain the *why* behind each point — don't just list "
            "facts, explain the reasoning and mechanisms."
        )

    if "comparisons" in missing:
        parts.append(
            "• Compare and contrast — highlight key differences, "
            "when each option shines, and when it falls short."
        )

    if "practical_recommendations" in missing:
        parts.append(
            "• End with practical recommendations — what should the "
            "reader actually DO with this information? Give concrete guidance."
        )

    if "tradeoffs_or_caveats" in missing:
        parts.append(
            "• Discuss tradeoffs and caveats — nothing is perfect; "
            "mention limitations, edge cases, and things to watch out for."
        )

    if "structured_sections" in missing:
        parts.append(
            "• Use clear structure — headers, bullet points, or "
            "numbered lists to organize your response."
        )

    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
# Self-test
# ═══════════════════════════════════════════════════════════════════════════
