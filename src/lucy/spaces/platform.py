"""Lucy Spaces platform orchestrator.

Coordinates Convex, Vercel, and the template to manage the full
lifecycle of a space project: init, deploy, list, status, delete.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
from datetime import datetime, timezone
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
    4. Create Vercel project + domain
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
        convex_project_id = convex_result["projectId"]

        deployments = await convex.list_deployments(convex_project_id)
        if deployments:
            dev_dep = deployments[0]
        else:
            dev_dep = await convex.create_deployment(convex_project_id, "dev")

        deployment_name = dev_dep["name"]
        deployment_url = dev_dep.get("deploymentUrl", "")

        deploy_key_result = await convex.create_deploy_key(deployment_name)
        deploy_key = deploy_key_result.get("deployKey", "")

        vercel = get_vercel_api()
        vercel_result = await vercel.create_project(f"lucy-{slug}-{short_hash}")
        vercel_project_id = vercel_result["id"]

        await vercel.add_domain(vercel_project_id, subdomain)

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
            created_at=datetime.now(timezone.utc).isoformat(),
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
        if project_dir.exists():
            shutil.rmtree(project_dir, ignore_errors=True)
        return {"success": False, "error": str(e)}


async def deploy_app(
    project_name: str,
    workspace_id: str,
    environment: str = "preview",
) -> dict[str, Any]:
    """Build and deploy a Lucy Spaces project.

    Automatically runs bun install + vite build before uploading.
    No manual build step required.
    """
    slug = _slugify(project_name)
    config_path = _project_config_path(workspace_id, slug)

    if not config_path.exists():
        return {"success": False, "error": f"Project '{slug}' not found"}

    config = SpaceProject.load(config_path)
    project_dir = _project_dir(workspace_id, slug)

    try:
        build_result = await _build_project(project_dir)
        if not build_result["success"]:
            return build_result

        dist_dir = project_dir / "dist"
        if not dist_dir.exists():
            return {"success": False, "error": "Build completed but dist/ not found"}

        vercel = get_vercel_api()
        target = "production" if environment == "production" else "preview"
        result = await vercel.deploy_directory(
            project_id=config.vercel_project_id,
            dist_dir=str(dist_dir),
            project_name=f"lucy-{slug}",
            target=target,
        )

        config.last_deployed_at = datetime.now(timezone.utc).isoformat()
        config.vercel_deployment_url = result.get("url")
        config.save(config_path)

        custom_url = f"https://{config.subdomain}"
        vercel_url = result.get("url", "")
        if vercel_url and not vercel_url.startswith("http"):
            vercel_url = f"https://{vercel_url}"

        return {
            "success": True,
            "url": vercel_url or custom_url,
            "custom_domain": custom_url,
            "deployment_id": result.get("id"),
            "environment": environment,
        }

    except Exception as e:
        logger.error("spaces_deploy_failed", slug=slug, error=str(e))
        return {"success": False, "error": str(e)}


async def _build_project(project_dir: Path) -> dict[str, Any]:
    """Run bun install + vite build inside the project directory."""
    import asyncio

    bun_path = Path.home() / ".bun" / "bin" / "bun"
    bun = str(bun_path) if bun_path.exists() else "bun"

    env = {**os.environ, "PATH": f"{bun_path.parent}:{os.environ.get('PATH', '')}"}

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

    except asyncio.TimeoutError:
        return {"success": False, "error": "Build timed out (120s)"}


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
                    "url": f"https://{config.subdomain}",
                    "created_at": config.created_at,
                    "last_deployed": config.last_deployed_at,
                })
            except Exception:
                apps.append({"name": entry.name, "slug": entry.name, "error": "corrupt config"})

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
        "url": f"https://{config.subdomain}",
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

    try:
        convex = get_convex_api()
        await convex.delete_project(config.convex_project_id)
        deleted.append(f"Convex project {config.convex_project_id}")
    except Exception as e:
        logger.warning("convex_delete_failed", error=str(e))

    try:
        vercel = get_vercel_api()
        await vercel.delete_project(config.vercel_project_id)
        deleted.append(f"Vercel project {config.vercel_project_id}")
    except Exception as e:
        logger.warning("vercel_delete_failed", error=str(e))

    project_dir = _project_dir(workspace_id, slug)
    if project_dir.exists():
        shutil.rmtree(project_dir)
        deleted.append(f"Local files at {project_dir}")

    return {"success": True, "deleted": deleted}
