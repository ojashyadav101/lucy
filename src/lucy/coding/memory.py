"""Coding memory: persistent preferences and lessons per workspace.

Stores user coding preferences, project patterns, and lessons learned
from past coding sessions. Loaded into the CodingEngine prompt to provide
context-aware code generation.

Inspired by Windsurf's create_memory and Qoder's four-category memory.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from lucy.config import settings

logger = structlog.get_logger()

MAX_PREFERENCES = 50
MAX_LESSONS = 100
MAX_MEMORY_PROMPT_CHARS = 2000


@dataclass
class CodingMemory:
    """Coding-specific memory for a workspace."""

    user_preferences: dict[str, str] = field(default_factory=dict)
    project_patterns: dict[str, str] = field(default_factory=dict)
    lessons_learned: list[str] = field(default_factory=list)
    branding: dict[str, str] = field(default_factory=dict)

    def add_preference(self, key: str, value: str) -> None:
        """Record a user coding preference (e.g. 'ui_framework': 'tailwind')."""
        if len(self.user_preferences) >= MAX_PREFERENCES:
            oldest = next(iter(self.user_preferences))
            del self.user_preferences[oldest]
        self.user_preferences[key] = value

    def add_pattern(self, key: str, value: str) -> None:
        """Record a project pattern (e.g. 'db': 'supabase')."""
        if len(self.project_patterns) >= MAX_PREFERENCES:
            oldest = next(iter(self.project_patterns))
            del self.project_patterns[oldest]
        self.project_patterns[key] = value

    def add_lesson(self, lesson: str) -> None:
        """Record a lesson learned from a coding session."""
        if lesson in self.lessons_learned:
            return
        if len(self.lessons_learned) >= MAX_LESSONS:
            self.lessons_learned.pop(0)
        self.lessons_learned.append(lesson)

    def add_branding(self, key: str, value: str) -> None:
        """Record a branding value (e.g. 'primary_color': '#3B82F6')."""
        if len(self.branding) >= MAX_PREFERENCES and key not in self.branding:
            oldest = next(iter(self.branding))
            del self.branding[oldest]
        self.branding[key] = value

    def to_prompt_section(self) -> str:
        """Format memory as a prompt section for injection."""
        parts: list[str] = []

        if self.branding:
            brand = ", ".join(
                f"{k}: {v}" for k, v in self.branding.items()
            )
            parts.append(f"Company branding: {brand}")

        if self.user_preferences:
            prefs = ", ".join(
                f"{k}: {v}" for k, v in self.user_preferences.items()
            )
            parts.append(f"User preferences: {prefs}")

        if self.project_patterns:
            patterns = ", ".join(
                f"{k}: {v}" for k, v in self.project_patterns.items()
            )
            parts.append(f"Project patterns: {patterns}")

        if self.lessons_learned:
            recent = self.lessons_learned[-10:]
            lessons = "; ".join(recent)
            parts.append(f"Lessons: {lessons}")

        result = "\n".join(parts)
        return result[:MAX_MEMORY_PROMPT_CHARS]

    def is_empty(self) -> bool:
        return (
            not self.user_preferences
            and not self.project_patterns
            and not self.lessons_learned
            and not self.branding
        )


def _memory_path(workspace_id: str) -> Path:
    return settings.workspace_root / workspace_id / "coding_memory.json"


def load_coding_memory(workspace_id: str) -> CodingMemory:
    """Load coding memory from the workspace filesystem."""
    path = _memory_path(workspace_id)
    if not path.exists():
        return CodingMemory()

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return CodingMemory(
            user_preferences=data.get("user_preferences", {}),
            project_patterns=data.get("project_patterns", {}),
            lessons_learned=data.get("lessons_learned", []),
            branding=data.get("branding", {}),
        )
    except Exception as e:
        logger.warning("coding_memory_load_failed", error=str(e))
        return CodingMemory()


def save_coding_memory(workspace_id: str, memory: CodingMemory) -> None:
    """Persist coding memory to the workspace filesystem."""
    path = _memory_path(workspace_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    data: dict[str, Any] = {
        "user_preferences": memory.user_preferences,
        "project_patterns": memory.project_patterns,
        "lessons_learned": memory.lessons_learned,
        "branding": memory.branding,
    }

    try:
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info(
            "coding_memory_saved",
            workspace_id=workspace_id,
            preferences=len(memory.user_preferences),
            patterns=len(memory.project_patterns),
            lessons=len(memory.lessons_learned),
            branding=len(memory.branding),
        )
    except Exception as e:
        logger.error("coding_memory_save_failed", error=str(e))
