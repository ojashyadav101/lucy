"""Per-project configuration for Lucy Spaces.

Each space project stores its config as project.json in the workspace.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()


@dataclass
class SpaceProject:
    """Configuration for a single Lucy Spaces project."""

    name: str
    description: str
    workspace_id: str
    convex_project_id: int
    convex_deployment_name: str
    convex_deployment_url: str
    convex_deploy_key: str
    vercel_project_id: str
    subdomain: str
    project_secret: str
    created_at: str
    vercel_project_name: str = ""
    vercel_bypass_secret: str = ""
    last_deployed_at: str | None = None
    vercel_deployment_url: str | None = None

    def public_url(self) -> str:
        """Return the public URL using the custom domain (no auth needed)."""
        if self.subdomain:
            return f"https://{self.subdomain}"
        base = self.vercel_deployment_url or ""
        if not base.startswith("http"):
            base = f"https://{base}"
        if self.vercel_bypass_secret:
            sep = "&" if "?" in base else "?"
            return f"{base}{sep}x-vercel-protection-bypass={self.vercel_bypass_secret}"
        return base

    def save(self, path: Path) -> None:
        """Serialize to JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("space_project_saved", name=self.name, path=str(path))

    @classmethod
    def load(cls, path: Path) -> SpaceProject:
        """Deserialize from JSON file."""
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(**{
            k: v for k, v in data.items()
            if k in cls.__dataclass_fields__
        })
