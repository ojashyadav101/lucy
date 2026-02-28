"""Convex Management API wrapper for Lucy Spaces.

Handles project lifecycle: create, deploy, query, and delete Convex projects
via the Management API at https://api.convex.dev/v1.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from lucy.config import settings

logger = structlog.get_logger()

_BASE = "https://api.convex.dev/v1"
_TIMEOUT = settings.convex_timeout_s

_client: ConvexAPI | None = None


class ConvexAPI:
    """Async client for the Convex Management API."""

    def __init__(self, team_token: str, team_id: str) -> None:
        self._token = team_token
        self._team_id = team_id

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    async def create_project(self, project_name: str) -> dict[str, Any]:
        """Create a new Convex project with a dev deployment."""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{_BASE}/teams/{self._team_id}/create_project",
                headers=self._headers(),
                json={
                    "projectName": project_name,
                    "deploymentType": "dev",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            logger.info(
                "convex_project_created",
                project_id=data.get("projectId"),
                name=project_name,
            )
            return data

    async def list_projects(self) -> list[dict[str, Any]]:
        """List all projects for the team."""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{_BASE}/teams/{self._team_id}/list_projects",
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()

    async def create_deployment(
        self, project_id: int, deploy_type: str = "dev",
    ) -> dict[str, Any]:
        """Create a deployment (dev or prod) for a project."""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{_BASE}/projects/{project_id}/create_deployment",
                headers=self._headers(),
                json={"type": deploy_type},
            )
            resp.raise_for_status()
            data = resp.json()
            logger.info(
                "convex_deployment_created",
                deployment_name=data.get("name"),
                deploy_type=deploy_type,
                url=data.get("deploymentUrl"),
            )
            return data

    async def create_deploy_key(
        self, deployment_name: str, key_name: str = "lucy-spaces",
    ) -> dict[str, Any]:
        """Create a deploy key for CLI authentication."""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{_BASE}/deployments/{deployment_name}/create_deploy_key",
                headers=self._headers(),
                json={"name": key_name},
            )
            resp.raise_for_status()
            data = resp.json()
            logger.info(
                "convex_deploy_key_created",
                deployment_name=deployment_name,
            )
            return data

    async def get_deployment(self, deployment_name: str) -> dict[str, Any]:
        """Get deployment details."""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{_BASE}/deployments/{deployment_name}",
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()

    async def list_deployments(self, project_id: int) -> list[dict[str, Any]]:
        """List all deployments for a project."""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{_BASE}/projects/{project_id}/list_deployments",
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()

    async def delete_project(self, project_id: int) -> None:
        """Delete a project and all its deployments."""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{_BASE}/projects/{project_id}/delete",
                headers=self._headers(),
                json={},
            )
            resp.raise_for_status()
            logger.info("convex_project_deleted", project_id=project_id)


def get_convex_api() -> ConvexAPI:
    """Return the singleton ConvexAPI client."""
    global _client
    if _client is not None:
        return _client
    if not settings.convex_team_token:
        raise RuntimeError("Convex team token not configured")
    _client = ConvexAPI(
        team_token=settings.convex_team_token,
        team_id=settings.convex_team_id,
    )
    return _client
