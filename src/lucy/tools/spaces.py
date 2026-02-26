"""Lucy Spaces tool definitions and executor.

Provides tool definitions for building and deploying web applications
via Lucy Spaces. The init tool runs the full orchestrated pipeline:
plan → generate → write → validate → fix → deploy.

Architecture modeled after v0, Lovable, and Bolt — code is generated
in a single focused LLM call, then validated and deployed programmatically.
"""

from __future__ import annotations

import asyncio
import json
import re as _re
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

import structlog

logger = structlog.get_logger()

_LOG_PATH = "/Users/ojashyadav/SEO Code/lucy/.cursor/debug-2fecae.log"
_MAX_FIX_ATTEMPTS = 3


def _dbg(location: str, message: str, data: dict[str, Any]) -> None:
    """Write a single NDJSON debug line."""
    try:
        entry = {
            "sessionId": "2fecae",
            "runId": "pipeline",
            "hypothesisId": "F",
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with open(_LOG_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def get_spaces_tool_definitions() -> list[dict[str, Any]]:
    """Return OpenAI-format tool definitions for spaces operations."""
    return [
        {
            "type": "function",
            "function": {
                "name": "lucy_spaces_init",
                "description": (
                    "Build, update, or rebuild a web application. "
                    "For NEW apps: provide a name and description — "
                    "the system plans, generates code, validates, and "
                    "deploys to zeeya.app automatically. "
                    "For UPDATING existing apps: use the SAME project "
                    "name and describe the changes/additions wanted — "
                    "the system reads the existing code, enhances it, "
                    "and redeploys. Returns the live URL when complete. "
                    "This handles everything end-to-end."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_name": {
                            "type": "string",
                            "description": (
                                "Name for the app (e.g. 'calculator', "
                                "'task-manager'). Will be slugified."
                            ),
                        },
                        "description": {
                            "type": "string",
                            "description": (
                                "Detailed description of the app. Include "
                                "all features, UI requirements, and "
                                "functionality. The more detail, the better "
                                "the generated app."
                            ),
                        },
                    },
                    "required": ["project_name", "description"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "lucy_spaces_deploy",
                "description": (
                    "Re-deploy a Lucy Spaces app after making edits. "
                    "Only use this after manually editing code with "
                    "lucy_edit_file. For new apps, use lucy_spaces_init "
                    "which handles deployment automatically."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_name": {
                            "type": "string",
                            "description": "Name of the project to deploy.",
                        },
                        "environment": {
                            "type": "string",
                            "enum": ["preview", "production"],
                            "description": "Deploy target. Default: production.",
                        },
                    },
                    "required": ["project_name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "lucy_spaces_list",
                "description": (
                    "List all web applications you have built and deployed."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "lucy_spaces_status",
                "description": (
                    "Get detailed status of a deployed app including URLs, "
                    "deployment history, and Convex database info."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_name": {
                            "type": "string",
                            "description": "Name of the project.",
                        },
                    },
                    "required": ["project_name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "lucy_spaces_delete",
                "description": (
                    "Delete a Lucy Spaces app. Removes the Convex project, "
                    "Vercel deployment, and all local files. Irreversible."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_name": {
                            "type": "string",
                            "description": "Name of the project to delete.",
                        },
                    },
                    "required": ["project_name"],
                },
            },
        },
    ]


_SPACES_TOOL_NAMES = frozenset({
    "lucy_spaces_init",
    "lucy_spaces_deploy",
    "lucy_spaces_list",
    "lucy_spaces_status",
    "lucy_spaces_delete",
})


def is_spaces_tool(tool_name: str) -> bool:
    """Check if a tool name belongs to the spaces tool suite."""
    return tool_name in _SPACES_TOOL_NAMES


_BUILTIN_MODULES = frozenset({
    "react", "react-dom", "react-dom/client", "react/jsx-runtime",
})

_TEMPLATE_PACKAGES = frozenset({
    "react", "react-dom", "@types/react", "@types/react-dom",
    "typescript", "vite", "@vitejs/plugin-react",
    "tailwindcss", "postcss", "autoprefixer",
    "@tailwindcss/vite",
    "lucide-react", "framer-motion", "recharts",
    "react-router-dom",
    "class-variance-authority", "clsx", "tailwind-merge",
    "@radix-ui/react-slot",
})


def _detect_missing_deps(code: str, project_dir: Path) -> list[str]:
    """Parse imports from generated code and return packages not in package.json."""
    pkg_json = project_dir / "package.json"
    installed: set[str] = set(_TEMPLATE_PACKAGES)
    if pkg_json.exists():
        try:
            pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
            installed.update(pkg.get("dependencies", {}).keys())
            installed.update(pkg.get("devDependencies", {}).keys())
        except Exception:
            pass

    import_pattern = _re.compile(
        r'''(?:from\s+['"]([^'"./][^'"]*?)['"]|'''
        r'''require\s*\(\s*['"]([^'"./][^'"]*?)['"]\s*\))''',
    )
    raw_modules: set[str] = set()
    for m in import_pattern.finditer(code):
        mod = m.group(1) or m.group(2)
        if mod:
            raw_modules.add(mod)

    needed: set[str] = set()
    for mod in raw_modules:
        if mod in _BUILTIN_MODULES:
            continue
        pkg_name = mod if not mod.startswith("@") else "/".join(mod.split("/")[:2])
        base = pkg_name.split("/")[0] if not pkg_name.startswith("@") else pkg_name
        if base not in installed and pkg_name not in installed:
            needed.add(pkg_name)

    needed.discard("@/components/ui/button")
    needed.discard("@/components/ui/card")
    needed.discard("@/components/ui/input")
    needed = {p for p in needed if not p.startswith("@/") and not p.startswith(".")}

    return sorted(needed)


_CORS_SAFE_DOMAINS = frozenset({
    "api.open-meteo.com",
    "air-quality-api.open-meteo.com",
    "geocoding-api.open-meteo.com",
    "archive-api.open-meteo.com",
    "flood-api.open-meteo.com",
    "marine-api.open-meteo.com",
    "api.sunrise-sunset.org",
    "jsonplaceholder.typicode.com",
    "restcountries.com",
    "pokeapi.co",
    "api.github.com",
    "hacker-news.firebaseio.com",
    "openlibrary.org",
    "api.quotable.io",
    "randomuser.me",
    "api.adviceslip.com",
    "catfact.ninja",
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "cdn.jsdelivr.net",
    "unpkg.com",
    "cdnjs.cloudflare.com",
})

_CORS_PROXY_HELPER = (
    "\nconst corsProxy = (url: string) =>\n"
    "  `https://api.allorigins.win/raw?url=${encodeURIComponent(url)}`;\n"
)


def _enforce_cors_proxies(code: str) -> tuple[str, int]:
    """Scan generated code for unproxied external API calls and auto-fix.

    Strategy:
    1. Detect ALL external URLs hitting non-CORS-safe domains.
    2. Inject a ``corsProxy`` helper if one doesn't already exist.
    3. Wrap ``fetch`` / ``axios`` calls and URL constants with
       ``corsProxy()`` so the browser never makes a direct cross-origin
       request that would be blocked.

    Returns ``(fixed_code, num_fixes)``.
    """
    proxy_markers = ("allorigins", "corsproxy", "corsProxy", "cors_proxy")
    has_proxy_helper = any(
        _re.search(rf'\b{_re.escape(m)}\b', code) for m in ("corsProxy", "cors_proxy")
    )

    url_hit = _re.compile(r'https?://([a-zA-Z0-9][-a-zA-Z0-9.]*\.[a-zA-Z]{2,})')
    external_domains: set[str] = set()
    for m in url_hit.finditer(code):
        domain = m.group(1).lower()
        if domain in _CORS_SAFE_DOMAINS or domain in ("localhost", "127.0.0.1"):
            continue
        ctx_start = max(0, m.start() - 120)
        if any(marker in code[ctx_start:m.start()] for marker in proxy_markers):
            continue
        external_domains.add(domain)

    if not external_domains:
        return code, 0

    fixes = 0

    if not has_proxy_helper:
        last_import = None
        for m in _re.finditer(r'^import\s+.+$', code, _re.MULTILINE):
            last_import = m
        if last_import:
            pos = last_import.end()
            code = code[:pos] + _CORS_PROXY_HELPER + code[pos:]
        else:
            code = _CORS_PROXY_HELPER + code
        fixes += 1

    def _already_proxied(match: _re.Match[str], url_group_idx: int = 3) -> bool:
        """Check if this specific URL occurrence is already behind a proxy."""
        url = match.group(url_group_idx) if match.lastindex >= url_group_idx else ""
        if "allorigins" in url or "corsproxy" in url:
            return True
        pre = code[max(0, match.start() - 15):match.start()]
        return "corsProxy(" in pre

    for domain in external_domains:
        esc = _re.escape(domain)

        p_fetch_str = _re.compile(
            r'''(fetch\s*\(\s*)(['"])(https?://''' + esc + r'''[^'"]*)\2''',
        )
        for m in p_fetch_str.finditer(code):
            if _already_proxied(m):
                continue
            old, q, url = m.group(0), m.group(2), m.group(3)
            code = code.replace(old, f'{m.group(1)}corsProxy({q}{url}{q})', 1)
            fixes += 1

        p_fetch_tpl = _re.compile(
            r'''(fetch\s*\(\s*)(`https?://''' + esc + r'''[^`]*`)''',
        )
        for m in p_fetch_tpl.finditer(code):
            if _already_proxied(m, 2):
                continue
            old, tpl = m.group(0), m.group(2)
            code = code.replace(old, f'{m.group(1)}corsProxy({tpl})', 1)
            fixes += 1

        p_axios = _re.compile(
            r'''(axios\.\w+\s*\(\s*)(['"`])(https?://''' + esc + r'''[^'"`]*)\2''',
        )
        for m in p_axios.finditer(code):
            if _already_proxied(m):
                continue
            old, q, url = m.group(0), m.group(2), m.group(3)
            code = code.replace(old, f'{m.group(1)}corsProxy({q}{url}{q})', 1)
            fixes += 1

        p_const_str = _re.compile(
            r'''((?:const|let|var)\s+\w+\s*=\s*)(['"])(https?://''' + esc + r'''[^'"]*)\2''',
        )
        for m in p_const_str.finditer(code):
            if _already_proxied(m):
                continue
            old, q, url = m.group(0), m.group(2), m.group(3)
            code = code.replace(old, f'{m.group(1)}corsProxy({q}{url}{q})', 1)
            fixes += 1

        p_const_tpl = _re.compile(
            r'''((?:const|let|var)\s+\w+\s*=\s*)(`https?://''' + esc + r'''[^`]*`)''',
        )
        for m in p_const_tpl.finditer(code):
            if _already_proxied(m, 2):
                continue
            old, tpl = m.group(0), m.group(2)
            code = code.replace(old, f'{m.group(1)}corsProxy({tpl})', 1)
            fixes += 1

    return code, fixes


async def _build_full_app(
    project_name: str,
    description: str,
    workspace_id: str,
    progress: Callable[[str], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Orchestrated build pipeline: scaffold → plan → generate → validate → fix → deploy.

    Runs the complete app generation flow programmatically — the LLM
    is only called for planning and code generation, everything else
    is deterministic. Modeled after v0/Lovable/Bolt architecture.
    """
    from lucy.coding.engine import CodingContext, classify_complexity, get_coding_engine
    from lucy.spaces.platform import deploy_app, init_app_project

    pipeline_start = time.monotonic()

    async def _notify(msg: str) -> None:
        if progress:
            try:
                await progress(msg)
            except Exception:
                pass

    complexity = classify_complexity(description)
    _dbg("pipeline:complexity", "task complexity classified", {
        "complexity": complexity,
        "description_words": len(description.split()),
    })

    # ── Phase 1: Scaffold (or reuse existing project) ──────────────
    _dbg("pipeline:scaffold", "starting scaffold", {"project_name": project_name})
    await _notify(f"Setting up project {project_name}...")

    init_result = await init_app_project(
        project_name=project_name,
        description=description,
        workspace_id=workspace_id,
    )

    if not init_result.get("success"):
        err = init_result.get("error", "")
        if "already exists" in err:
            from lucy.config import settings
            import re
            slug = re.sub(r"[^a-z0-9-]", "-", project_name.lower().strip())
            slug = re.sub(r"-+", "-", slug).strip("-")[:40]
            existing_dir = (
                settings.workspace_root / workspace_id / "spaces" / slug
            )
            if existing_dir.exists():
                _dbg("pipeline:reuse_existing", "reusing existing project", {
                    "slug": slug, "path": str(existing_dir),
                })
                sandbox_path = str(existing_dir)
                init_result = {
                    "success": True,
                    "project_name": project_name,
                    "slug": slug,
                    "sandbox_path": sandbox_path,
                    "reused": True,
                }
            else:
                return init_result
        else:
            return init_result

    sandbox_path = init_result["sandbox_path"]
    app_tsx = f"{sandbox_path}/src/App.tsx"
    project_dir = Path(sandbox_path)

    _dbg("pipeline:scaffold_done", "scaffold complete", {
        "sandbox_path": sandbox_path,
        "slug": init_result.get("slug"),
        "reused": init_result.get("reused", False),
    })

    if not init_result.get("reused", False):
        bun_path = Path.home() / ".bun" / "bin" / "bun"
        bun = str(bun_path) if bun_path.exists() else "bun"
        try:
            proc = await asyncio.create_subprocess_exec(
                bun, "install",
                cwd=sandbox_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=30)
        except Exception:
            pass

    # ── Phase 2: Plan ────────────────────────────────────────────────
    await _notify("Planning the architecture...")
    engine = get_coding_engine()

    existing_code = ""
    is_update = init_result.get("reused", False)
    if is_update and Path(app_tsx).exists():
        try:
            existing_code = Path(app_tsx).read_text(encoding="utf-8")
            _dbg("pipeline:existing_code_read", "read existing App.tsx for update", {
                "chars": len(existing_code),
            })
        except Exception:
            existing_code = ""

    task = description
    if is_update and existing_code and len(existing_code) > 200:
        task = (
            f"UPDATE the existing app with these changes: {description}\n\n"
            f"EXISTING CODE (modify this, keep all working features):\n"
            f"```tsx\n{existing_code}\n```"
        )

    ctx = CodingContext(
        workspace_id=workspace_id,
        task=task,
        project_dir=project_dir,
        target_file=app_tsx,
        task_type="spaces",
    )

    plan = await engine.plan(ctx)
    _dbg("pipeline:plan_done", "plan generated", {
        "plan_length": len(plan), "preview": plan[:150],
        "is_update": is_update,
    })

    # ── Phase 3: Generate code ───────────────────────────────────────
    await _notify("Updating the code..." if is_update else "Writing the code...")
    code = await engine.generate_code(ctx, plan)

    if not code or len(code) < 100:
        _dbg("pipeline:generate_failed", "code generation produced empty/short output", {
            "code_length": len(code) if code else 0,
        })
        return {
            "success": False,
            "error": "Code generation failed — the model returned insufficient output.",
            "sandbox_path": sandbox_path,
            "project_name": project_name,
            "slug": init_result.get("slug"),
        }

    # ── Phase 3.2: CORS enforcement ──────────────────────────────────
    code, cors_fixes = _enforce_cors_proxies(code)
    if cors_fixes:
        _dbg("pipeline:cors_enforced", "auto-proxied unprotected API calls", {
            "fixes_applied": cors_fixes,
        })
        logger.info(
            "cors_enforcement",
            project=project_name,
            fixes=cors_fixes,
        )

    # Write code to disk
    Path(app_tsx).write_text(code, encoding="utf-8")
    ctx.files_written.append(app_tsx)
    _dbg("pipeline:code_written", "code written to disk", {
        "path": app_tsx, "chars": len(code),
    })

    # ── Phase 3.5: Auto-install missing dependencies ──────────────────
    missing = _detect_missing_deps(code, project_dir)
    if missing:
        _dbg("pipeline:auto_deps", "installing missing deps", {"packages": missing})
        await _notify(f"Installing dependencies: {', '.join(missing)}...")
        bun_path = Path.home() / ".bun" / "bin" / "bun"
        bun = str(bun_path) if bun_path.exists() else "bun"
        try:
            proc = await asyncio.create_subprocess_exec(
                bun, "add", *missing,
                cwd=sandbox_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode == 0:
                logger.info("auto_deps_installed", packages=missing, project=project_name)
            else:
                err_msg = (stderr_b or b"").decode(errors="replace")[:200]
                logger.warning("auto_deps_failed", packages=missing, error=err_msg)
        except Exception as exc:
            logger.warning("auto_deps_error", error=str(exc)[:200])

    # ── Phase 4: Validate & fix loop ─────────────────────────────────
    await _notify("Validating the code...")

    validation_passed = False
    for attempt in range(_MAX_FIX_ATTEMPTS):
        validation = await engine.validate(ctx)

        if validation.ok:
            validation_passed = True
            _dbg("pipeline:validation_passed", "code passes validation", {
                "attempt": attempt,
            })
            break

        error_summary = validation.error_summary(max_errors=8)
        _dbg("pipeline:validation_failed", "validation errors found", {
            "attempt": attempt,
            "error_count": len(validation.errors),
            "preview": error_summary[:200],
        })

        if attempt >= _MAX_FIX_ATTEMPTS - 1:
            logger.warning(
                "pipeline_fix_budget_exhausted",
                attempts=attempt + 1,
                errors=len(validation.errors),
            )
            break

        await _notify(
            f"Found {len(validation.errors)} error(s), fixing "
            f"(attempt {attempt + 1}/{_MAX_FIX_ATTEMPTS})..."
        )

        fixed_code = await engine.fix_code(ctx, error_summary)
        if not fixed_code:
            _dbg("pipeline:fix_failed", "fix returned empty", {"attempt": attempt})
            break

        fixed_code, cors_re_fixes = _enforce_cors_proxies(fixed_code)
        if cors_re_fixes:
            _dbg("pipeline:cors_enforced_in_fix", "re-applied CORS proxies after fix", {
                "attempt": attempt, "fixes": cors_re_fixes,
            })

        Path(app_tsx).write_text(fixed_code, encoding="utf-8")
        _dbg("pipeline:fix_applied", "fix written to disk", {
            "attempt": attempt, "chars": len(fixed_code),
        })

    if not validation_passed:
        logger.warning(
            "pipeline_deploying_with_errors",
            project=project_name,
            note="validation did not pass, attempting deploy anyway (vite build is final gate)",
        )

    # ── Phase 5: Deploy ──────────────────────────────────────────────
    await _notify("Deploying to zeeya.app...")

    deploy_result = await deploy_app(
        project_name=project_name,
        workspace_id=workspace_id,
        environment="production",
    )

    if not deploy_result.get("success"):
        _dbg("pipeline:deploy_failed", "deployment failed", {
            "error": deploy_result.get("error", "")[:200],
        })

        if deploy_result.get("fixable"):
            deploy_error = deploy_result.get("error", "")
            await _notify("Build errors detected, attempting fix...")
            fixed_code = await engine.fix_code(ctx, deploy_error)
            if fixed_code:
                Path(app_tsx).write_text(fixed_code, encoding="utf-8")
                deploy_result = await deploy_app(
                    project_name=project_name,
                    workspace_id=workspace_id,
                    environment="production",
                )

        if not deploy_result.get("success"):
            return {
                "success": False,
                "error": deploy_result.get("error", "Deployment failed"),
                "sandbox_path": sandbox_path,
                "summary": (
                    f"I built the app but deployment failed: "
                    f"{deploy_result.get('error', 'unknown error')}. "
                    f"The code is saved — I can try to fix and redeploy."
                ),
            }

    url = deploy_result["url"]
    warning = deploy_result.get("validation_warning", "")

    pipeline_elapsed = round(time.monotonic() - pipeline_start, 1)

    logger.info(
        "pipeline_complete_summary",
        project=project_name,
        complexity=complexity,
        url=url,
        is_update=is_update,
        elapsed_seconds=pipeline_elapsed,
        warning=warning or None,
    )

    display_name = project_name.replace("-", " ").title()
    action = "updated" if is_update else "ready"
    user_summary = f"Your {display_name} app is {action}! :tada:\n\n{url}"

    return {
        "success": True,
        "url": url,
        "project_name": project_name,
        "slug": init_result.get("slug"),
        "deployment_id": deploy_result.get("deployment_id"),
        "summary": user_summary,
    }


async def execute_spaces_tool(
    tool_name: str,
    parameters: dict[str, Any],
    workspace_id: str,
    progress_callback: Callable[[str], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Dispatch a spaces tool call to the platform service.

    For lucy_spaces_init, runs the full orchestrated build pipeline.
    Returns structured results with a 'summary' field for the user.
    """
    from lucy.spaces.platform import (
        delete_app_project,
        deploy_app,
        get_app_status,
        list_apps,
    )

    try:
        if tool_name == "lucy_spaces_init":
            return await _build_full_app(
                project_name=parameters.get("project_name", ""),
                description=parameters.get("description", ""),
                workspace_id=workspace_id,
                progress=progress_callback,
            )

        if tool_name == "lucy_spaces_deploy":
            result = await deploy_app(
                project_name=parameters.get("project_name", ""),
                workspace_id=workspace_id,
                environment=parameters.get("environment", "production"),
            )
            if result.get("success"):
                url = result["url"]
                warning = result.get("validation_warning", "")
                if warning:
                    result["summary"] = (
                        f"App deployed to {url} but validation detected "
                        f"an issue: {warning}. The app may not render "
                        f"correctly. Consider reviewing App.tsx for errors "
                        f"and redeploying."
                    )
                else:
                    result["summary"] = (
                        f"App deployed and verified! "
                        f"The live URL is: {url} "
                        f"— share this EXACT link with the user, "
                        f"including the full query string."
                    )
            elif result.get("fixable"):
                result["summary"] = (
                    f"Build failed with fixable errors. "
                    f"Fix these errors using lucy_edit_file, then call "
                    f"lucy_spaces_deploy again. Error details:\n"
                    f"{result.get('error', 'Unknown error')}"
                )
            return result

        if tool_name == "lucy_spaces_list":
            result = await list_apps(workspace_id=workspace_id)
            if result["count"] == 0:
                result["summary"] = "No apps deployed yet."
            else:
                lines = [f"You have {result['count']} app(s):"]
                for app in result["apps"]:
                    lines.append(f"- {app['name']}: {app['url']}")
                result["summary"] = "\n".join(lines)
            return result

        if tool_name == "lucy_spaces_status":
            result = await get_app_status(
                project_name=parameters.get("project_name", ""),
                workspace_id=workspace_id,
            )
            if result.get("success"):
                result["summary"] = (
                    f"App '{result['name']}': {result['url']} "
                    f"(last deployed: {result.get('last_deployed') or 'never'})"
                )
            return result

        if tool_name == "lucy_spaces_delete":
            result = await delete_app_project(
                project_name=parameters.get("project_name", ""),
                workspace_id=workspace_id,
            )
            if result.get("success"):
                result["summary"] = "App deleted."
            return result

        return {"error": f"Unknown spaces tool: {tool_name}"}

    except Exception as e:
        logger.error(
            "spaces_tool_failed",
            tool=tool_name,
            error=str(e),
        )
        return {"error": str(e)}
