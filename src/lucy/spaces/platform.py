"""Lucy Spaces platform orchestrator.

Coordinates Convex, Vercel, and the template to manage the full
lifecycle of a space project: init, deploy, list, status, delete.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from lucy.config import settings
from lucy.spaces.convex_api import get_convex_api
from lucy.spaces.project_config import SpaceProject
from lucy.spaces.vercel_api import get_vercel_api

logger = structlog.get_logger()

_TEMPLATE_DIR = Path(__file__).parent.parent.parent.parent / "templates" / "lucy-spaces"


def _slugify(name: str) -> str:
    """Convert a project name to a URL-safe slug."""
    slug = re.sub(r"[^a-z0-9-]", "-", name.lower().strip())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:40]


def _spaces_dir(workspace_id: str) -> Path:
    return settings.workspace_root / workspace_id / "spaces"


def _project_dir(workspace_id: str, slug: str) -> Path:
    return _spaces_dir(workspace_id) / slug


def _project_config_path(workspace_id: str, slug: str) -> Path:
    return _project_dir(workspace_id, slug) / "project.json"



async def init_app_project(
    project_name: str,
    description: str,
    workspace_id: str,
) -> dict[str, Any]:
    """Scaffold a new Lucy Spaces project.

    1. Validate + slugify name
    2. Copy template to workspace
    3. Create Convex project + deployment
    4. Create Vercel project + domain + protection bypass
    5. Generate secrets + write .env.local
    6. Save project.json
    """
    slug = _slugify(project_name)
    if not slug:
        return {"success": False, "error": "Invalid project name"}

    project_dir = _project_dir(workspace_id, slug)
    if project_dir.exists():
        return {"success": False, "error": f"Project '{slug}' already exists"}

    if not _TEMPLATE_DIR.exists():
        return {"success": False, "error": "Lucy Spaces template not found"}

    short_hash = secrets.token_hex(3)
    subdomain = f"{slug}-{short_hash}.{settings.spaces_domain}"
    project_secret = secrets.token_hex(32)
    vercel_project_name = f"lucy-{slug}-{short_hash}"

    # Cleanup registry: we append cloud resources as they are created so that
    # if any step fails, we roll back only what was actually provisioned.
    # Each entry is a (label, async_cleanup_coroutine) pair.
    _created_convex_project_id: str = ""
    _created_vercel_project_id: str = ""

    async def _rollback() -> None:
        """Best-effort cleanup of cloud resources created so far."""
        if _created_vercel_project_id:
            try:
                vercel = get_vercel_api()
                await vercel.delete_project(_created_vercel_project_id)
                logger.info("spaces_rollback_vercel_deleted", vercel_id=_created_vercel_project_id)
            except Exception as rb_err:
                logger.warning(
                    "spaces_rollback_vercel_failed",
                    vercel_id=_created_vercel_project_id,
                    error=str(rb_err),
                )
        if _created_convex_project_id:
            try:
                convex = get_convex_api()
                await convex.delete_project(_created_convex_project_id)
                logger.info("spaces_rollback_convex_deleted", convex_id=_created_convex_project_id)
            except Exception as rb_err:
                logger.warning(
                    "spaces_rollback_convex_failed",
                    convex_id=_created_convex_project_id,
                    error=str(rb_err),
                )
        if project_dir.exists():
            shutil.rmtree(project_dir, ignore_errors=True)

    try:
        shutil.copytree(
            _TEMPLATE_DIR, project_dir,
            ignore=shutil.ignore_patterns(
                "node_modules", ".git", "dist", "tmp", "bun.lock",
                "package-lock.json", "_generated",
            ),
        )
        logger.info("spaces_template_copied", slug=slug)

        convex = get_convex_api()
        convex_result = await convex.create_project(project_name)
        _created_convex_project_id = convex_result["projectId"]
        convex_project_id = _created_convex_project_id

        # Use the first deployment that exists, or create a dev one.
        # Sort by name to get deterministic results (avoids mixing dev/prod).
        deployments = await convex.list_deployments(convex_project_id)
        dev_deps = [d for d in deployments if d.get("deploymentType") == "dev"]
        if dev_deps:
            dev_dep = dev_deps[0]
        elif deployments:
            dev_dep = deployments[0]
        else:
            dev_dep = await convex.create_deployment(convex_project_id, "dev")

        deployment_name = dev_dep["name"]
        deployment_url = dev_dep.get("deploymentUrl", "")

        deploy_key_result = await convex.create_deploy_key(deployment_name)
        deploy_key = deploy_key_result.get("deployKey", "")

        vercel = get_vercel_api()
        vercel_result = await vercel.create_project(vercel_project_name)
        _created_vercel_project_id = vercel_result["id"]
        vercel_project_id = _created_vercel_project_id

        bypass_secret = await vercel.generate_protection_bypass(vercel_project_id)
        await vercel.add_domain(vercel_project_id, subdomain)

        # All cloud resources provisioned — now write local files.
        # Writing config LAST ensures that if anything above fails, we can
        # rollback the cloud resources and leave no orphaned project.json.
        env_local = project_dir / ".env.local"
        env_local.write_text(
            f"VITE_CONVEX_URL={deployment_url}\n"
            f"LUCY_SPACES_API_URL=\n"
            f"LUCY_SPACES_PROJECT_NAME={slug}\n"
            f"LUCY_SPACES_PROJECT_SECRET={project_secret}\n"
            f"CONVEX_DEPLOY_KEY={deploy_key}\n",
            encoding="utf-8",
        )

        config = SpaceProject(
            name=project_name,
            description=description,
            workspace_id=workspace_id,
            convex_project_id=convex_project_id,
            convex_deployment_name=deployment_name,
            convex_deployment_url=deployment_url,
            convex_deploy_key=deploy_key,
            vercel_project_id=vercel_project_id,
            subdomain=subdomain,
            project_secret=project_secret,
            vercel_project_name=vercel_project_name,
            vercel_bypass_secret=bypass_secret,
            created_at=datetime.now(UTC).isoformat(),
        )
        config.save(_project_config_path(workspace_id, slug))

        return {
            "success": True,
            "project_name": project_name,
            "slug": slug,
            "sandbox_path": str(project_dir),
            "convex_url": deployment_url,
            "subdomain": subdomain,
            "preview_url": f"https://{subdomain}",
        }

    except Exception as e:
        logger.error("spaces_init_failed", slug=slug, error=str(e))
        await _rollback()
        return {"success": False, "error": str(e)}


async def deploy_app(
    project_name: str,
    workspace_id: str,
    environment: str = "production",
) -> dict[str, Any]:
    """Build, deploy, wait for readiness, and validate a Lucy Spaces project."""
    slug = _slugify(project_name)
    config_path = _project_config_path(workspace_id, slug)

    if not config_path.exists():
        return {"success": False, "error": f"Project '{slug}' not found. Use the exact slug returned by lucy_spaces_init."}  # noqa: E501

    config = SpaceProject.load(config_path)
    project_dir = _project_dir(workspace_id, slug)

    app_tsx = project_dir / "src" / "App.tsx"
    if app_tsx.exists():
        app_content = app_tsx.read_text(encoding="utf-8")
        if "LUCY_SPACES_PLACEHOLDER" in app_content:
            return {
                "success": False,
                "error": (
                    "App.tsx has not been modified from the template. "
                    "You must write your app code to src/App.tsx before deploying."
                ),
            }

    try:
        build_result = await _build_project(project_dir)
        if not build_result["success"]:
            return build_result

        dist_dir = project_dir / "dist"
        if not dist_dir.exists():
            return {"success": False, "error": "Build completed but dist/ not found"}

        vercel = get_vercel_api()
        target = "production" if environment == "production" else "preview"
        deploy_name = config.vercel_project_name or f"lucy-{slug}"
        result = await vercel.deploy_directory(
            project_id=config.vercel_project_id,
            dist_dir=str(dist_dir),
            project_name=deploy_name,
            target=target,
        )

        deployment_id = result.get("id", "")
        deploy_url = result.get("url", "")

        ready_state = await _wait_for_deployment(vercel, deployment_id)
        if ready_state != "READY":
            error_msg = f"Deployment failed with state: {ready_state}"
            logger.error("deployment_not_ready", state=ready_state, id=deployment_id)
            return {"success": False, "error": error_msg}

        config.last_deployed_at = datetime.now(UTC).isoformat()
        config.vercel_deployment_url = deploy_url
        config.save(config_path)

        public_url = config.public_url()

        # Pass the bypass secret so validation works even when Vercel deployment
        # protection is enabled (without it, validation always gets HTTP 401).
        bypass_secret = getattr(config, "bypass_secret", None)
        validation = await _validate_deployment(public_url, bypass_secret=bypass_secret)
        if not validation["ok"]:
            logger.warning(
                "deployment_validation_failed",
                url=public_url,
                reason=validation.get("reason"),
            )
            return {
                "success": True,
                "url": public_url,
                "deployment_id": deployment_id,
                "environment": environment,
                "validation_warning": validation.get("reason", ""),
            }

        return {
            "success": True,
            "url": public_url,
            "deployment_id": deployment_id,
            "environment": environment,
            "validated": True,
        }

    except Exception as e:
        logger.error("spaces_deploy_failed", slug=slug, error=str(e))
        return {"success": False, "error": str(e)}


async def _wait_for_deployment(
    vercel: Any, deployment_id: str, max_wait: int = 180,
) -> str:
    """Poll Vercel until deployment reaches a terminal state."""
    import asyncio

    delay = 2.0
    max_delay = 10.0
    start_time = time.monotonic()
    consecutive_errors = 0
    last_error_msg = ""

    while (time.monotonic() - start_time) < max_wait:
        try:
            dep = await vercel.get_deployment(deployment_id)
            state = dep.get("readyState", "UNKNOWN")
            consecutive_errors = 0
            if state in ("READY", "ERROR", "CANCELED"):
                return state
        except Exception as exc:
            err_msg = str(exc)
            if err_msg == last_error_msg:
                consecutive_errors += 1
            else:
                consecutive_errors = 1
                last_error_msg = err_msg
            if consecutive_errors >= 3:
                logger.error(
                    "deployment_poll_repeated_error",
                    deployment_id=deployment_id,
                    error=err_msg,
                    consecutive=consecutive_errors,
                )
                return f"POLL_ERROR: {err_msg}"
        await asyncio.sleep(delay)
        delay = min(delay * 2, max_delay)

    return "TIMEOUT"


async def _validate_deployment(url: str, bypass_secret: str | None = None) -> dict[str, Any]:
    """Fetch the deployed URL and verify the app renders content.

    bypass_secret: Vercel deployment protection bypass secret. Required when
    Vercel has protection enabled — without it, validation always gets HTTP 401.
    """
    import httpx

    try:
        headers: dict[str, str] = {}
        if bypass_secret:
            headers["x-vercel-protection-bypass"] = bypass_secret
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                return {"ok": False, "reason": f"HTTP {resp.status_code}"}

            body = resp.text
            if len(body) < 100:
                return {"ok": False, "reason": "Response too small"}

            if "<script" not in body and "<div" not in body:
                return {"ok": False, "reason": "No HTML content detected"}

            return {"ok": True}

    except Exception as e:
        return {"ok": False, "reason": str(e)}


async def _build_project(project_dir: Path) -> dict[str, Any]:
    """Run bun install + vite build inside the project directory."""
    import asyncio

    bun_path = Path.home() / ".bun" / "bin" / "bun"
    bun = str(bun_path) if bun_path.exists() else "bun"

    path_env = os.environ.get("PATH", "")
    env = {**os.environ, "PATH": f"{bun_path.parent}:{path_env}"}

    build_step = "bun install"
    try:
        proc = await asyncio.create_subprocess_exec(
            bun, "install",
            cwd=str(project_dir),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        if proc.returncode != 0:
            err = stderr.decode(errors="replace")
            logger.error("bun_install_failed", error=err[:500])
            return {"success": False, "error": f"bun install failed: {err[:300]}"}

        logger.info("bun_install_complete", project=str(project_dir.name))

        build_step = "vite build"
        npx = str(bun_path.parent / "npx") if (bun_path.parent / "npx").exists() else "npx"
        proc2 = await asyncio.create_subprocess_exec(
            npx, "vite", "build",
            cwd=str(project_dir),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout2, stderr2 = await asyncio.wait_for(proc2.communicate(), timeout=120)
        if proc2.returncode != 0:
            err2 = stderr2.decode(errors="replace")
            logger.error("vite_build_failed", error=err2[:500])
            return {"success": False, "error": f"Build failed: {err2[:300]}"}

        logger.info("vite_build_complete", project=str(project_dir.name))
        return {"success": True}

    except TimeoutError:
        return {"success": False, "error": f"Build timed out during '{build_step}'"}


async def list_apps(workspace_id: str) -> dict[str, Any]:
    """List all Lucy Spaces projects in a workspace."""
    spaces_root = _spaces_dir(workspace_id)
    if not spaces_root.exists():
        return {"apps": [], "count": 0}

    apps: list[dict[str, Any]] = []
    for entry in sorted(spaces_root.iterdir()):
        config_path = entry / "project.json"
        if config_path.exists():
            try:
                config = SpaceProject.load(config_path)
                apps.append({
                    "name": config.name,
                    "slug": entry.name,
                    "url": config.public_url(),
                    "created_at": config.created_at,
                    "last_deployed": config.last_deployed_at,
                })
            except Exception as e:
                logger.warning("spaces_config_parse_failed", slug=entry.name, error=str(e))
                apps.append({
                    "name": entry.name,
                    "slug": entry.name,
                    "error": "corrupt config",
                })

    return {"apps": apps, "count": len(apps)}


async def get_app_status(
    project_name: str, workspace_id: str,
) -> dict[str, Any]:
    """Get detailed status for a Lucy Spaces project."""
    slug = _slugify(project_name)
    config_path = _project_config_path(workspace_id, slug)

    if not config_path.exists():
        return {"success": False, "error": f"Project '{slug}' not found"}

    config = SpaceProject.load(config_path)
    return {
        "success": True,
        "name": config.name,
        "slug": slug,
        "description": config.description,
        "url": config.public_url(),
        "convex_url": config.convex_deployment_url,
        "created_at": config.created_at,
        "last_deployed": config.last_deployed_at,
    }


async def delete_app_project(
    project_name: str, workspace_id: str,
) -> dict[str, Any]:
    """Delete a Lucy Spaces project (Convex + Vercel + local files)."""
    slug = _slugify(project_name)
    config_path = _project_config_path(workspace_id, slug)

    if not config_path.exists():
        return {"success": False, "error": f"Project '{slug}' not found"}

    config = SpaceProject.load(config_path)
    deleted: list[str] = []
    warnings: list[str] = []

    try:
        convex = get_convex_api()
        await convex.delete_project(config.convex_project_id)
        deleted.append(f"Convex project {config.convex_project_id}")
    except Exception as e:
        logger.warning("convex_delete_failed", error=str(e))
        warnings.append(f"Convex cleanup failed: {e}")

    try:
        vercel = get_vercel_api()
        await vercel.delete_project(config.vercel_project_id)
        deleted.append(f"Vercel project {config.vercel_project_id}")
    except Exception as e:
        logger.warning("vercel_delete_failed", error=str(e))
        warnings.append(f"Vercel cleanup failed: {e}")

    project_dir = _project_dir(workspace_id, slug)
    if project_dir.exists():
        shutil.rmtree(project_dir)
        deleted.append(f"Local files at {project_dir}")

    result: dict[str, Any] = {"success": True, "deleted": deleted}
    if warnings:
        result["warnings"] = warnings
    return result
