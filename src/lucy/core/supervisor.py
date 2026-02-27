"""Supervisor Agent — intelligent progress monitor for the agent loop.

Replaces dumb timeouts with a cheap LLM-based supervisor that evaluates
whether the agent is making progress, stuck, or needs intervention.

Architecture:
    1. Planner: Before execution, generates an explicit step-by-step plan
       for complex tasks (skipped for simple requests).
    2. Supervisor: After every N turns or M seconds, evaluates progress
       against the plan and decides: CONTINUE, INTERVENE, REPLAN,
       ESCALATE, ASK_USER, or ABORT.

Both the planner and supervisor use the cheapest/fastest model tier
(MODEL_TIERS["fast"]) to keep costs negligible.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

from lucy.config import settings

logger = structlog.get_logger()

SUPERVISOR_CHECK_INTERVAL_TURNS = 3
SUPERVISOR_CHECK_INTERVAL_SECONDS = 60.0

_COMPLEX_INTENTS = frozenset({
    "data", "document", "code", "code_reasoning", "tool_use", "research",
    "monitoring",
})

_SIMPLE_INTENTS = frozenset({
    "greeting", "fast", "follow_up", "status",
})


class SupervisorDecision(str, Enum):
    CONTINUE = "continue"
    INTERVENE = "intervene"
    REPLAN = "replan"
    ESCALATE = "escalate"
    ASK_USER = "ask_user"
    ABORT = "abort"


@dataclass
class PlanStep:
    number: int
    description: str
    expected_tools: list[str] = field(default_factory=list)


@dataclass
class TaskPlan:
    goal: str
    steps: list[PlanStep]
    success_criteria: str = ""

    def to_prompt_text(self) -> str:
        lines = [f"Goal: {self.goal}"]
        for s in self.steps:
            tools_hint = f" (using: {', '.join(s.expected_tools)})" if s.expected_tools else ""
            lines.append(f"  {s.number}. {s.description}{tools_hint}")
        if self.success_criteria:
            lines.append(f"Success: {self.success_criteria}")
        return "\n".join(lines)


@dataclass
class TurnReport:
    turn: int
    tool_name: str
    tool_args_summary: str
    result_preview: str
    had_error: bool
    error_summary: str = ""


@dataclass
class SupervisorResult:
    decision: SupervisorDecision
    guidance: str = ""


def _needs_plan(intent: str, message: str) -> bool:
    """Determine if a task is complex enough to warrant a plan."""
    if intent in _SIMPLE_INTENTS:
        return False
    if intent in _COMPLEX_INTENTS:
        if len(message.split()) < 8:
            return False
        return True
    return len(message.split()) > 15


async def create_plan(
    user_message: str,
    available_tools: list[str],
    intent: str,
) -> TaskPlan | None:
    """Generate a step-by-step plan for complex tasks.

    Returns None for simple tasks that don't need planning.
    Uses the cheapest model tier for cost efficiency.
    """
    if not _needs_plan(intent, user_message):
        return None

    from lucy.core.openclaw import ChatConfig, get_openclaw_client
    from lucy.core.router import MODEL_TIERS

    model = MODEL_TIERS.get("fast", settings.model_tier_fast)
    client = await get_openclaw_client()

    tools_str = ", ".join(available_tools[:30])
    prompt = (
        "You are a task planner. Create a brief execution plan.\n\n"
        f"USER REQUEST: {user_message[:300]}\n"
        f"AVAILABLE TOOLS: {tools_str}\n\n"
        "Output a plan with 2-6 numbered steps. Each step should be "
        "one concrete action. Keep it terse — one line per step.\n"
        "Format:\n"
        "GOAL: <one sentence>\n"
        "1. <step> [tool: <tool_name>]\n"
        "2. <step> [tool: <tool_name>]\n"
        "...\n"
        "SUCCESS: <what the final output should contain>"
    )

    try:
        response = await client.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            config=ChatConfig(
                model=model,
                max_tokens=400,
                temperature=0.3,
            ),
        )
        return _parse_plan(response.content or "")
    except Exception as exc:
        logger.warning("plan_creation_failed", error=str(exc) or type(exc).__name__)
        return None


def _parse_plan(text: str) -> TaskPlan | None:
    """Parse the LLM's plan output into a TaskPlan."""
    if not text or len(text) < 10:
        return None

    lines = text.strip().splitlines()
    goal = ""
    steps: list[PlanStep] = []
    success = ""

    for line in lines:
        stripped = line.strip()
        upper = stripped.upper()

        if upper.startswith("GOAL:"):
            goal = stripped[5:].strip()
            continue
        if upper.startswith("SUCCESS:"):
            success = stripped[8:].strip()
            continue

        if stripped and stripped[0].isdigit() and "." in stripped[:4]:
            dot_idx = stripped.index(".")
            desc = stripped[dot_idx + 1:].strip()
            tools: list[str] = []
            if "[tool:" in desc.lower():
                bracket_start = desc.lower().index("[tool:")
                tool_part = desc[bracket_start + 6:].rstrip("]").strip()
                tools = [t.strip() for t in tool_part.split(",") if t.strip()]
                desc = desc[:bracket_start].strip()
            steps.append(PlanStep(
                number=len(steps) + 1,
                description=desc,
                expected_tools=tools,
            ))

    if not steps:
        return None

    return TaskPlan(
        goal=goal or "Complete the user's request",
        steps=steps,
        success_criteria=success,
    )


def should_check(
    turn: int,
    last_check_time: float,
    elapsed_seconds: float,
) -> bool:
    """Decide whether to run a supervisor checkpoint."""
    if turn < 2:
        return False

    since_last = time.monotonic() - last_check_time
    if since_last >= SUPERVISOR_CHECK_INTERVAL_SECONDS:
        return True

    if turn > 0 and turn % SUPERVISOR_CHECK_INTERVAL_TURNS == 0:
        return True

    return False


async def evaluate_progress(
    plan: TaskPlan | None,
    turn_reports: list[TurnReport],
    user_message: str,
    elapsed_seconds: float,
    current_model: str,
    response_text_length: int,
    intent: str = "",
) -> SupervisorResult:
    """Evaluate agent progress and decide next action.

    Uses the cheapest model tier for a fast, single-classification call.
    The prompt is kept under 500 tokens for minimal cost.
    """
    from lucy.core.openclaw import ChatConfig, get_openclaw_client
    from lucy.core.router import MODEL_TIERS

    model = MODEL_TIERS.get("fast", settings.model_tier_fast)

    plan_text = plan.to_prompt_text() if plan else "No plan (simple task)"

    recent = turn_reports[-3:] if turn_reports else []
    recent_lines: list[str] = []
    for r in recent:
        status = f"ERROR: {r.error_summary}" if r.had_error else r.result_preview[:80]
        recent_lines.append(f"  Turn {r.turn}: {r.tool_name} -> {status}")
    recent_text = "\n".join(recent_lines) if recent_lines else "  (no tools called yet)"

    error_count = sum(1 for r in turn_reports if r.had_error)
    consecutive_errors = 0
    for r in reversed(turn_reports):
        if r.had_error:
            consecutive_errors += 1
        else:
            break

    intent_hint = ""
    if intent == "monitoring":
        intent_hint = (
            "\nIMPORTANT: This is a MONITORING/ALERTING request. "
            "The agent should be creating a heartbeat monitor (lucy_create_heartbeat) "
            "for instant alerts, or a cron job (lucy_create_cron) for periodic reports. "
            "NOT just fetching data once. If the agent is only fetching "
            "data without setting up monitoring, choose I and "
            "instruct it to use lucy_create_heartbeat for instant alerts "
            "or lucy_create_cron for scheduled reports.\n"
        )

    prompt = (
        "You are a task supervisor. Evaluate this agent's progress and "
        "decide what should happen next.\n\n"
        f"USER REQUEST: {user_message[:150]}\n"
        f"INTENT: {intent or 'general'}\n"
        f"{intent_hint}"
        f"PLAN:\n{plan_text}\n"
        f"TURN: {len(turn_reports)}\n"
        f"ELAPSED: {int(elapsed_seconds)}s\n"
        f"RECENT ACTIONS:\n{recent_text}\n"
        f"TOTAL ERRORS: {error_count} "
        f"(consecutive: {consecutive_errors})\n"
        f"RESPONSE SO FAR: {response_text_length} chars\n"
        f"MODEL: {current_model}\n\n"
        "Reply with EXACTLY one letter, then optionally a brief reason "
        "or guidance on the same line:\n"
        "C = continue (agent is making progress)\n"
        "I = intervene (inject guidance to correct course)\n"
        "R = replan (current plan is wrong, needs new approach)\n"
        "E = escalate (switch to a stronger/smarter model)\n"
        "A = ask user (need clarification from the user)\n"
        "X = abort (task is impossible, stop gracefully)\n\n"
        "IMPORTANT: Only choose I/R/E/A/X if there is a clear problem. "
        "If the agent is working and making progress, choose C."
    )

    try:
        client = await get_openclaw_client()
        response = await client.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            config=ChatConfig(
                model=model,
                max_tokens=100,
                temperature=0.1,
            ),
        )
        return _parse_decision(response.content or "C")
    except Exception as exc:
        logger.warning(
            "supervisor_check_failed",
            error=str(exc) or type(exc).__name__,
        )
        return SupervisorResult(decision=SupervisorDecision.CONTINUE)


def _parse_decision(text: str) -> SupervisorResult:
    """Parse the supervisor's response into a decision."""
    text = text.strip()
    if not text:
        return SupervisorResult(decision=SupervisorDecision.CONTINUE)

    first_char = text[0].upper()
    guidance = text[1:].strip().lstrip("=:—-–").strip() if len(text) > 1 else ""

    mapping = {
        "C": SupervisorDecision.CONTINUE,
        "I": SupervisorDecision.INTERVENE,
        "R": SupervisorDecision.REPLAN,
        "E": SupervisorDecision.ESCALATE,
        "A": SupervisorDecision.ASK_USER,
        "X": SupervisorDecision.ABORT,
    }

    decision = mapping.get(first_char, SupervisorDecision.CONTINUE)
    return SupervisorResult(decision=decision, guidance=guidance)


def build_turn_report(
    turn: int,
    tool_calls: list[dict[str, Any]],
    tool_results: list[tuple[str, str]],
) -> list[TurnReport]:
    """Build TurnReports from a turn's tool calls and results."""
    reports: list[TurnReport] = []
    for tc, (call_id, result_str) in zip(tool_calls, tool_results):
        name = tc.get("name", "unknown")
        args = tc.get("arguments", "")
        if isinstance(args, str) and len(args) > 80:
            args = args[:77] + "..."

        had_error = False
        error_summary = ""
        preview = result_str[:100] if result_str else ""

        if result_str:
            lower = result_str[:500].lower()
            if '"error"' in lower or '"error":' in lower:
                had_error = True
                try:
                    parsed = __import__("json").loads(result_str)
                    if isinstance(parsed, dict) and parsed.get("error"):
                        error_summary = str(parsed["error"])[:120]
                except Exception:
                    pass
            if "traceback" in lower or "exception" in lower:
                had_error = True
                error_summary = error_summary or result_str[:120]

        reports.append(TurnReport(
            turn=turn,
            tool_name=name,
            tool_args_summary=args,
            result_preview=preview,
            had_error=had_error,
            error_summary=error_summary,
        ))
    return reports
