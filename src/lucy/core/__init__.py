"""Lucy core: agent orchestrator, LLM client, and system prompt builder."""

from lucy.core.agent import AgentContext, LucyAgent, get_agent
from lucy.core.openclaw import OpenClawClient
from lucy.core.output import process_output
from lucy.core.prompt import build_system_prompt

__all__ = [
    "AgentContext",
    "LucyAgent",
    "get_agent",
    "OpenClawClient",
    "build_system_prompt",
    "process_output",
]


class LucyError(Exception):
    """Root exception for all Lucy domain errors."""
