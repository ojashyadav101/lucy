"""OpenClaw HTTP client for Lucy.

Connects to the OpenClaw gateway on your VPS (167.86.82.46:18791)
and provides methods for chat completions and memory access.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, AsyncIterator
from uuid import UUID

import httpx
import structlog

from lucy.config import settings

logger = structlog.get_logger()


@dataclass
class ChatConfig:
    """Configuration for an OpenClaw chat completion."""

    model: str = "kimi"
    temperature: float = 0.7
    max_tokens: int = 4096
    system_prompt: str | None = None
    tools: list[dict[str, Any]] | None = None


@dataclass
class OpenClawResponse:
    """Response from OpenClaw chat completion."""

    content: str
    tool_calls: list[dict[str, Any]] | None = None
    usage: dict[str, int] | None = None
    engrams_added: list[dict[str, Any]] | None = None
    error: str | None = None


class OpenClawError(Exception):
    """OpenClaw API error."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class OpenClawClient:
    """HTTP client for OpenClaw gateway using OpenAI-compatible endpoints."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None):
        """Initialize client."""
        self.base_url = (base_url or settings.openclaw_base_url).rstrip("/")
        self.api_key = api_key or settings.openclaw_api_key
        
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(15.0, connect=5.0, read=30.0, write=5.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        
        logger.info(
            "openclaw_client_initialized",
            base_url=self.base_url,
        )

    async def close(self) -> None:
        """Close HTTP client."""
        await self._client.aclose()
        logger.info("openclaw_client_closed")

    async def health_check(self) -> dict[str, Any]:
        """Check OpenClaw gateway health."""
        try:
            # The root path / returns the control UI HTML, which means the server is up.
            response = await self._client.get("/")
            response.raise_for_status()
            return {"status": "ok", "message": "Gateway is reachable"}
        except httpx.HTTPStatusError as e:
            logger.error(
                "health_check_failed",
                status_code=e.response.status_code,
                error=str(e),
            )
            raise OpenClawError(
                f"Health check failed: {e.response.status_code}",
                status_code=e.response.status_code,
            )
        except Exception as e:
            logger.error("health_check_error", error=str(e))
            raise OpenClawError(f"Health check error: {e}")

    async def chat_completion(
        self,
        messages: list[dict[str, Any]],
        config: ChatConfig | None = None,
        workspace_id: UUID | None = None,
        user_id: UUID | None = None,
    ) -> OpenClawResponse:
        """Send a chat completion request to OpenClaw.

        Args:
            messages: List of message dicts (role, content)
            config: Chat configuration
            workspace_id: Optional workspace ID for context tracking
            user_id: Optional user ID for context tracking

        Returns:
            OpenClawResponse with content and metadata.
        """
        config = config or ChatConfig()
        
        # Prepare messages array. Ensure system prompt is first if provided.
        final_messages = []
        if config.system_prompt:
            final_messages.append({"role": "system", "content": config.system_prompt})
        else:
            soul = self._load_soul()
            if soul:
                final_messages.append({"role": "system", "content": soul})
                
        final_messages.extend(messages)
        
        payload = {
            "model": config.model,
            "messages": final_messages,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
        }
        
        # Include tools if provided (OpenAI-compatible function calling)
        if config.tools:
            payload["tools"] = config.tools
            # Explicitly tell the model it can use tools
            payload["tool_choice"] = "auto"
        
        # Optionally pass metadata in headers if OpenClaw supports it
        headers = {}
        if workspace_id:
            headers["x-workspace-id"] = str(workspace_id)
        if user_id:
            headers["x-user-id"] = str(user_id)
            
        try:
            logger.info(
                "chat_completion_request",
                model=config.model,
                message_count=len(final_messages),
                has_tools=bool(config.tools),
                tool_count=len(config.tools) if config.tools else 0,
            )
            
            response = await self._client.post(
                "/v1/chat/completions",
                json=payload,
                headers=headers
            )
            response.raise_for_status()
            data = response.json()
            
            # OpenAI response format
            choices = data.get("choices", [])
            content = ""
            tool_calls = None
            
            if choices:
                message = choices[0].get("message", {})
                content = message.get("content", "")
                # Extract tool calls from response
                raw_tool_calls = message.get("tool_calls")
                if raw_tool_calls:
                    import json
                    tool_calls = []
                    for tc in raw_tool_calls:
                        args = tc.get("function", {}).get("arguments", "{}")
                        parse_error = None
                        # Parse JSON string arguments if needed
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except json.JSONDecodeError:
                                parse_error = "invalid_json_arguments"
                                args = {}
                        tool_calls.append({
                            "id": tc.get("id"),
                            "name": tc.get("function", {}).get("name"),
                            "parameters": args,
                            "parse_error": parse_error,
                        })
            
            usage = data.get("usage")
            
            result = OpenClawResponse(
                content=content,
                tool_calls=tool_calls,
                usage=usage,
            )
            
            logger.info(
                "chat_completion_success",
                content_length=len(result.content),
                has_tool_calls=bool(result.tool_calls),
                tool_call_count=len(result.tool_calls) if result.tool_calls else 0,
            )
            
            return result
            
        except httpx.HTTPStatusError as e:
            logger.error(
                "chat_completion_failed",
                status_code=e.response.status_code,
                response=e.response.text,
            )
            raise OpenClawError(
                f"Failed chat completion: {e.response.status_code} - {e.response.text}",
                status_code=e.response.status_code,
            )
        except Exception as e:
            logger.error("chat_completion_error", error=str(e))
            raise OpenClawError(f"Chat completion error: {e}")

    def _load_soul(self) -> str:
        """Load SOUL.md for system prompt."""
        return load_soul()


def load_soul() -> str:
    """Load SOUL.md system prompt (standalone function)."""
    try:
        import pathlib
        # Path: src/lucy/core/openclaw.py -> project root -> assets/SOUL.md
        soul_path = pathlib.Path(__file__).parent.parent.parent.parent / "assets" / "SOUL.md"
        if soul_path.exists():
            return soul_path.read_text()
    except Exception as e:
        logger.warning("failed_to_load_soul", error=str(e))
    return (
        "You are Lucy, an AI coworker. You are direct, helpful, and get things done. "
        "You work inside Slack and have access to various tools and integrations."
    )


# Singleton instance for application use
_client: OpenClawClient | None = None


async def get_openclaw_client() -> OpenClawClient:
    """Get or create singleton OpenClaw client."""
    global _client
    if _client is None:
        _client = OpenClawClient()
    return _client


async def close_openclaw_client() -> None:
    """Close singleton OpenClaw client."""
    global _client
    if _client is not None:
        await _client.close()
        _client = None
