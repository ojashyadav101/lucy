"""Vercel REST API wrapper for Lucy Spaces.

Handles git-less deployments: create project, upload files, deploy,
and manage custom domains on zeeya.app.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import httpx
import structlog

from lucy.config import settings

logger = structlog.get_logger()

_BASE = "https://api.vercel.com"
_TIMEOUT = 60.0

_client: VercelAPI | None = None


class VercelAPI:
    """Async client for the Vercel REST API."""

    def __init__(self, token: str, team_id: str = "") -> None:
        self._token = token
        self._team_id = team_id

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def _team_params(self) -> dict[str, str]:
        if self._team_id:
            return {"teamId": self._team_id}
        return {}

    async def create_project(self, name: str) -> dict[str, Any]:
        """Create a new Vercel project configured for Vite."""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{_BASE}/v10/projects",
                headers={**self._headers(), "Content-Type": "application/json"},
                params=self._team_params(),
                json={"name": name, "framework": "vite"},
            )
            if resp.status_code >= 400:
                logger.error("vercel_api_error", status=resp.status_code, body=resp.text)
            resp.raise_for_status()
            data = resp.json()
            logger.info(
                "vercel_project_created",
                project_id=data.get("id"),
                name=name,
            )
            return data

    async def add_domain(
        self, project_id: str, domain: str,
    ) -> dict[str, Any]:
        """Add a custom domain to a Vercel project."""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{_BASE}/v10/projects/{project_id}/domains",
                headers={**self._headers(), "Content-Type": "application/json"},
                params=self._team_params(),
                json={"name": domain},
            )
            if resp.status_code >= 400:
                logger.error("vercel_api_error", status=resp.status_code, body=resp.text)
            resp.raise_for_status()
            data = resp.json()
            logger.info(
                "vercel_domain_added",
                domain=domain,
                verified=data.get("verified"),
            )
            return data

    async def deploy_directory(
        self,
        project_id: str,
        dist_dir: str,
        project_name: str,
        target: str = "production",
    ) -> dict[str, Any]:
        """Upload a built dist/ directory and create a deployment."""
        dist_path = Path(dist_dir)
        if not dist_path.is_dir():
            raise FileNotFoundError(f"Build directory not found: {dist_dir}")

        file_entries: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            for file_path in dist_path.rglob("*"):
                if not file_path.is_file():
                    continue

                content = file_path.read_bytes()
                sha1 = hashlib.sha1(content).hexdigest()
                relative = str(file_path.relative_to(dist_path))

                resp = await client.post(
                    f"{_BASE}/v2/files",
                    headers={
                        **self._headers(),
                        "Content-Type": "application/octet-stream",
                        "x-vercel-digest": sha1,
                    },
                    params=self._team_params(),
                    content=content,
                )
                if resp.status_code not in (200, 409):
                    logger.error("vercel_file_upload_error", status=resp.status_code, body=resp.text)
                    resp.raise_for_status()

                file_entries.append({
                    "file": relative,
                    "sha": sha1,
                    "size": len(content),
                })

            logger.info(
                "vercel_files_uploaded",
                count=len(file_entries),
                project=project_name,
            )

            deploy_body: dict[str, Any] = {
                "name": project_name,
                "files": file_entries,
                "projectSettings": {"framework": "vite"},
            }
            if target == "production":
                deploy_body["target"] = "production"

            resp = await client.post(
                f"{_BASE}/v13/deployments",
                headers={
                    **self._headers(),
                    "Content-Type": "application/json",
                },
                params=self._team_params(),
                json=deploy_body,
            )
            if resp.status_code >= 400:
                logger.error("vercel_deploy_error", status=resp.status_code, body=resp.text)
            resp.raise_for_status()
            data = resp.json()
            logger.info(
                "vercel_deployment_created",
                deployment_id=data.get("id"),
                url=data.get("url"),
                target=target,
            )
            return data

    async def get_deployment(self, deployment_id: str) -> dict[str, Any]:
        """Get deployment status and details."""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{_BASE}/v13/deployments/{deployment_id}",
                headers=self._headers(),
                params=self._team_params(),
            )
            resp.raise_for_status()
            return resp.json()

    async def delete_project(self, project_id: str) -> None:
        """Delete a Vercel project."""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.delete(
                f"{_BASE}/v9/projects/{project_id}",
                headers=self._headers(),
                params=self._team_params(),
            )
            resp.raise_for_status()
            logger.info("vercel_project_deleted", project_id=project_id)


def get_vercel_api() -> VercelAPI:
    """Return the singleton VercelAPI client."""
    global _client
    if _client is not None:
        return _client
    if not settings.vercel_token:
        raise RuntimeError("Vercel token not configured")
    _client = VercelAPI(
        token=settings.vercel_token,
        team_id=settings.vercel_team_id,
    )
    return _client
