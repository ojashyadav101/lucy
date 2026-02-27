"""Message routing, prompt construction, and output processing pipeline."""

from __future__ import annotations

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
    "MODEL_TIERS",
    "FastPathResult",
    "build_system_prompt",
    "classify_and_route",
    "classify_error_for_degradation",
    "evaluate_fast_path",
    "get_degradation_message",
    "humanize",
    "is_task_cancellation",
    "pick",
    "process_output",
    "process_output_sync",
    "refresh_pools",
    "should_deduplicate_tool_call",
]
