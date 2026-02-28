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

import itertools
import json
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

from lucy.config import LLMPresets, settings

logger = structlog.get_logger()

SUPERVISOR_CHECK_INTERVAL_TURNS = settings.supervisor_check_interval_turns
SUPERVISOR_CHECK_INTERVAL_SECONDS = settings.supervisor_check_interval_s

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
    ideal_outcome: str = ""
    underwhelming: str = ""
    who: str = ""
    risks: str = ""
    format_hint: str = ""

    def to_prompt_text(self) -> str:
        lines = [f"Goal: {self.goal}"]
        if self.who:
            lines.append(f"Who is asking: {self.who}")
        if self.ideal_outcome:
            lines.append(f"AMAZING outcome: {self.ideal_outcome}")
        if self.underwhelming:
            lines.append(f"AVOID (underwhelming): {self.underwhelming}")
        for s in self.steps:
            tools_hint = f" (using: {', '.join(s.expected_tools)})" if s.expected_tools else ""
            lines.append(f"  {s.number}. {s.description}{tools_hint}")
        if self.risks:
            lines.append(f"Risks: {self.risks}")
        if self.success_criteria:
            lines.append(f"Success: {self.success_criteria}")
        if self.format_hint:
            lines.append(f"Format: {self.format_hint}")
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
    """Determine if a task is complex enough to warrant a plan.

    Cost-conscious: only burn planner tokens when the task genuinely
    has multiple steps or ambiguous scope. Short messages within
    complex intents (e.g. "deploy it") don't need a plan — the main
    model handles them fine.
    """
    if intent in _SIMPLE_INTENTS:
        return False
    stripped = re.sub(r"```[\s\S]*?```", "", message)
    stripped = re.sub(r"https?://\S+", "", stripped)
    words = stripped.split()
    word_count = len(words)
    if word_count < 10:
        return False
    if intent in _COMPLEX_INTENTS:
        return True
    return word_count > 20


async def create_plan(
    user_message: str,
    available_tools: list[str],
    intent: str,
    user_context: str = "",
) -> TaskPlan | None:
    """Run the Thinking Model as an explicit LLM step before execution.

    This is the most important step in Lucy's pipeline. A cheap/fast model
    thinks through the full problem BEFORE the main model starts executing.
    This produces a concrete plan artifact that:
    - Forces real planning to happen (not just prompt suggestions)
    - Gives the main model a clear roadmap
    - Gives the supervisor something to evaluate against
    - Catches failure modes before they waste tool calls

    Returns None for simple tasks (greetings, short follow-ups).
    Cost: ~400 tokens on the cheapest model (~$0.0001).
    """
    if not _needs_plan(intent, user_message):
        return None

    from lucy.core.openclaw import ChatConfig, get_openclaw_client
    from lucy.pipeline.router import MODEL_TIERS

    model = MODEL_TIERS.get("fast", settings.model_tier_fast)
    client = await get_openclaw_client()

    tools_str = ", ".join(available_tools[:50])
    context_block = ""
    if user_context:
        context_block = f"\nCONTEXT:\n{user_context[:1500]}\n"

    _user_msg = user_message
    if len(_user_msg) > 1500:
        _user_msg = _user_msg[:750] + "\n...(middle omitted)...\n" + _user_msg[-750:]

    prompt = (
        f"REQUEST: {_user_msg}\n"
        f"TOOLS: {tools_str}"
        f"{context_block}\n"
        "Think through this task. One line per field. Be terse.\n\n"
        "REAL_NEED: <what does the person actually need? Use context "
        "about their role, company, and recent conversations to infer "
        "the real need behind the literal request.>\n"
        "WHO: <who is asking? technical/non-technical? what format do "
        "they prefer? what's their communication style from context?>\n"
        "AMAZING: <what response would make them say 'exactly what I needed'?>\n"
        "UNDERWHELMING: <what response would make them think 'I could have Googled this'?>\n"
        "1. <action> [tool] (fallback: <alt if this fails>)\n"
        "...\n"
        "RISKS: <1-2 biggest risks: pagination, missing creds, scope>\n"
        "SUCCESS: <specific deliverables>\n"
        "FORMAT: <how to present the result to this person; inline text, "
        "text + file, or text + multi-tab Excel>"
    )

    try:
        response = await client.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            config=ChatConfig(
                model=model,
                system_prompt=(
                    "You are a task planner. Think step by step about what "
                    "the user really needs, not just what they literally said. "
                    "Output structured fields. Be terse, one line each.\n\n"
                    "THREE-FRAME TEST (critical):\n"
                    "- AMAZING: What response would make them say 'this is exactly what I needed'?\n"
                    "- UNDERWHELMING: What would make them think 'I could have Googled this'? "
                    "Avoid this at all costs.\n"
                    "- Build toward AMAZING. If AMAZING includes insights they didn't ask for, "
                    "month-over-month comparison, a well-organized file, or a specific next step, "
                    "plan for those.\n\n"
                    "WHO: Consider who is asking. Technical or non-technical? "
                    "Brief or detailed preference? Match the response format to the person.\n\n"
                    "FORMAT guidance rules:\n"
                    "- Data requests: key metric first with comparison, then supporting detail. "
                    "If data is large, multi-tab Excel + concise Slack message with top insights. "
                    "File must contain MORE than the message.\n"
                    "- 'Detailed'/'all'/'comprehensive' = EVERYTHING, not a sample. "
                    "Plan for pagination.\n"
                    "- Simple requests: short answer, no file.\n"
                    "- Always specify: inline text, text + file, or text + multi-tab Excel."
                ),
                max_tokens=LLMPresets.SUPERVISOR.max_tokens,
                temperature=LLMPresets.SUPERVISOR.temperature,
            ),
        )
        return _parse_plan(response.content or "")
    except Exception as exc:
        logger.warning("plan_creation_failed", error=str(exc) or type(exc).__name__)
        return None


def _parse_plan(text: str) -> TaskPlan | None:
    """Parse the LLM's thinking model output into a TaskPlan."""
    if not text or len(text) < 10:
        return None

    lines = text.strip().splitlines()
    goal = ""
    ideal_outcome = ""
    underwhelming = ""
    who = ""
    risks = ""
    success = ""
    format_hint = ""
    steps: list[PlanStep] = []

    for line in lines:
        stripped = line.strip()
        upper = stripped.upper()

        if upper.startswith("GOAL:"):
            goal = stripped[5:].strip()
            continue
        if upper.startswith("REAL_NEED:"):
            goal = stripped[10:].strip()
            continue
        if upper.startswith("IDEAL:"):
            ideal_outcome = stripped[6:].strip()
            continue
        if upper.startswith("AMAZING:"):
            ideal_outcome = stripped[8:].strip()
            continue
        if upper.startswith("UNDERWHELMING:"):
            underwhelming = stripped[14:].strip()
            continue
        if upper.startswith("WHO:"):
            who = stripped[4:].strip()
            continue
        if upper.startswith("RISKS:"):
            risks = stripped[6:].strip()
            continue
        if upper.startswith("SUCCESS:"):
            success = stripped[8:].strip()
            continue
        if upper.startswith("FORMAT:"):
            format_hint = stripped[7:].strip()
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
            elif "[" in desc and "]" in desc:
                bracket_start = desc.index("[")
                bracket_end = desc.index("]")
                tool_part = desc[bracket_start + 1:bracket_end].strip()
                tools = [t.strip() for t in tool_part.split(",") if t.strip()]
                desc = (desc[:bracket_start] + desc[bracket_end + 1:]).strip()
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
        ideal_outcome=ideal_outcome,
        underwhelming=underwhelming,
        who=who,
        risks=risks,
        format_hint=format_hint,
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
    consecutive_failures: int = 0,
) -> SupervisorResult:
    """Evaluate agent progress and decide next action.

    Uses the cheapest model tier for a fast, single-classification call.
    The prompt is kept under 500 tokens for minimal cost.

    When ``consecutive_failures`` (consecutive LLM-call failures for
    the supervisor itself) reaches 3+, a heuristic fallback is used
    instead of calling the LLM again.
    """
    if consecutive_failures >= 3:
        consecutive_errors = 0
        for r in reversed(turn_reports):
            if r.had_error:
                consecutive_errors += 1
            else:
                break
        turn = len(turn_reports)
        if turn > 20:
            return SupervisorResult(
                decision=SupervisorDecision.ABORT,
                guidance="Heuristic: too many turns without supervisor LLM",
            )
        if consecutive_errors >= 3:
            return SupervisorResult(
                decision=SupervisorDecision.ESCALATE,
                guidance="Heuristic: 3+ consecutive tool errors",
            )
        return SupervisorResult(
            decision=SupervisorDecision.CONTINUE,
            guidance="Heuristic fallback (supervisor LLM unavailable)",
        )

    from lucy.core.openclaw import ChatConfig, get_openclaw_client
    from lucy.pipeline.router import MODEL_TIERS

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
        f"Evaluate agent progress. Bias: CONTINUE unless clearly stuck.\n\n"
        f"REQUEST: {user_message[:250] + '...(middle omitted)...' + user_message[-250:] if len(user_message) > 500 else user_message}\n"
        f"{intent_hint}"
        f"PLAN: {plan_text}\n"
        f"TURN: {len(turn_reports)} | ELAPSED: {int(elapsed_seconds)}s | "
        f"ERRORS: {error_count} (consec: {consecutive_errors}) | "
        f"RESPONSE: {response_text_length} chars\n"
        f"RECENT:\n{recent_text}\n\n"
        "NOTE: Long elapsed time alone is NOT a problem. The streaming "
        "layer handles hung models automatically. Only evaluate based on "
        "tool results and errors, not duration.\n\n"
        "Reply ONE letter + brief reason:\n"
        "C=continue | I=intervene(say what to try) | R=replan | "
        "E=escalate model | A=ask user | X=abort(truly impossible only)\n"
        "X is last resort — try I/R/E first."
    )

    try:
        client = await get_openclaw_client()
        response = await client.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            config=ChatConfig(
                model=model,
                system_prompt="Reply ONE letter + short reason. Be terse.",
                max_tokens=LLMPresets.SUPERVISOR_TERSE.max_tokens,
                temperature=LLMPresets.SUPERVISOR_TERSE.temperature,
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

    mapping = {
        "C": SupervisorDecision.CONTINUE,
        "I": SupervisorDecision.INTERVENE,
        "R": SupervisorDecision.REPLAN,
        "E": SupervisorDecision.ESCALATE,
        "A": SupervisorDecision.ASK_USER,
        "X": SupervisorDecision.ABORT,
    }

    first_char = text[0].upper()
    if first_char in mapping:
        guidance = text[1:].strip().lstrip("=:—-–").strip() if len(text) > 1 else ""
        return SupervisorResult(decision=mapping[first_char], guidance=guidance)

    upper_text = text.upper()
    for letter, decision in mapping.items():
        if letter in upper_text:
            guidance = text.strip()
            return SupervisorResult(decision=decision, guidance=guidance)

    return SupervisorResult(decision=SupervisorDecision.CONTINUE, guidance=text)


def build_turn_report(
    turn: int,
    tool_calls: list[dict[str, Any]],
    tool_results: list[tuple[str, str]],
) -> list[TurnReport]:
    """Build TurnReports from a turn's tool calls and results."""
    reports: list[TurnReport] = []
    if len(tool_calls) != len(tool_results):
        logger.warning(
            "turn_report_length_mismatch",
            tool_calls=len(tool_calls),
            tool_results=len(tool_results),
        )
    for tc, tr in itertools.zip_longest(tool_calls, tool_results):
        if tc is None:
            tc = {}
        call_id, result_str = tr if tr is not None else ("", "")
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
                    parsed = json.loads(result_str)
                    if isinstance(parsed, dict) and parsed.get("error"):
                        error_summary = str(parsed["error"])[:120]
                except Exception as e:
                    logger.warning("supervisor_error_parse_failed", error=str(e))
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
