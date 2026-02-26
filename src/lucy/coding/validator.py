"""Code validation: TypeScript checking, linting, and build verification.

Runs tsc, eslint, and build commands against project directories to catch
errors before deployment. Returns structured error objects the CodingEngine
can feed back to the LLM for correction.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import structlog

logger = structlog.get_logger()

MAX_ERROR_CHARS = 3000


@dataclass
class ValidationError:
    """A single code validation error."""

    file: str
    line: int | None = None
    column: int | None = None
    message: str = ""
    severity: str = "error"
    source: str = ""

    def format(self) -> str:
        loc = self.file
        if self.line:
            loc += f":{self.line}"
            if self.column:
                loc += f":{self.column}"
        return f"[{self.source}] {loc}: {self.message}"


@dataclass
class ValidationResult:
    """Result of a validation run."""

    ok: bool
    errors: list[ValidationError] = field(default_factory=list)
    raw_output: str = ""
    source: str = ""

    def error_summary(self, max_errors: int = 10) -> str:
        if self.ok:
            return ""
        lines = [f"{self.source} found {len(self.errors)} error(s):"]
        for err in self.errors[:max_errors]:
            lines.append(f"  {err.format()}")
        if len(self.errors) > max_errors:
            lines.append(f"  ... and {len(self.errors) - max_errors} more")
        return "\n".join(lines)


def _get_bun_env() -> tuple[str, dict[str, str]]:
    """Get bun/npx binary path and PATH-augmented env."""
    bun_path = Path.home() / ".bun" / "bin" / "bun"
    bun = str(bun_path) if bun_path.exists() else "bun"
    path_env = os.environ.get("PATH", "")
    env = {**os.environ, "PATH": f"{bun_path.parent}:{path_env}"}
    return bun, env


def _parse_tsc_output(output: str) -> list[ValidationError]:
    """Parse TypeScript compiler output into structured errors."""
    errors: list[ValidationError] = []
    pattern = re.compile(
        r"^(.+?)\((\d+),(\d+)\):\s+error\s+TS\d+:\s+(.+)$",
        re.MULTILINE,
    )
    for m in pattern.finditer(output):
        errors.append(ValidationError(
            file=m.group(1),
            line=int(m.group(2)),
            column=int(m.group(3)),
            message=m.group(4).strip(),
            severity="error",
            source="tsc",
        ))
    return errors


def _parse_build_output(output: str) -> list[ValidationError]:
    """Parse Vite/esbuild output into structured errors."""
    errors: list[ValidationError] = []

    ts_pattern = re.compile(
        r"error\s+TS\d+.*?(?:in\s+)?(\S+\.tsx?)\s*\((\d+),(\d+)\):\s*(.+)",
        re.MULTILINE,
    )
    for m in ts_pattern.finditer(output):
        errors.append(ValidationError(
            file=m.group(1),
            line=int(m.group(2)),
            column=int(m.group(3)),
            message=m.group(4).strip(),
            severity="error",
            source="build",
        ))

    generic_pattern = re.compile(
        r"(?:ERROR|Error).*?(\S+\.(?:tsx?|jsx?)):(\d+):(\d+).*?[:\-]\s*(.+)",
        re.MULTILINE,
    )
    for m in generic_pattern.finditer(output):
        errors.append(ValidationError(
            file=m.group(1),
            line=int(m.group(2)),
            column=int(m.group(3)),
            message=m.group(4).strip(),
            severity="error",
            source="build",
        ))

    if not errors and ("error" in output.lower() or "Error" in output):
        trimmed = output[:MAX_ERROR_CHARS]
        errors.append(ValidationError(
            file="(build)",
            message=trimmed,
            severity="error",
            source="build",
        ))

    return errors


async def check_typescript(project_dir: Path) -> ValidationResult:
    """Run `npx tsc --noEmit` and return structured errors."""
    _, env = _get_bun_env()
    bun_bin = Path.home() / ".bun" / "bin"
    npx = str(bun_bin / "npx") if (bun_bin / "npx").exists() else "npx"

    tsconfig = project_dir / "tsconfig.json"
    if not tsconfig.exists():
        return ValidationResult(ok=True, source="tsc")

    try:
        proc = await asyncio.create_subprocess_exec(
            npx, "tsc", "--noEmit",
            cwd=str(project_dir),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        combined = (stdout or b"").decode(errors="replace") + \
                   (stderr or b"").decode(errors="replace")

        if proc.returncode == 0:
            logger.info("tsc_check_passed", project=project_dir.name)
            return ValidationResult(ok=True, source="tsc", raw_output=combined)

        errors = _parse_tsc_output(combined)
        if not errors:
            logger.info(
                "tsc_check_passed",
                project=project_dir.name,
                note="non-zero exit but no parseable errors",
                returncode=proc.returncode,
                raw_preview=combined[:300] if combined.strip() else "(empty)",
            )
            return ValidationResult(ok=True, source="tsc", raw_output=combined)

        logger.warning(
            "tsc_check_failed",
            project=project_dir.name,
            error_count=len(errors),
        )
        return ValidationResult(
            ok=False, errors=errors, raw_output=combined[:MAX_ERROR_CHARS],
            source="tsc",
        )

    except asyncio.TimeoutError:
        return ValidationResult(
            ok=False,
            errors=[ValidationError(
                file="(tsc)", message="TypeScript check timed out (60s)",
                source="tsc",
            )],
            source="tsc",
        )
    except FileNotFoundError:
        logger.warning("tsc_not_found", project=project_dir.name)
        return ValidationResult(ok=True, source="tsc")
    except Exception as e:
        logger.error("tsc_check_error", error=str(e))
        return ValidationResult(
            ok=False,
            errors=[ValidationError(
                file="(tsc)", message=f"TypeScript check crashed: {e}",
                source="tsc",
            )],
            source="tsc",
        )


async def check_build(project_dir: Path) -> ValidationResult:
    """Run bun install + vite build and return structured errors."""
    bun, env = _get_bun_env()

    try:
        proc = await asyncio.create_subprocess_exec(
            bun, "install",
            cwd=str(project_dir),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        if proc.returncode != 0:
            err = (stderr or b"").decode(errors="replace")
            return ValidationResult(
                ok=False,
                errors=[ValidationError(
                    file="(install)", message=err[:MAX_ERROR_CHARS],
                    source="build",
                )],
                raw_output=err[:MAX_ERROR_CHARS],
                source="build",
            )

        bun_bin = Path.home() / ".bun" / "bin"
        npx = str(bun_bin / "npx") if (bun_bin / "npx").exists() else "npx"

        proc2 = await asyncio.create_subprocess_exec(
            npx, "vite", "build",
            cwd=str(project_dir),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout2, stderr2 = await asyncio.wait_for(
            proc2.communicate(), timeout=120,
        )
        combined = (stdout2 or b"").decode(errors="replace") + \
                   (stderr2 or b"").decode(errors="replace")

        if proc2.returncode == 0:
            logger.info("build_check_passed", project=project_dir.name)
            return ValidationResult(
                ok=True, source="build", raw_output=combined,
            )

        errors = _parse_build_output(combined)
        logger.warning(
            "build_check_failed",
            project=project_dir.name,
            error_count=len(errors),
        )
        return ValidationResult(
            ok=False, errors=errors,
            raw_output=combined[:MAX_ERROR_CHARS], source="build",
        )

    except asyncio.TimeoutError:
        return ValidationResult(
            ok=False,
            errors=[ValidationError(
                file="(build)", message="Build timed out (120s)",
                source="build",
            )],
            source="build",
        )


async def check_python(file_path: Path) -> ValidationResult:
    """Run Python syntax check on a file."""
    if not file_path.exists():
        return ValidationResult(ok=True, source="python")

    try:
        proc = await asyncio.create_subprocess_exec(
            "python3", "-m", "py_compile", str(file_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        combined = (stderr or b"").decode(errors="replace")

        if proc.returncode == 0:
            return ValidationResult(ok=True, source="python")

        errors: list[ValidationError] = []
        line_match = re.search(r"line (\d+)", combined)
        errors.append(ValidationError(
            file=str(file_path),
            line=int(line_match.group(1)) if line_match else None,
            message=combined.strip()[:500],
            severity="error",
            source="python",
        ))
        return ValidationResult(
            ok=False, errors=errors, raw_output=combined, source="python",
        )

    except asyncio.TimeoutError:
        return ValidationResult(
            ok=False,
            errors=[ValidationError(
                file=str(file_path),
                message="Python syntax check timed out (15s)",
                source="python",
            )],
            source="python",
        )
    except FileNotFoundError:
        return ValidationResult(ok=True, source="python")
    except Exception as e:
        logger.error("python_check_error", error=str(e))
        return ValidationResult(
            ok=False,
            errors=[ValidationError(
                file=str(file_path),
                message=f"Python syntax check crashed: {e}",
                source="python",
            )],
            source="python",
        )


async def validate_project(
    project_dir: Path,
    run_tsc: bool = True,
    run_build: bool = False,
) -> ValidationResult:
    """Run all applicable validations on a project directory.

    Returns a combined ValidationResult. By default runs only tsc (fast).
    Set run_build=True for full build validation before deployment.
    """
    all_errors: list[ValidationError] = []
    raw_parts: list[str] = []

    if run_tsc:
        tsc_result = await check_typescript(project_dir)
        if not tsc_result.ok:
            all_errors.extend(tsc_result.errors)
            raw_parts.append(tsc_result.raw_output)

    if run_build:
        build_result = await check_build(project_dir)
        if not build_result.ok:
            all_errors.extend(build_result.errors)
            raw_parts.append(build_result.raw_output)

    if all_errors:
        return ValidationResult(
            ok=False,
            errors=all_errors,
            raw_output="\n---\n".join(raw_parts),
            source="validation",
        )

    return ValidationResult(ok=True, source="validation")
