"""Content classifier for Lucy's output pipeline.

Classifies response content into USER_CONTENT (keep) vs INTERNAL (strip).

Internal content includes:
- Planning, self-correction, and quality-gate critique
- Meta-referential language ("The original response...", "Self-correction:...")
- Process narration vs actual results
- Leaked XML tags (<planning>, <thinking>, <supervisor_guidance>, etc.)

Uses structural markers (XML tags, section boundaries) AND semantic
patterns to avoid false positives on legitimate user content.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import NamedTuple

import structlog

logger = structlog.get_logger()


class ContentType(Enum):
    USER_CONTENT = "user_content"
    INTERNAL = "internal"


class ClassifiedBlock(NamedTuple):
    text: str
    content_type: ContentType
    reason: str  # Why it was classified this way


# ═══════════════════════════════════════════════════════════════════════
# XML TAG DETECTION — highest confidence signal
# ═══════════════════════════════════════════════════════════════════════

# Tags that are ALWAYS internal (never user-facing)
_INTERNAL_XML_TAGS = frozenset({
    "planning", "thinking", "self_critique", "self_correction",
    "supervisor_guidance", "supervisor_note", "internal_note",
    "quality_check", "quality_gate", "execution_plan",
    "meta_commentary", "reasoning", "reflection", "scratchpad",
    "chain_of_thought", "cot", "inner_monologue",
    "custom_integration_directive",
})

# Match opening + content + closing for known internal tags
_INTERNAL_XML_BLOCK_RE = re.compile(
    r"<(" + "|".join(re.escape(t) for t in _INTERNAL_XML_TAGS) + r")(?:\s[^>]*)?>.*?</\1>",
    re.DOTALL | re.IGNORECASE,
)

# Match stray opening/closing tags that weren't properly paired
_STRAY_INTERNAL_TAG_RE = re.compile(
    r"</?(" + "|".join(re.escape(t) for t in _INTERNAL_XML_TAGS) + r")(?:\s[^>]*)?>",
    re.IGNORECASE,
)


# ═══════════════════════════════════════════════════════════════════════
# META-REFERENTIAL PATTERNS — self-talk, corrections, process narration
# ═══════════════════════════════════════════════════════════════════════

_META_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Self-correction markers
    (re.compile(r"^Self[- ]correction:\s*", re.IGNORECASE | re.MULTILINE),
     "self_correction_prefix"),
    (re.compile(r"^Correction:\s*(?:I should|Let me|The previous)", re.IGNORECASE | re.MULTILINE),
     "correction_prefix"),

    # References to "the original response" / "my previous response"
    (re.compile(
        r"(?:The |My )?(?:original|previous|initial|first) "
        r"(?:response|answer|output|reply) "
        r"(?:is|was|had|didn't|did not|failed|missed|lacked|needs?)",
        re.IGNORECASE,
    ), "meta_response_reference"),

    # Quality-gate critique leaks
    (re.compile(r"(?:The response|This response) (?:is|was) (?:unhelpful|incomplete|incorrect|wrong|missing)", re.IGNORECASE),
     "quality_critique_leak"),
    (re.compile(r"RESPONSE_OK\b", re.IGNORECASE),
     "quality_gate_token"),
    (re.compile(r"^ISSUE:\s*", re.IGNORECASE | re.MULTILINE),
     "quality_gate_issue_token"),

    # Process narration ("Remember, the user expects...")
    (re.compile(r"^Remember,?\s+(?:the user|I should|I need to|we need)", re.IGNORECASE | re.MULTILINE),
     "process_reminder"),
    (re.compile(r"^Note to self:\s*", re.IGNORECASE | re.MULTILINE),
     "self_note"),
    (re.compile(r"^(?:Internal|Mental) note:\s*", re.IGNORECASE | re.MULTILINE),
     "internal_note"),

    # Planning leaks
    (re.compile(r"^(?:Step \d+|Plan|Strategy|Approach):\s*(?:First|Next|Then|Finally|I (?:will|should|need))", re.IGNORECASE | re.MULTILINE),
     "planning_leak"),
    (re.compile(r"^Let me (?:think|plan|reason|work) (?:through|about|on) this", re.IGNORECASE | re.MULTILINE),
     "thinking_leak"),

    # Supervisor / system directive leaks
    (re.compile(r"(?:supervisor|system) (?:says|directs|instructs|guidance|directive)", re.IGNORECASE),
     "supervisor_leak"),
    (re.compile(r"(?:as |per )(?:my |the )?(?:instructions?|directives?|guidance)", re.IGNORECASE),
     "directive_reference"),
]

# Full-line patterns — if a line matches, the ENTIRE line is internal
_FULL_LINE_INTERNAL_RE: list[re.Pattern[str]] = [
    re.compile(r"^\s*Self[- ]correction:\s*.+$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*(?:Internal|Mental) note:\s*.+$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*Note to self:\s*.+$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*RESPONSE_OK\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*ISSUE:\s*.+$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*Remember,\s+(?:the user|I should).+$", re.IGNORECASE | re.MULTILINE),
]


# ═══════════════════════════════════════════════════════════════════════
# PARAGRAPH-LEVEL CLASSIFIER
# ═══════════════════════════════════════════════════════════════════════

def _is_internal_paragraph(paragraph: str) -> tuple[bool, str]:
    """Check if a paragraph is internal content.

    Returns (is_internal, reason).
    """
    stripped = paragraph.strip()
    if not stripped:
        return False, ""

    # Check for internal XML blocks
    if _INTERNAL_XML_BLOCK_RE.search(stripped):
        return True, "xml_internal_block"

    # Check for stray internal XML tags
    if _STRAY_INTERNAL_TAG_RE.search(stripped):
        # Only flag if the paragraph is SHORT (stray tags in long content
        # might be part of a code example or discussion)
        if len(stripped) < 300:
            return True, "stray_internal_tag"

    # Check meta-referential patterns
    for pattern, reason in _META_PATTERNS:
        if pattern.search(stripped):
            # Be careful: only flag if the paragraph is predominantly meta.
            # A paragraph that starts with user content and mentions
            # "the original response" in passing should not be stripped.
            # Heuristic: if the pattern match is in the first 100 chars,
            # it's likely the paragraph IS the meta content.
            match = pattern.search(stripped)
            if match and match.start() < 100:
                return True, reason

    return False, ""


def _is_internal_line(line: str) -> bool:
    """Check if a single line is internal content."""
    for pattern in _FULL_LINE_INTERNAL_RE:
        if pattern.match(line):
            return True
    return False


# ═══════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════

def classify_content(text: str) -> list[ClassifiedBlock]:
    """Classify each paragraph/section of a response.

    Splits on double-newlines (paragraph boundaries) and classifies
    each block. XML-tagged internal blocks are handled first as
    contiguous regions, then remaining paragraphs are checked for
    meta-referential patterns.

    Returns a list of ClassifiedBlock tuples preserving order.
    """
    if not text or not text.strip():
        return []

    # Phase 1: Strip XML-tagged internal blocks and mark their positions
    cleaned = text
    xml_regions: list[tuple[int, int]] = []
    for match in _INTERNAL_XML_BLOCK_RE.finditer(text):
        xml_regions.append((match.start(), match.end()))

    # Phase 2: Split into paragraphs and classify
    paragraphs = re.split(r"\n\n+", text)
    results: list[ClassifiedBlock] = []

    for para in paragraphs:
        if not para.strip():
            continue

        is_internal, reason = _is_internal_paragraph(para)
        if is_internal:
            results.append(ClassifiedBlock(
                text=para,
                content_type=ContentType.INTERNAL,
                reason=reason,
            ))
        else:
            results.append(ClassifiedBlock(
                text=para,
                content_type=ContentType.USER_CONTENT,
                reason="",
            ))

    return results


def strip_internal_content(text: str) -> str:
    """Remove all internal content from a response.

    This is the main entry point for the output pipeline.
    Returns cleaned text with only user-facing content, or a fallback
    message if everything was stripped.
    """
    if not text or not text.strip():
        return text or ""

    # Step 1: Remove XML-tagged internal blocks
    cleaned = _INTERNAL_XML_BLOCK_RE.sub("", text)

    # Step 2: Remove stray internal XML tags
    cleaned = _STRAY_INTERNAL_TAG_RE.sub("", cleaned)

    # Step 3: Remove full internal lines
    lines = cleaned.split("\n")
    kept_lines: list[str] = []
    for line in lines:
        if not _is_internal_line(line):
            kept_lines.append(line)
    cleaned = "\n".join(kept_lines)

    # Step 4: Classify remaining paragraphs and strip internal ones
    paragraphs = re.split(r"(\n\n+)", cleaned)
    kept_parts: list[str] = []
    for part in paragraphs:
        # Preserve paragraph separators
        if re.match(r"^\n+$", part):
            kept_parts.append(part)
            continue
        if not part.strip():
            kept_parts.append(part)
            continue

        is_internal, reason = _is_internal_paragraph(part)
        if not is_internal:
            kept_parts.append(part)

    result = "".join(kept_parts)

    # Step 5: Clean up artifacts from stripping
    result = _clean_artifacts(result)

    # Step 6: Fallback if everything was stripped
    if not result.strip():
        return "I've completed the task."

    return result


def _clean_artifacts(text: str) -> str:
    """Clean up leftover artifacts from content stripping.

    Handles orphaned whitespace, broken formatting, and jagged edges
    left behind after internal blocks are removed.
    """
    # Collapse 3+ newlines into 2 (paragraph break)
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Remove leading/trailing whitespace on each line (but preserve indentation in code blocks)
    lines = text.split("\n")
    in_code_block = False
    cleaned_lines: list[str] = []
    for line in lines:
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
        if not in_code_block:
            cleaned_lines.append(line.rstrip())
        else:
            cleaned_lines.append(line)
    text = "\n".join(cleaned_lines)

    # Remove orphaned bullet/list markers
    text = re.sub(r"^\s*[•\-\*]\s*$", "", text, flags=re.MULTILINE)

    # Remove orphaned numbered list items (just a number with nothing after)
    text = re.sub(r"^\s*\d+\.\s*$", "", text, flags=re.MULTILINE)

    # Collapse multiple blank lines again after cleanup
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()
