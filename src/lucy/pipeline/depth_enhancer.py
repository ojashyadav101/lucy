"""Response depth enhancer — catches shallow data dumps and flags them for enrichment.

The core quality gap between shallow and excellent responses:
- Shallow: "Here's the data" (raw numbers, no context)
- Excellent: "Here's the data + what it means + what to do about it"

This module provides heuristic analysis to detect shallow responses and
generate enrichment instructions. It runs zero LLM calls — purely
pattern-based analysis that feeds back into the agent loop.

Pipeline position: runs AFTER initial response generation, BEFORE output.
If the response is flagged as shallow, the agent gets depth instructions
injected as a system nudge for self-correction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger()


# ═══════════════════════════════════════════════════════════════════════════
# Response type classification
# ═══════════════════════════════════════════════════════════════════════════


class ResponseType(str, Enum):
    """Classification of what kind of response this is."""
    DATA_REPORT = "data_report"       # Numbers, metrics, lists of records
    DATA_LOOKUP = "data_lookup"       # Single data point fetch
    ANALYSIS = "analysis"             # Already contains interpretation
    ACTION_CONFIRM = "action_confirm" # Confirmation of an action taken
    EXPLANATION = "explanation"        # Educational / how-to content
    CASUAL = "casual"                 # Greetings, small talk
    ERROR = "error"                   # Error or "can't do" response
    UNKNOWN = "unknown"


@dataclass
class DepthAssessment:
    """Result of assessing a response's analytical depth."""
    response_type: ResponseType
    depth_score: int              # 1-10: 1=raw dump, 10=deep analysis
    has_data: bool                # Contains numbers, metrics, or records
    has_interpretation: bool      # Contains "what this means" analysis
    has_comparison: bool          # Contains period-over-period or benchmark comparison
    has_recommendations: bool     # Contains actionable next steps
    has_anomaly_flags: bool       # Flags outliers or unusual patterns
    is_shallow: bool              # True if this should be deeper
    missing_layers: list[str] = field(default_factory=list)
    enrichment_instructions: str = ""


# ═══════════════════════════════════════════════════════════════════════════
# Detection patterns
# ═══════════════════════════════════════════════════════════════════════════

# Signals that the response contains data/numbers
_DATA_PATTERNS = [
    re.compile(r"\b\d{1,3}(?:,\d{3})+\b"),              # formatted numbers: 1,234
    re.compile(r"\$[\d,.]+[KkMmBb]?"),                   # currency: $42,350 or $42K
    re.compile(r"\b\d+(?:\.\d+)?%"),                     # percentages: 8.2%
    re.compile(r"\b\d+\s*(?:users?|customers?|subscribers?|records?|items?|rows?|entries|accounts?)\b", re.I),
    re.compile(r"\b(?:MRR|ARR|LTV|CAC|NPS|DAU|MAU|WAU|ARPU|GMV|AOV|CVR)\b"),
    re.compile(r"\b(?:revenue|churn|growth|conversion|retention|signup)\s*(?:rate|ratio)?\s*(?:is|was|:)\s", re.I),
    re.compile(r"(?:total|count|sum|average|median|mean)\s*(?:of|:)\s*\d", re.I),
]

# Signals that the response contains interpretation/analysis
_INTERPRETATION_PATTERNS = [
    re.compile(r"\b(?:this (?:means|suggests|indicates|shows|implies)|what this means)\b", re.I),
    re.compile(r"\b(?:trend|pattern|shift|spike|dip|drop|surge|plateau|decline|growth)\b", re.I),
    re.compile(r"\b(?:because|driven by|likely due to|caused by|attributed to|correlat)\b", re.I),
    re.compile(r"\b(?:worth (?:noting|flagging|watching)|interesting(?:ly)?|notable|noteworthy)\b", re.I),
    re.compile(r"\b(?:compared to|vs\.?|versus|relative to|period-over-period|month-over-month|year-over-year|MoM|YoY|WoW)\b", re.I),
    re.compile(r"\b(?:insight|takeaway|finding|observation)\b", re.I),
    re.compile(r"\b(?:up|down|increased|decreased|grew|fell|rose|dropped)\s+(?:by\s+)?\d", re.I),
]

# Signals that the response compares to a baseline
_COMPARISON_PATTERNS = [
    re.compile(r"\b(?:last (?:month|week|quarter|year)|previous|prior|same period)\b", re.I),
    re.compile(r"\b(?:from|up from|down from|compared to|vs\.?)\s+[\$\d]", re.I),
    re.compile(r"\b(?:benchmark|baseline|target|goal|average|industry)\b", re.I),
    re.compile(r"\b(?:higher|lower|better|worse|above|below)\s+(?:than|average)\b", re.I),
    re.compile(r"[+\-]\d+(?:\.\d+)?%"),                  # +8.2% or -3.1%
]

# Signals that the response includes actionable recommendations
_RECOMMENDATION_PATTERNS = [
    re.compile(r"\b(?:recommend|suggest|consider|should|could|might want to|worth (?:trying|exploring|considering))\b", re.I),
    re.compile(r"\b(?:next step|action item|to-do|follow[- ]?up|want me to)\b", re.I),
    re.compile(r"\b(?:set (?:this |it )?up as|automate|schedule|recurring|weekly report)\b", re.I),
    re.compile(r"\b(?:dig(?:ging)? deeper|look(?:ing)? into|investigate|monitor)\b", re.I),
    re.compile(r"\b(?:opportunity|potential|room for|improvement|optimize)\b", re.I),
]

# Signals that the response flags anomalies or outliers
_ANOMALY_PATTERNS = [
    re.compile(r"\b(?:outlier|anomal|unusual|unexpected|surprising|abnormal|spike|cliff)\b", re.I),
    re.compile(r"\b(?:concern|warning|watch out|heads up|flag(?:ging)?|attention|careful)\b", re.I),
    re.compile(r":warning:", re.I),
    re.compile(r"\b(?:something I noticed|worth flagging|caught my eye|stands out)\b", re.I),
    re.compile(r"\b(?:cluster|concentration|disproportionate|skew)\b", re.I),
]

# Patterns that indicate a "data dump" — lots of data with no analysis
_DATA_DUMP_PATTERNS = [
    re.compile(r"(?:here (?:is|are)|here's) (?:the|your|a)\s+(?:data|list|report|breakdown|summary|export|results?)", re.I),
    re.compile(r"(?:^|\n)\s*•\s*\*?\w[^:]*\*?:\s*[\$\d]", re.MULTILINE),  # bullet-point data lists
]

# Questions that imply the user wants analysis, not just data
_ANALYSIS_INTENT_PATTERNS = [
    re.compile(r"\b(?:how (?:is|are|was|were)|how's)\b", re.I),
    re.compile(r"\b(?:analyze|analysis|insight|trend|pattern|breakdown|deep dive)\b", re.I),
    re.compile(r"\b(?:what (?:happened|changed|shifted)|why (?:did|is|are))\b", re.I),
    re.compile(r"\b(?:compare|comparison|vs|versus)\b", re.I),
    re.compile(r"\b(?:report|overview|summary|status|update|check)\b", re.I),
    re.compile(r"\b(?:pull|show|get|fetch)\s+(?:my|our|the)\s+(?:\w+\s+)?(?:data|metrics|numbers|stats)\b", re.I),
]


# ═══════════════════════════════════════════════════════════════════════════
# Core classification and assessment
# ═══════════════════════════════════════════════════════════════════════════


def classify_response_type(
    user_message: str,
    response_text: str,
) -> ResponseType:
    """Classify what kind of response this is based on content signals."""
    resp_lower = response_text.lower()
    user_lower = user_message.lower()

    # Short casual responses
    if len(response_text) < 80 and not any(p.search(response_text) for p in _DATA_PATTERNS):
        casual_signals = ["hey", "hi ", "hello", "thanks", "good morning", "morning", "gm", "👋"]
        if any(s in user_lower for s in casual_signals):
            return ResponseType.CASUAL

    # Error / can't-do responses
    error_signals = [
        "i can't", "i couldn't", "i wasn't able", "not connected",
        "need access", "connect it here", "error", "failed",
    ]
    if any(s in resp_lower for s in error_signals) and len(response_text) < 400:
        return ResponseType.ERROR

    # Action confirmations (short responses after doing something)
    action_signals = [
        "done", "created", "sent", "scheduled", "updated", "deleted",
        "saved", "uploaded", "deployed", "set up", "✅",
    ]
    is_action = any(s in resp_lower for s in action_signals)
    command_words = ["send", "create", "schedule", "delete", "set", "update", "deploy"]
    is_command = any(w in user_lower.split()[:5] for w in command_words)
    if is_action and is_command and len(response_text) < 300:
        return ResponseType.ACTION_CONFIRM

    # Count data signals
    data_signal_count = sum(1 for p in _DATA_PATTERNS if p.search(response_text))
    interpretation_count = sum(1 for p in _INTERPRETATION_PATTERNS if p.search(response_text))

    # Already has rich analysis
    if data_signal_count >= 2 and interpretation_count >= 3:
        return ResponseType.ANALYSIS

    # Data-heavy response (numbers, metrics, lists)
    if data_signal_count >= 2:
        return ResponseType.DATA_REPORT

    # Single data lookup
    if data_signal_count == 1 and len(response_text) < 200:
        return ResponseType.DATA_LOOKUP

    # Educational / explanatory content
    explain_signals = ["what is", "how does", "explain", "definition", "means"]
    if any(s in user_lower for s in explain_signals):
        return ResponseType.EXPLANATION

    return ResponseType.UNKNOWN


def _count_pattern_matches(text: str, patterns: list[re.Pattern[str]]) -> int:
    """Count how many pattern groups have at least one match."""
    return sum(1 for p in patterns if p.search(text))


def assess_depth(
    user_message: str,
    response_text: str,
    tool_calls_count: int = 0,
) -> DepthAssessment:
    """Assess the analytical depth of a response.

    Returns a DepthAssessment with scoring and enrichment instructions.
    This is the main entry point for the depth enhancer.
    """
    if not response_text or not response_text.strip():
        return DepthAssessment(
            response_type=ResponseType.UNKNOWN,
            depth_score=1,
            has_data=False,
            has_interpretation=False,
            has_comparison=False,
            has_recommendations=False,
            has_anomaly_flags=False,
            is_shallow=False,
            missing_layers=[],
            enrichment_instructions="",
        )

    response_type = classify_response_type(user_message, response_text)

    # For casual, action confirmations, and errors — depth isn't relevant
    if response_type in (ResponseType.CASUAL, ResponseType.ACTION_CONFIRM, ResponseType.ERROR):
        return DepthAssessment(
            response_type=response_type,
            depth_score=7,  # Neutral — depth not applicable
            has_data=False,
            has_interpretation=False,
            has_comparison=False,
            has_recommendations=False,
            has_anomaly_flags=False,
            is_shallow=False,
            missing_layers=[],
            enrichment_instructions="",
        )

    # Detect each depth layer
    has_data = _count_pattern_matches(response_text, _DATA_PATTERNS) >= 1
    has_interpretation = _count_pattern_matches(response_text, _INTERPRETATION_PATTERNS) >= 2
    has_comparison = _count_pattern_matches(response_text, _COMPARISON_PATTERNS) >= 1
    has_recommendations = _count_pattern_matches(response_text, _RECOMMENDATION_PATTERNS) >= 1
    has_anomaly_flags = _count_pattern_matches(response_text, _ANOMALY_PATTERNS) >= 1

    # Check if the user's question implies they want analysis
    user_wants_analysis = _count_pattern_matches(user_message, _ANALYSIS_INTENT_PATTERNS) >= 1

    # Check if this looks like a data dump (data without analysis)
    is_data_dump = (
        has_data
        and _count_pattern_matches(response_text, _DATA_DUMP_PATTERNS) >= 1
        and not has_interpretation
    )

    # Calculate depth score (1-10)
    score = 3  # Base for any response with content

    if has_data:
        score += 1
    if has_interpretation:
        score += 2
    if has_comparison:
        score += 1
    if has_recommendations:
        score += 1
    if has_anomaly_flags:
        score += 1

    # Bonus for longer, structured responses that show effort
    if len(response_text) > 500 and has_data:
        score += 1

    # Penalty for data dump pattern
    if is_data_dump:
        score -= 2

    # Penalty for high tool usage with shallow response
    if tool_calls_count >= 3 and score < 6:
        score -= 1

    score = max(1, min(10, score))

    # Determine what's missing
    missing_layers: list[str] = []
    if has_data and not has_interpretation:
        missing_layers.append("interpretation")
    if has_data and not has_comparison:
        missing_layers.append("comparison")
    if (has_data or user_wants_analysis) and not has_recommendations:
        missing_layers.append("recommendations")
    if has_data and not has_anomaly_flags and response_type == ResponseType.DATA_REPORT:
        missing_layers.append("anomaly_detection")

    # Determine if this response should be deeper
    is_shallow = (
        response_type in (ResponseType.DATA_REPORT, ResponseType.DATA_LOOKUP)
        and len(missing_layers) >= 2
    ) or (
        user_wants_analysis and score <= 5
    ) or (
        is_data_dump
    ) or (
        tool_calls_count >= 3 and score <= 4
    )

    # Generate enrichment instructions
    enrichment = ""
    if is_shallow:
        enrichment = _build_enrichment_instructions(
            response_type=response_type,
            missing_layers=missing_layers,
            user_message=user_message,
            is_data_dump=is_data_dump,
        )

    if is_shallow:
        logger.info(
            "depth_assessment_shallow",
            response_type=response_type.value,
            depth_score=score,
            missing_layers=missing_layers,
            is_data_dump=is_data_dump,
        )

    return DepthAssessment(
        response_type=response_type,
        depth_score=score,
        has_data=has_data,
        has_interpretation=has_interpretation,
        has_comparison=has_comparison,
        has_recommendations=has_recommendations,
        has_anomaly_flags=has_anomaly_flags,
        is_shallow=is_shallow,
        missing_layers=missing_layers,
        enrichment_instructions=enrichment,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Enrichment instruction generation
# ═══════════════════════════════════════════════════════════════════════════


def _build_enrichment_instructions(
    response_type: ResponseType,
    missing_layers: list[str],
    user_message: str,
    is_data_dump: bool,
) -> str:
    """Build specific instructions for deepening a shallow response.

    These instructions get injected into the agent's context as a
    self-correction nudge. They're specific to what's missing.
    """
    parts: list[str] = []

    parts.append(
        "DEPTH CHECK: Your response contains data but lacks analysis. "
        "A great response has three layers: the DATA, what it MEANS, "
        "and what to DO about it. Enrich your response:"
    )

    if is_data_dump:
        parts.append(
            "\n⚠ DATA DUMP DETECTED: You're returning raw data without "
            "telling the user what it means. Never send numbers without "
            "interpretation."
        )

    if "interpretation" in missing_layers:
        parts.append(
            "\n→ ADD INTERPRETATION: After presenting the data, explain "
            "what it means. Is this good or bad? What's the trend? "
            "What patterns are visible? Use phrases like 'This shows...', "
            "'The trend here is...', 'What stands out is...'"
        )

    if "comparison" in missing_layers:
        parts.append(
            "\n→ ADD COMPARISON: Compare to a baseline. Period-over-period "
            "(last month, last week), targets/goals, or industry benchmarks. "
            "Include the delta: 'up 8.2% from last month' or "
            "'3.3% vs your 5% target'."
        )

    if "recommendations" in missing_layers:
        parts.append(
            "\n→ ADD RECOMMENDATIONS: End with 1-2 specific, actionable "
            "next steps. Not generic advice. Something the user can act "
            "on today. 'Want me to dig into the churn spike?' or "
            "'I can set this up as a weekly automated report.'"
        )

    if "anomaly_detection" in missing_layers:
        parts.append(
            "\n→ FLAG ANOMALIES: Look at the data you have. Any outliers? "
            "Unexpected spikes or drops? Concentrations in a specific "
            "segment? If you spot something unusual, flag it with "
            "a brief 'Something I noticed:' callout."
        )

    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
# Quick-check utilities (for use in quality gates)
# ═══════════════════════════════════════════════════════════════════════════


def is_data_dump(response_text: str) -> bool:
    """Quick check: does this response look like a data dump without analysis?

    Returns True if the response has data patterns but lacks interpretation.
    Useful as a fast gate before running the full assessment.
    """
    if not response_text or len(response_text) < 50:
        return False

    has_data = _count_pattern_matches(response_text, _DATA_PATTERNS) >= 2
    has_dump_pattern = _count_pattern_matches(response_text, _DATA_DUMP_PATTERNS) >= 1
    has_interp = _count_pattern_matches(response_text, _INTERPRETATION_PATTERNS) >= 2

    return has_data and has_dump_pattern and not has_interp


def needs_deeper_response(
    user_message: str,
    response_text: str,
    tool_calls_count: int = 0,
) -> bool:
    """Quick boolean: should this response be deeper?

    Convenience wrapper around assess_depth for use in quality gates
    where you just need a yes/no answer.
    """
    assessment = assess_depth(user_message, response_text, tool_calls_count)
    return assessment.is_shallow


def get_depth_nudge(
    user_message: str,
    response_text: str,
    tool_calls_count: int = 0,
) -> str | None:
    """Get enrichment instructions if the response is shallow, else None.

    Main integration point: call this after generating a response draft.
    If it returns a string, inject that as a system message before the
    agent's next self-correction pass.
    """
    assessment = assess_depth(user_message, response_text, tool_calls_count)
    if assessment.is_shallow and assessment.enrichment_instructions:
        return assessment.enrichment_instructions
    return None
