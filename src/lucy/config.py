"""Centralized configuration via Pydantic Settings.

All values loaded from environment variables prefixed with LUCY_.
Sensitive credentials can also be loaded from keys.json.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _load_keys_json() -> dict:
    """Load credentials from keys.json if available."""
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
    slack_bot_token: str = ""
    slack_app_token: str = ""
    slack_signing_secret: str = ""

    # LLM via OpenRouter (all requests go through OpenRouter)
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openclaw_model: str = "minimax/minimax-m2.5"
    openclaw_read_timeout: float = 120.0

    # OpenClaw Gateway (available for sandbox/memory, not used for chat)
    openclaw_base_url: str = "http://167.86.82.46:18791"
    openclaw_api_key: str = ""

    # CamoFox browser
    camofox_url: str = "http://localhost:9377"

    # Database
    database_url: str = "postgresql+asyncpg://lucy:lucy@localhost:5432/lucy"

    # Composio
    composio_api_key: str = ""

    # Workspace filesystem
    workspace_root: Path = Path("./workspaces")

    # Application
    log_level: str = "INFO"
    env: str = "development"

    def model_post_init(self, __context: object) -> None:
        """Post-initialization: load from keys.json if env vars not set."""
        keys = _load_keys_json()

        if not self.openclaw_api_key:
            oc_key = keys.get("openclaw_lucy", {}).get("gateway_token")
            if oc_key:
                self.openclaw_api_key = oc_key

        if not self.openrouter_api_key:
            or_key = keys.get("openclaw_lucy", {}).get("openrouter_api_key")
            if or_key:
                self.openrouter_api_key = or_key

        if not self.composio_api_key:
            comp_key = keys.get("composio", {}).get("api_key")
            if comp_key:
                self.composio_api_key = comp_key

        if not self.workspace_root.is_absolute():
            self.workspace_root = Path(__file__).parent.parent.parent / self.workspace_root


settings = Settings()  # type: ignore[call-arg]
