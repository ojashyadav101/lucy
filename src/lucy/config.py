"""Centralized configuration via Pydantic Settings.

All values loaded from environment variables prefixed with LUCY_.
Sensitive credentials can also be loaded from keys.json.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import structlog
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


def _load_keys_json() -> dict:
    """Load credentials from keys.json if available."""
    keys_path = Path(__file__).parent.parent.parent / "keys.json"
    if keys_path.exists():
        try:
            with open(keys_path) as f:
                return json.load(f)
        except Exception as e:
            logger.warning("keys_json_load_failed", error=str(e))
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
    model_tier_default: str = "minimax/minimax-m2.5"
    model_tier_code: str = "minimax/minimax-m2.5"
    model_tier_research: str = "google/gemini-3-flash-preview"
    model_tier_document: str = "minimax/minimax-m2.5"
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

    # ── Agent limits ────────────────────────────────────────────
    agent_max_tool_turns: int = 50
    agent_max_context_messages: int = 80
    agent_tool_result_max_chars: int = 50_000
    agent_tool_result_summary_threshold: int = 24_000
    agent_max_payload_chars: int = 120_000
    agent_absolute_max_seconds: int = 14_400
    agent_silence_threshold_s: float = 480.0
    agent_wallclock_timeout_s: float = 1200.0

    # ── Slack handler limits ──────────────────────────────────
    handler_execution_timeout: int = 14400
    approved_action_timeout: int = 300
    max_concurrent_agents: int = 10
    event_dedup_ttl: float = 30.0

    # ── Sub-agent limits ──────────────────────────────────────
    subagent_max_turns: int = 20
    subagent_max_payload_chars: int = 200_000
    subagent_max_tool_result_chars: int = 32_000
    subagent_timeout_s: int = 600

    # ── Supervisor ────────────────────────────────────────────
    supervisor_check_interval_turns: int = 3
    supervisor_check_interval_s: float = 60.0

    # ── Connection watcher ────────────────────────────────────
    connection_poll_interval_s: int = 5
    connection_poll_max_duration_s: int = 600
    connection_max_concurrent_watches: int = 20

    # ── External service timeouts ─────────────────────────────
    composio_timeout_s: float = 60.0
    vercel_timeout_s: float = 60.0
    convex_timeout_s: float = 30.0
    camofox_request_timeout_s: float = 30.0
    camofox_navigate_timeout_s: float = 45.0
    clerk_api_timeout_s: float = 60.0
    polar_api_timeout_s: float = 60.0
    openclaw_gateway_timeout_s: float = 120.0

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


@dataclass(frozen=True)
class LLMPreset:
    """Named parameter preset for internal LLM calls."""

    temperature: float
    max_tokens: int


class LLMPresets:
    """Central registry of LLM parameter presets used for internal calls.

    Keeps temperature/max_tokens pairs out of individual files so they
    can be tuned in one place.
    """

    ACK = LLMPreset(temperature=0.9, max_tokens=80)
    SUPERVISOR = LLMPreset(temperature=0.2, max_tokens=1000)
    SUPERVISOR_TERSE = LLMPreset(temperature=0.1, max_tokens=200)
    HUMANIZE = LLMPreset(temperature=0.9, max_tokens=500)
    HUMANIZE_POOL = LLMPreset(temperature=0.9, max_tokens=8000)
    CLASSIFIER = LLMPreset(temperature=0.1, max_tokens=500)
    CODE_GEN = LLMPreset(temperature=0.2, max_tokens=16384)
    SEARCH = LLMPreset(temperature=0.1, max_tokens=8192)
    DEAI_REWRITE = LLMPreset(temperature=0.4, max_tokens=16384)
    SUBAGENT = LLMPreset(temperature=0.4, max_tokens=16384)
