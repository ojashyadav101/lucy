"""Filesystem-based workspace for a Slack workspace.

Each workspace gets a directory tree:
    {root}/{workspace_id}/
    ├── company/SKILL.md
    ├── team/SKILL.md
    ├── skills/
    ├── crons/
    ├── scripts/
    ├── data/
    ├── logs/
    └── state.json
"""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiofiles
import aiofiles.os
import structlog

logger = structlog.get_logger()

WORKSPACE_DIRS = [
    "company",
    "team",
    "skills",
    "crons",
    "scripts",
    "data",
    "logs",
]


class WorkspaceFS:
    """Manages the persistent workspace directory for a single Slack workspace."""

    def __init__(self, workspace_id: str, base_path: Path) -> None:
        self.workspace_id = workspace_id
        self.root = base_path / workspace_id

    @property
    def exists(self) -> bool:
        return self.root.is_dir()

    async def ensure_structure(self) -> None:
        """Create the standard directory tree if it doesn't exist."""
        for d in WORKSPACE_DIRS:
            dir_path = self.root / d
            dir_path.mkdir(parents=True, exist_ok=True)

        state_path = self.root / "state.json"
        if not state_path.exists():
            await self.write_file(
                "state.json",
                json.dumps(
                    {
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "workspace_id": self.workspace_id,
                    },
                    indent=2,
                ),
            )

        logger.info(
            "workspace_structure_ensured",
            workspace_id=self.workspace_id,
            root=str(self.root),
        )

    async def read_file(self, relative_path: str) -> str | None:
        """Read a file from the workspace. Returns None if not found."""
        path = self._resolve(relative_path)
        if not path.is_file():
            return None
        async with aiofiles.open(path, mode="r", encoding="utf-8") as f:
            return await f.read()

    async def write_file(self, relative_path: str, content: str) -> Path:
        """Write content to a file atomically (write tmp → rename)."""
        path = self._resolve(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        tmp_path = path.with_suffix(path.suffix + ".tmp")
        async with aiofiles.open(tmp_path, mode="w", encoding="utf-8") as f:
            await f.write(content)
        tmp_path.rename(path)
        return path

    async def append_file(self, relative_path: str, content: str) -> Path:
        """Append content to a file, creating it if needed."""
        path = self._resolve(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(path, mode="a", encoding="utf-8") as f:
            await f.write(content)
        return path

    async def delete_file(self, relative_path: str) -> bool:
        """Delete a file. Returns True if deleted, False if not found."""
        path = self._resolve(relative_path)
        if path.is_file():
            await aiofiles.os.remove(path)
            return True
        return False

    async def list_dir(self, relative_path: str = ".") -> list[str]:
        """List entries in a directory, returning relative paths from workspace root."""
        path = self._resolve(relative_path)
        if not path.is_dir():
            return []
        entries = []
        for entry in sorted(path.iterdir()):
            rel = entry.relative_to(self.root)
            suffix = "/" if entry.is_dir() else ""
            entries.append(str(rel) + suffix)
        return entries

    async def search(self, query: str, directory: str = ".") -> list[dict[str, Any]]:
        """Plain-text search (grep -rn) across workspace files.

        Returns list of {path, line_number, line} matches.
        """
        search_path = self._resolve(directory)
        if not search_path.is_dir():
            return []

        proc = await asyncio.create_subprocess_exec(
            "grep", "-rn", "--include=*.md", "--include=*.json",
            "--include=*.py", "--include=*.txt", "--include=*.yaml",
            "-i", query, str(search_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()

        results: list[dict[str, Any]] = []
        for line in stdout.decode("utf-8", errors="replace").splitlines():
            parts = line.split(":", 2)
            if len(parts) >= 3:
                file_path = parts[0]
                try:
                    rel = str(Path(file_path).relative_to(self.root))
                except ValueError:
                    rel = file_path
                results.append({
                    "path": rel,
                    "line_number": int(parts[1]) if parts[1].isdigit() else 0,
                    "line": parts[2],
                })
        return results

    async def copy_seeds(
        self, seeds_dir: Path, target_subdir: str = "",
    ) -> int:
        """Copy seed files into the workspace, preserving directory structure.

        Args:
            seeds_dir: Source directory containing seed files.
            target_subdir: Subdirectory within workspace to copy into
                           (e.g. "skills", "crons"). Empty string means root.

        Returns the number of files copied.
        """
        if not seeds_dir.is_dir():
            logger.warning("seeds_dir_not_found", path=str(seeds_dir))
            return 0

        dest_base = self.root / target_subdir if target_subdir else self.root
        count = 0
        for src_file in seeds_dir.rglob("*"):
            if src_file.is_file() and src_file.name != ".gitkeep":
                rel = src_file.relative_to(seeds_dir)
                dest = dest_base / rel
                if not dest.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_file, dest)
                    count += 1

        logger.info(
            "seeds_copied",
            workspace_id=self.workspace_id,
            count=count,
            source=str(seeds_dir),
            target=target_subdir or "(root)",
        )
        return count

    async def read_state(self) -> dict[str, Any]:
        """Read state.json."""
        content = await self.read_file("state.json")
        if content:
            return json.loads(content)
        return {}

    async def update_state(self, updates: dict[str, Any]) -> None:
        """Merge updates into state.json."""
        state = await self.read_state()
        state.update(updates)
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        await self.write_file("state.json", json.dumps(state, indent=2))

    def _resolve(self, relative_path: str) -> Path:
        """Resolve a relative path within the workspace, preventing traversal."""
        resolved = (self.root / relative_path).resolve()
        if not str(resolved).startswith(str(self.root.resolve())):
            raise ValueError(f"Path traversal denied: {relative_path}")
        return resolved


def get_workspace(workspace_id: str, base_path: Path | None = None) -> WorkspaceFS:
    """Create a WorkspaceFS instance."""
    from lucy.config import settings

    return WorkspaceFS(
        workspace_id=workspace_id,
        base_path=base_path or settings.workspace_root,
    )
