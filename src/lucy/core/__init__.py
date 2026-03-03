"""Lucy core: agent orchestrator, supervisor, and LLM client."""

from lucy.core.agent import AgentContext, LucyAgent, get_agent
from lucy.core.escalation import escalate_response, strip_quality_gate_meta
from lucy.core.openclaw import OpenClawClient
from lucy.core.quality import (
    assess_response_quality,
    detect_stuck_state,
    filter_search_results,
    is_genuine_service_match,
    normalize_service_name,
    validate_connection_relevance,
    validate_search_relevance,
    verify_output,
)
from lucy.core.tool_results import (
    compact_data,
    extract_structured_summary,
    sanitize_tool_output,
    strip_control_tokens,
    trim_tool_results,
)
from lucy.pipeline.output import process_output, process_output_sync
from lucy.pipeline.prompt import build_system_prompt

__all__ = [
    "AgentContext",
    "LucyAgent",
    "OpenClawClient",
    "assess_response_quality",
    "build_system_prompt",
    "compact_data",
    "detect_stuck_state",
    "escalate_response",
    "extract_structured_summary",
    "filter_search_results",
    "get_agent",
    "is_genuine_service_match",
    "normalize_service_name",
    "process_output",
    "process_output_sync",
    "sanitize_tool_output",
    "strip_control_tokens",
    "strip_quality_gate_meta",
    "trim_tool_results",
    "validate_connection_relevance",
    "validate_search_relevance",
    "verify_output",
]


class LucyError(Exception):
    """Root exception for all Lucy domain errors."""
