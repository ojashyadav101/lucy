"""Centralized configuration via Pydantic Settings.

All values loaded from environment variables prefixed with LUCY_.
Additional keys loaded from keys.json for sensitive credentials.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _load_keys_json() -> dict:
    """Load credentials from keys.json if available."""
    # Path: src/lucy/config.py -> go up 3 levels to repo root
    keys_path = Path(__file__).parent.parent.parent / "keys.json"
    if keys_path.exists():
        try:
            with open(keys_path) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LUCY_", env_file=".env", extra="ignore")

    # Slack
    slack_bot_token: str
    slack_app_token: str
    slack_signing_secret: str

    # OpenClaw
    openclaw_base_url: str = "http://localhost:3000"
    openclaw_api_key: str = ""

    # Database
    database_url: str = "postgresql+asyncpg://lucy:lucy@localhost:5432/lucy"

    # Memory — Qdrant
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""

    # Memory — Mem0
    mem0_api_key: str = ""

    # Embeddings provider selection: "openai" or "openrouter"
    embedding_provider: str = "openrouter"

    # OpenAI (for embeddings, if embedding_provider=openai)
    openai_api_key: str = ""

    # OpenRouter (for embeddings, if embedding_provider=openrouter)
    # Falls back to keys.json -> openclaw_lucy -> openrouter_api_key if not in env
    openrouter_api_key: str = ""

    # Model Routing — LiteLLM
    litellm_base_url: str = "http://localhost:4000"
    litellm_api_key: str = ""

    # Composio
    composio_api_key: str = ""

    # E2B Sandbox
    e2b_api_key: str = ""

    # Security
    llamafirewall_enabled: bool = True

    # Observability — Langfuse
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # Application
    log_level: str = "INFO"
    env: str = "development"

    def model_post_init(self, __context: object) -> None:
        """Post-initialization: load from keys.json if env vars not set."""
        keys = _load_keys_json()

        # Load OpenRouter key from keys.json if not set via env
        if not self.openrouter_api_key:
            or_key = keys.get("openclaw_lucy", {}).get("openrouter_api_key")
            if or_key:
                self.openrouter_api_key = or_key

        # Load OpenAI key from keys.json if not set via env (for OpenClaw VPS)
        if not self.openai_api_key:
            # OpenClaw VPS stores OpenAI key for its internal use
            # Not in keys.json currently, but could be added
            pass


settings = Settings()  # type: ignore[call-arg]
