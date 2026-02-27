"""Lucy core: agent orchestrator, supervisor, and LLM client."""

from lucy.core.agent import AgentContext, LucyAgent, get_agent
from lucy.core.openclaw import OpenClawClient

from lucy.pipeline.output import process_output, process_output_sync
from lucy.pipeline.prompt import build_system_prompt

__all__ = [
    "AgentContext",
    "LucyAgent",
    "OpenClawClient",
    "build_system_prompt",
    "get_agent",
    "process_output",
    "process_output_sync",
]


class LucyError(Exception):
    """Root exception for all Lucy domain errors."""
