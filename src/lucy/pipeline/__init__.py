"""Message routing, prompt construction, and output processing pipeline."""

from __future__ import annotations

from lucy.pipeline.content_classifier import (
    ClassifiedBlock,
    ContentType,
    classify_content,
    strip_internal_content,
)
from lucy.pipeline.depth_enhancer import (
    DepthAssessment,
    ResponseType,
    assess_depth,
    get_depth_nudge,
    is_data_dump,
    needs_deeper_response,
)
from lucy.pipeline.edge_cases import (
    classify_error_for_degradation,
    get_degradation_message,
    is_task_cancellation,
    should_deduplicate_tool_call,
)
from lucy.pipeline.fast_path import FastPathResult, evaluate_fast_path
from lucy.pipeline.humanize import humanize, pick, refresh_pools
from lucy.pipeline.output import process_output, process_output_sync
from lucy.pipeline.prompt import build_system_prompt
from lucy.pipeline.router import MODEL_TIERS, classify_and_route

__all__ = [
    "ClassifiedBlock",
    "ContentType",
    "DepthAssessment",
    "MODEL_TIERS",
    "FastPathResult",
    "ResponseType",
    "assess_depth",
    "build_system_prompt",
    "classify_and_route",
    "classify_content",
    "classify_error_for_degradation",
    "evaluate_fast_path",
    "get_degradation_message",
    "get_depth_nudge",
    "humanize",
    "is_data_dump",
    "is_task_cancellation",
    "needs_deeper_response",
    "pick",
    "process_output",
    "process_output_sync",
    "refresh_pools",
    "should_deduplicate_tool_call",
    "strip_internal_content",
]
