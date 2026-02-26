"""CodingEngine — plan → execute → validate → retry orchestrator.

Central coding sub-system that handles all code generation tasks:
app generation, script writing, code fixes, and general coding.

Called from the main agent, sub-agents, Spaces, and cron scripts.
Provides consistent code quality across all pathways.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

import structlog

from lucy.coding.memory import CodingMemory, load_coding_memory, save_coding_memory
from lucy.coding.prompt import build_coding_prompt
from lucy.coding.validator import ValidationResult, validate_project
from lucy.config import settings
from lucy.core.openclaw import (
    ChatConfig,
    OpenClawResponse,
    get_openclaw_client,
)

logger = structlog.get_logger()

_TOOL_CALL_PATTERN = re.compile(
    r"<(?:minimax|anthropic|tool)[:\s_].*?>.*?</(?:minimax|anthropic|tool)[:\s_].*?>",
    re.DOTALL,
)


def _strip_code_fences(code: str) -> str:
    """Remove markdown code fences from LLM output.

    Models often wrap code in ```tsx ... ``` even when told not to.
    """
    if not code:
        return code
    stripped = code.strip()
    if stripped.startswith("```"):
        lines = stripped.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines)
    return stripped


def _sanitize_plan_output(plan: str) -> str:
    """Strip accidental tool-call XML from plan output.

    Some models (e.g. MiniMax) emit tool-call XML even when not given tools.
    """
    if not plan or "<" not in plan:
        return plan
    cleaned = _TOOL_CALL_PATTERN.sub("", plan).strip()
    if len(cleaned) < 20:
        return "(Model produced invalid plan output — proceed with implementation directly.)"
    return cleaned


MAX_FIX_ATTEMPTS = 3
MAX_ENGINE_TURNS = 15
ENGINE_TIMEOUT_SECONDS = 240
_UNLIMITED_TOKENS = 100_000

MODEL_COSTS: dict[str, tuple[float, float]] = {
    "google/gemini-3.1-pro-preview": (2.00, 12.00),
    "minimax/minimax-m2.5": (0.30, 1.10),
    "moonshotai/kimi-k2.5": (0.45, 2.20),
    "google/gemini-3-flash-preview": (0.50, 3.00),
    "google/gemini-2.5-flash": (0.30, 2.50),
}

_COMPLEXITY_HIGH_SIGNALS = [
    "canvas", "webgl", "web audio", "webaudio", "websocket",
    "real-time", "realtime", "drag-and-drop", "drag and drop",
    "3d", "three.js", "animation", "visualiz",
    "multiple api", "multi-api", "3 api", "4 api",
    "oauth", "authentication", "encrypt",
    "cors proxy", "cors", "coingecko", "deezer", "spotify",
    "portfolio", "trading", "kanban", "drag",
    "leaflet", "mapbox", "openstreetmap",
    "audio api", "analysernode", "oscillator",
    "camera", "microphone", "geolocation",
]
_COMPLEXITY_LOW_SIGNALS = [
    "calculator", "counter", "todo", "timer", "stopwatch",
    "hello world", "simple", "basic", "minimal",
    "clock", "greeting", "landing page",
]


def classify_complexity(task: str) -> str:
    """Classify task complexity for model selection.

    Returns 'high', 'standard', or 'simple'.
    """
    lower = task.lower()
    word_count = len(task.split())

    if any(s in lower for s in _COMPLEXITY_LOW_SIGNALS) and word_count < 40:
        return "simple"

    high_score = sum(1 for s in _COMPLEXITY_HIGH_SIGNALS if s in lower)
    if high_score >= 2 or word_count > 150:
        return "high"

    return "standard"


def _calc_cost(
    model: str, prompt_tokens: int, completion_tokens: int,
) -> float:
    """Calculate cost in USD for a single LLM call."""
    input_rate, output_rate = MODEL_COSTS.get(model, (0.0, 0.0))
    return (prompt_tokens * input_rate + completion_tokens * output_rate) / 1_000_000


_PLANNING_PREAMBLE = (
    "You are in PLANNING mode. Do NOT write any code yet. "
    "Do NOT call any tools or output XML/function calls. "
    "Output ONLY plain text.\n\n"
    "Analyze the task and produce a comprehensive plan:\n\n"
)

_PLANNING_SPACES = (
    "1. GOAL: What is the user actually trying to accomplish? "
    "Restate the core purpose in one sentence.\n"
    "2. ARCHITECTURE: Component/function structure and data flow.\n"
    "3. LIBRARIES: What imports are needed? Only use pre-installed "
    "libraries (shadcn/ui, lucide-react, framer-motion, recharts, "
    "react-router-dom, Tailwind CSS) or well-known npm packages.\n"
    "4. EXTERNAL APIs & CORS — MANDATORY:\n"
    "   THIS IS A CLIENT-SIDE REACT APP. It runs in the BROWSER.\n"
    "   Any external API that does not explicitly allow browser CORS "
    "will cause a blank screen / broken app.\n"
    "   RULE: For EVERY external API call, assume CORS is blocked "
    "unless you are 100% certain it is allowed. When in doubt, "
    "USE THE PROXY.\n"
    "   HOW: Define a corsProxy helper at the top of the file:\n"
    "     const corsProxy = (url: string) =>\n"
    "       `https://api.allorigins.win/raw?url="
    "${encodeURIComponent(url)}`;\n"
    "   Then wrap every external fetch: fetch(corsProxy(url))\n"
    "   CORS-SAFE (no proxy needed): Open-Meteo, sunrise-sunset.org, "
    "jsonplaceholder, restcountries.com, pokeapi.co, randomuser.me\n"
    "   CORS-BLOCKED (MUST proxy): CoinGecko, Deezer, NewsAPI, "
    "any API without explicit Access-Control-Allow-Origin: *\n"
    "   ALWAYS add try/catch with user-friendly fallback UI for "
    "API calls. Never let an API failure crash the app.\n"
    "5. POTENTIAL ISSUES WATCHLIST:\n"
    "   - List 3-5 specific things that could go wrong\n"
    "   - For each, state how the code will prevent or handle it\n"
    "   - Common issues: CORS blocks, API rate limits, "
    "empty responses, pagination, large payloads, "
    "localStorage quota, missing data fields\n"
    "6. IMPLEMENTATION ORDER: Step-by-step build sequence.\n\n"
    "Be thorough on sections 4 and 5. These prevent runtime failures."
)

_PLANNING_SCRIPT = (
    "1. GOAL: What is the user ACTUALLY trying to accomplish? "
    "Restate the core purpose in one sentence. Think about the "
    "broader intent, not just the literal request.\n"
    "2. DATA SOURCES: What APIs or services are involved? What auth "
    "is needed? What is each API's pagination model (offset, cursor, "
    "page-based)? What is the maximum page size each supports?\n"
    "3. VOLUME ESTIMATION: How many records are expected from each "
    "source? Will the data fit in memory? Does it need streaming to "
    "disk? Will it exceed the chat context window?\n"
    "4. RATE LIMIT STRATEGY: What are the known rate limits for each "
    "API? Plan to use 80% of capacity for maximum speed. How will "
    "the script detect rate limits from response headers "
    "(X-RateLimit-Remaining, Retry-After)? What backoff strategy?\n"
    "5. EXECUTION STRATEGY: Script (for 100+ records, merges, "
    "exports) or direct tool calls (for small lookups under 50 "
    "records)? Always prefer script for bulk data.\n"
    "6. OUTPUT FORMAT: What is the best format for the user? "
    "Excel with multiple sheets? CSV? JSON? Chat summary? "
    "What sheets should the workbook have? What columns?\n"
    "7. POTENTIAL ISSUES — SPECIFIC TO THIS TASK:\n"
    "   List 3-5 things that could go wrong for THIS specific task.\n"
    "   For EACH issue, state exactly how the code will prevent or "
    "handle it. Think about:\n"
    "   - Pagination: What if the API returns fewer results than "
    "expected? What if pagination tokens expire?\n"
    "   - Rate limits: What if the API throttles mid-batch?\n"
    "   - Data quality: Missing fields, nulls, different schemas "
    "between sources, encoding issues\n"
    "   - Merge conflicts: Different emails for the same person, "
    "duplicate records, join key mismatches\n"
    "   - Auth: What if the API key is expired or invalid?\n"
    "   - Volume: What if there are 100K records instead of 3K?\n"
    "8. IMPLEMENTATION: Step-by-step execution plan.\n\n"
    "Be thorough on sections 4 and 7. Rate limits and error "
    "anticipation prevent runtime failures."
)

_PLANNING_GENERAL = (
    "1. GOAL: What is the user ACTUALLY trying to accomplish? "
    "Think about the broader intent, not just the literal words.\n"
    "2. APPROACH: Is this a data/API task (use a script), a web app "
    "(use Spaces), or a simple code fix? Choose the right strategy.\n"
    "3. DEPENDENCIES: What APIs, libraries, or services are needed?\n"
    "4. POTENTIAL ISSUES — SPECIFIC TO THIS TASK:\n"
    "   List 3-5 things that could go wrong for THIS specific task.\n"
    "   For EACH issue, state how the code will handle it.\n"
    "   Think about: API failures, rate limits, data volume, "
    "pagination, auth, edge cases, missing data.\n"
    "5. IMPLEMENTATION: Step-by-step plan.\n\n"
    "Think deeply about section 4. Anticipating issues is what "
    "separates good code from brittle code."
)


def _build_planning_prompt(task_type: str) -> str:
    """Build the right planning prompt based on the task type."""
    if task_type == "spaces":
        return _PLANNING_PREAMBLE + _PLANNING_SPACES
    if task_type == "script":
        return _PLANNING_PREAMBLE + _PLANNING_SCRIPT
    return _PLANNING_PREAMBLE + _PLANNING_GENERAL


@dataclass
class CodingContext:
    """Context for a coding task."""

    workspace_id: str
    task: str
    project_dir: Path | None = None
    target_file: str | None = None
    task_type: str = "general"  # general | spaces | script | fix
    files_written: list[str] = field(default_factory=list)
    files_read: list[str] = field(default_factory=list)
    validation_attempts: int = 0
    errors_encountered: list[str] = field(default_factory=list)


@dataclass
class CodingResult:
    """Result of a CodingEngine execution."""

    success: bool
    files_modified: list[str] = field(default_factory=list)
    validation_passed: bool = False
    summary: str = ""
    error: str = ""
    attempts: int = 0


class CodingEngine:
    """Orchestrates plan → execute → validate → retry for all coding tasks."""

    def __init__(self) -> None:
        self._tool_executor: Callable[..., Awaitable[Any]] | None = None
        self._progress_callback: (
            Callable[[str], Awaitable[None]] | None
        ) = None

    def set_tool_executor(
        self,
        executor: Callable[..., Awaitable[Any]],
    ) -> None:
        """Set the function used to execute lucy_* tools."""
        self._tool_executor = executor

    def set_progress_callback(
        self,
        callback: Callable[[str], Awaitable[None]],
    ) -> None:
        """Set callback for progress notifications."""
        self._progress_callback = callback

    async def _notify_progress(self, message: str) -> None:
        if self._progress_callback:
            try:
                await self._progress_callback(message)
            except Exception:
                pass

    def _is_simple_task(self, task: str) -> bool:
        """Detect simple tasks for lighter-weight planning.

        Returns True for trivial tasks. Planning still runs but uses
        the code-tier model instead of the expensive frontier model.
        """
        simple_signals = [
            "calculator", "counter", "todo", "timer", "stopwatch",
            "hello world", "simple", "basic", "lean", "minimal",
            "clock", "greeting", "landing page",
        ]
        lower = task.lower()
        return any(s in lower for s in simple_signals) and len(task) < 200

    async def _load_workspace_context(self, workspace_id: str) -> str:
        """Load company/team knowledge from workspace SKILL.md files."""
        workspace_dir = settings.workspace_root / workspace_id
        context_parts: list[str] = []

        skill_paths = [
            workspace_dir / "company" / "SKILL.md",
            workspace_dir / "team" / "SKILL.md",
        ]
        for path in skill_paths:
            if path.exists():
                try:
                    content = path.read_text(encoding="utf-8")[:1500]
                    context_parts.append(content)
                except Exception:
                    pass

        return "\n---\n".join(context_parts) if context_parts else ""

    async def plan(
        self,
        ctx: CodingContext,
    ) -> str:
        """Generate a coding plan before execution.

        Always plans using the frontier model. No token limits —
        time-based safety via asyncio.wait_for is the guard.
        """
        client = await get_openclaw_client()

        memory = load_coding_memory(ctx.workspace_id)
        memory_section = memory.to_prompt_section() if not memory.is_empty() else ""

        workspace_context = await self._load_workspace_context(ctx.workspace_id)

        prompt = build_coding_prompt(
            memory_section=memory_section,
            task_context=(
                f"Task type: {ctx.task_type}\n"
                f"Project dir: {ctx.project_dir or 'N/A'}\n"
                f"Target file: {ctx.target_file or 'N/A'}"
                + (f"\n\nWorkspace knowledge:\n{workspace_context}" if workspace_context else "")
            ),
        )

        planning_prompt = _build_planning_prompt(ctx.task_type)

        config = ChatConfig(
            model=settings.model_tier_frontier,
            system_prompt=prompt,
            max_tokens=_UNLIMITED_TOKENS,
            temperature=0.3,
        )

        messages = [
            {"role": "user", "content": f"{planning_prompt}\n\nTask: {ctx.task}"},
        ]

        try:
            response = await asyncio.wait_for(
                client.chat_completion(messages=messages, config=config),
                timeout=60,
            )
            plan = response.content or ""
            plan = _sanitize_plan_output(plan)
            usage = response.usage or {}
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            cost = _calc_cost(config.model, prompt_tokens, completion_tokens)
            logger.info(
                "coding_plan_generated",
                workspace_id=ctx.workspace_id,
                model=config.model,
                plan_length=len(plan),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=round(cost, 4),
            )
            return plan
        except asyncio.TimeoutError:
            logger.warning("coding_plan_timeout", workspace_id=ctx.workspace_id)
            return "(Planning timed out — proceed with implementation directly.)"
        except Exception as e:
            logger.error("coding_plan_failed", error=str(e))
            return "(Planning unavailable — proceed with implementation directly.)"

    async def validate(
        self,
        ctx: CodingContext,
    ) -> ValidationResult:
        """Validate the current state of the project."""
        if not ctx.project_dir or not ctx.project_dir.exists():
            return ValidationResult(ok=True, source="skip")

        result = await validate_project(
            ctx.project_dir,
            run_tsc=True,
            run_build=False,
        )

        ctx.validation_attempts += 1

        if not result.ok:
            summary = result.error_summary(max_errors=5)
            ctx.errors_encountered.append(summary)
            logger.warning(
                "coding_validation_failed",
                workspace_id=ctx.workspace_id,
                attempt=ctx.validation_attempts,
                error_count=len(result.errors),
            )
        else:
            logger.info(
                "coding_validation_passed",
                workspace_id=ctx.workspace_id,
                attempt=ctx.validation_attempts,
            )

        return result

    async def validate_and_fix(
        self,
        ctx: CodingContext,
        tool_executor: Callable[..., Awaitable[Any]] | None = None,
    ) -> ValidationResult:
        """Validate code and attempt to fix errors up to MAX_FIX_ATTEMPTS.

        Uses the LLM to generate fixes based on validation errors,
        then re-validates. Stops after MAX_FIX_ATTEMPTS or on success.
        """
        executor = tool_executor or self._tool_executor

        for attempt in range(MAX_FIX_ATTEMPTS):
            result = await self.validate(ctx)
            if result.ok:
                return result

            if attempt >= MAX_FIX_ATTEMPTS - 1:
                logger.warning(
                    "coding_fix_budget_exhausted",
                    workspace_id=ctx.workspace_id,
                    attempts=attempt + 1,
                )
                return result

            if not executor:
                logger.warning("coding_no_executor_for_fix")
                return result

            await self._notify_progress(
                f"Found {len(result.errors)} error(s), fixing (attempt "
                f"{attempt + 1}/{MAX_FIX_ATTEMPTS})..."
            )

            fixed = await self._attempt_fix(ctx, result, executor)
            if not fixed:
                return result

        return ValidationResult(ok=False, source="fix_exhausted")

    async def _attempt_fix(
        self,
        ctx: CodingContext,
        validation: ValidationResult,
        executor: Callable[..., Awaitable[Any]],
    ) -> bool:
        """Ask the LLM to fix validation errors and apply the fix."""
        client = await get_openclaw_client()

        error_text = validation.error_summary(max_errors=8)
        target = ctx.target_file or (
            str(ctx.project_dir / "src" / "App.tsx")
            if ctx.project_dir
            else ""
        )

        if not target:
            return False

        try:
            current_content = Path(target).read_text(encoding="utf-8")
        except Exception:
            return False

        fix_prompt = (
            "Fix the following code errors. Return ONLY the corrected "
            "full file content, no explanations.\n\n"
            f"File: {target}\n"
            f"Errors:\n{error_text}\n\n"
            f"Current content:\n```\n{current_content}\n```"
        )

        config = ChatConfig(
            model=settings.model_tier_code,
            system_prompt="You are a code fixer. Return only the corrected code.",
            max_tokens=_UNLIMITED_TOKENS,
            temperature=0.2,
        )

        try:
            response = await asyncio.wait_for(
                client.chat_completion(
                    messages=[{"role": "user", "content": fix_prompt}],
                    config=config,
                ),
                timeout=60,
            )
            fixed_code = response.content or ""

            if fixed_code.startswith("```"):
                lines = fixed_code.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                fixed_code = "\n".join(lines)

            if len(fixed_code) < 50:
                return False

            result = await executor(
                "lucy_write_file",
                {"path": target, "content": fixed_code},
                ctx.workspace_id,
            )
            success = not result.get("error")

            if success:
                logger.info("coding_fix_applied", file=target)

            return success

        except Exception as e:
            logger.error("coding_fix_failed", error=str(e))
            return False

    async def generate_code(
        self,
        ctx: CodingContext,
        plan: str,
    ) -> str:
        """Generate complete app code in a single focused LLM call.

        This is the core of the orchestrated pipeline — modeled after
        v0, Lovable, and Bolt, which all generate code in one shot
        rather than through iterative tool calling.
        """
        client = await get_openclaw_client()

        memory = load_coding_memory(ctx.workspace_id)
        memory_section = memory.to_prompt_section() if not memory.is_empty() else ""
        workspace_context = await self._load_workspace_context(ctx.workspace_id)

        system_prompt = build_coding_prompt(
            memory_section=memory_section,
            task_context=(
                f"Task type: {ctx.task_type}\n"
                f"Project dir: {ctx.project_dir or 'N/A'}\n"
                f"Target file: {ctx.target_file or 'N/A'}"
                + (f"\n\nWorkspace knowledge:\n{workspace_context}" if workspace_context else "")
            ),
        )

        user_prompt = (
            "Generate a complete React application as a SINGLE App.tsx file.\n\n"
            "CONSTRAINTS:\n"
            "- Write ALL component code inline in one file\n"
            "- Export: export default function App() { ... }\n"
            "- Available libraries (pre-installed, just import them):\n"
            "  * shadcn/ui: import from @/components/ui/ "
            "(Button, Card, Input, Badge, Dialog, Sheet, Tabs, etc.)\n"
            "  * lucide-react: import icons from lucide-react\n"
            "  * framer-motion: import { motion, AnimatePresence } from framer-motion\n"
            "  * recharts: import { LineChart, BarChart, PieChart, etc. } from recharts\n"
            "  * react-router-dom, Tailwind CSS classes\n"
            "- Do NOT import from ./components/ or ./contexts/\n"
            "- Use TypeScript with proper types\n"
            "- Handle loading, empty, and error states\n"
            "- Responsive design (mobile + desktop)\n"
            "- CORS — NON-NEGOTIABLE: This runs in the BROWSER. Any "
            "external API call without CORS headers will silently fail and "
            "show a blank screen to the user.\n"
            "  REQUIRED: Define this helper at the top of your file:\n"
            "    const corsProxy = (url: string) =>\n"
            "      `https://api.allorigins.win/raw?url="
            "${encodeURIComponent(url)}`;\n"
            "  Then use corsProxy() for ALL external API calls EXCEPT these "
            "known CORS-safe APIs: Open-Meteo, sunrise-sunset.org, "
            "jsonplaceholder, restcountries.com, pokeapi.co, randomuser.me.\n"
            "  EVERY fetch/axios call to any other domain MUST go through "
            "corsProxy(). No exceptions.\n"
            "  ALWAYS wrap API calls in try/catch with graceful fallback UI.\n\n"
            "OUTPUT FORMAT:\n"
            "- Output ONLY valid TypeScript/TSX code\n"
            "- Start with import statements, end with the default export\n"
            "- No markdown fences, no explanations, no XML tags\n"
            "- No comments like '// rest of code here' — write EVERY line\n\n"
            f"TASK: {ctx.task}\n\n"
            f"ARCHITECTURE PLAN:\n{plan}\n\n"
            "Now generate the complete App.tsx code:"
        )

        messages = [{"role": "user", "content": user_prompt}]
        complexity = classify_complexity(ctx.task)

        if complexity == "high":
            models_to_try = [settings.model_tier_frontier]
        else:
            models_to_try = [settings.model_tier_code, settings.model_tier_frontier]

        code = ""
        for model_idx, model in enumerate(models_to_try):
            config = ChatConfig(
                model=model,
                system_prompt=system_prompt,
                max_tokens=_UNLIMITED_TOKENS,
                temperature=0.2,
            )
            try:
                response = await asyncio.wait_for(
                    client.chat_completion(messages=messages, config=config),
                    timeout=360,
                )
                code = response.content or ""
                code = _strip_code_fences(code)

                usage = response.usage or {}
                prompt_tokens = usage.get("prompt_tokens", 0)
                completion_tokens = usage.get("completion_tokens", 0)
                cached_tokens = usage.get("cached_tokens", 0)
                cost = _calc_cost(model, prompt_tokens, completion_tokens)

                truncated = await self.detect_truncation(response, config)
                if truncated:
                    logger.info("code_generation_truncated", chars=len(code))
                    await self._notify_progress("Code was truncated, continuing...")
                    code = await self.continue_truncated(messages, code, config)
                    code = _strip_code_fences(code)

                if len(code) < 3000:
                    logger.warning(
                        "code_gen_insufficient_output",
                        model=model,
                        chars=len(code),
                        complexity=complexity,
                    )
                    if model_idx < len(models_to_try) - 1:
                        logger.info(
                            "code_gen_escalating",
                            from_model=model,
                            to_model=models_to_try[model_idx + 1],
                            reason="insufficient_output",
                        )
                        await self._notify_progress("Retrying with a more capable model...")
                        continue
                    return ""

                logger.info(
                    "code_generated",
                    workspace_id=ctx.workspace_id,
                    model=model,
                    complexity=complexity,
                    chars=len(code),
                    tokens=completion_tokens,
                    prompt_tokens=prompt_tokens,
                    cached_tokens=cached_tokens,
                    cost_usd=round(cost, 4),
                    escalated=model_idx > 0,
                )
                return code

            except asyncio.TimeoutError:
                logger.warning(
                    "code_gen_timeout",
                    model=model,
                    complexity=complexity,
                )
                if model_idx < len(models_to_try) - 1:
                    logger.info(
                        "code_gen_escalating",
                        from_model=model,
                        to_model=models_to_try[model_idx + 1],
                        reason="timeout",
                    )
                    await self._notify_progress("Retrying with a more capable model...")
                    continue

        logger.error(
            "code_generation_all_models_failed",
            workspace_id=ctx.workspace_id,
            models_tried=[m for m in models_to_try],
        )
        return ""

    async def fix_code(
        self,
        ctx: CodingContext,
        error_text: str,
    ) -> str:
        """Fix code errors and return the corrected full file content.

        Standalone fix method that doesn't need a tool executor — reads
        the file directly and returns the fixed code string.
        """
        target = ctx.target_file
        if not target:
            return ""

        try:
            current_content = Path(target).read_text(encoding="utf-8")
        except Exception:
            return ""

        client = await get_openclaw_client()

        fix_prompt = (
            "Fix the following TypeScript/React errors. Return ONLY the "
            "corrected COMPLETE file content. No explanations, no markdown "
            "fences.\n\n"
            "IMPORTANT: This is a client-side React app running in the "
            "browser. If the code calls external APIs, it MUST use a "
            "corsProxy helper. Do NOT remove any existing corsProxy usage. "
            "If you see fetch() calls to external APIs without corsProxy, "
            "add it.\n\n"
            f"Errors:\n{error_text}\n\n"
            f"Current code:\n{current_content}"
        )

        config = ChatConfig(
            model=settings.model_tier_code,
            system_prompt="You are a code fixer. Return only the corrected code.",
            max_tokens=_UNLIMITED_TOKENS,
            temperature=0.2,
        )

        try:
            response = await asyncio.wait_for(
                client.chat_completion(
                    messages=[{"role": "user", "content": fix_prompt}],
                    config=config,
                ),
                timeout=120,
            )
            fixed = response.content or ""
            fixed = _strip_code_fences(fixed)

            usage = response.usage or {}
            cost = _calc_cost(
                config.model,
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0),
            )
            logger.info(
                "code_fix_generated",
                model=config.model,
                chars=len(fixed),
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                cost_usd=round(cost, 4),
            )

            if len(fixed) < 50:
                return ""
            return fixed

        except Exception as e:
            logger.error("code_fix_failed", error=str(e))
            return ""

    async def detect_truncation(
        self,
        response: OpenClawResponse,
        config: ChatConfig,
    ) -> bool:
        """Detect if the LLM output was truncated.

        Returns True if completion_tokens equals max_tokens, indicating
        the model ran out of token budget mid-output.
        """
        if not response.usage:
            return False

        completion_tokens = response.usage.get("completion_tokens", 0)
        if completion_tokens >= config.max_tokens - 10:
            logger.warning(
                "output_truncation_detected",
                completion_tokens=completion_tokens,
                max_tokens=config.max_tokens,
            )
            return True
        return False

    async def continue_truncated(
        self,
        messages: list[dict[str, Any]],
        partial_content: str,
        config: ChatConfig,
    ) -> str:
        """Continue generating from a truncated output using prefill.

        Appends the partial output as assistant content and asks the
        model to continue. Supported by Claude, DeepSeek, Mistral, Kimi.
        """
        client = await get_openclaw_client()

        continuation_messages = list(messages)
        continuation_messages.append({
            "role": "assistant",
            "content": partial_content,
        })
        continuation_messages.append({
            "role": "user",
            "content": (
                "Your previous output was truncated. Continue EXACTLY "
                "from where you left off. Do not repeat any code."
            ),
        })

        continuation_config = ChatConfig(
            model=config.model,
            system_prompt=config.system_prompt,
            tools=config.tools,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
        )

        try:
            response = await asyncio.wait_for(
                client.chat_completion(
                    messages=continuation_messages,
                    config=continuation_config,
                ),
                timeout=60,
            )
            continued = response.content or ""
            logger.info(
                "truncation_continued",
                original_len=len(partial_content),
                continuation_len=len(continued),
            )
            return partial_content + continued
        except Exception as e:
            logger.error("truncation_continuation_failed", error=str(e))
            return partial_content


_engine: CodingEngine | None = None


def get_coding_engine() -> CodingEngine:
    """Get or create the singleton CodingEngine instance."""
    global _engine
    if _engine is None:
        _engine = CodingEngine()
    return _engine
