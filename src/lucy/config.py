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
    openclaw_model: str = "google/gemini-2.5-flash"
    openclaw_read_timeout: float = 120.0

    # Model tiers for dynamic routing
    model_tier_fast: str = "google/gemini-2.5-flash"
    model_tier_default: str = "moonshotai/kimi-k2.5"
    model_tier_code: str = "minimax/minimax-m2.5"
    model_tier_research: str = "google/gemini-3-flash-preview"
    model_tier_document: str = "moonshotai/kimi-k2.5"
    model_tier_frontier: str = "google/gemini-3.1-pro-preview"

    # OpenClaw Gateway (available for sandbox/memory, not used for chat)
    openclaw_base_url: str = "http://167.86.82.46:18791"
    openclaw_api_key: str = ""

    # CamoFox browser
    camofox_url: str = "http://localhost:9377"

    # Database
    database_url: str = "postgresql+asyncpg://lucy:lucy@localhost:5432/lucy"

    # Composio
    composio_api_key: str = ""

    # AgentMail (native email identity)
    agentmail_api_key: str = ""
    agentmail_domain: str = "zeeyamail.com"
    agentmail_enabled: bool = True

    # Workspace filesystem
    workspace_root: Path = Path("./workspaces")

    # Lucy Spaces
    convex_team_token: str = ""
    convex_team_id: str = ""
    vercel_token: str = ""
    vercel_team_id: str = ""
    spaces_domain: str = "zeeya.app"
    spaces_enabled: bool = True

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

        if not self.agentmail_api_key:
            am_key = keys.get("agentmail", {}).get("api_key")
            if am_key:
                self.agentmail_api_key = am_key

        if not self.convex_team_token:
            cx = keys.get("convex", {})
            if cx.get("team_token"):
                self.convex_team_token = cx["team_token"]
            if cx.get("team_id"):
                self.convex_team_id = cx["team_id"]

        if not self.vercel_token:
            vl = keys.get("vercel", {})
            if vl.get("token"):
                self.vercel_token = vl["token"]
            if vl.get("team_id"):
                self.vercel_team_id = vl["team_id"]

        if not self.workspace_root.is_absolute():
            self.workspace_root = Path(__file__).parent.parent.parent / self.workspace_root


settings = Settings()  # type: ignore[call-arg]
