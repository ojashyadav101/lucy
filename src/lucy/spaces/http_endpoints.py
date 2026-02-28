"""HTTP endpoints for Lucy Spaces.

Two endpoints called by deployed Convex backends:
1. /api/lucy-spaces/send-email  — OTP emails via AgentMail
2. /api/lucy-spaces/tools/call  — Tool gateway (AI search, image gen, etc.)
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from lucy.config import settings

logger = structlog.get_logger()

router = APIRouter(prefix="/api/lucy-spaces", tags=["spaces"])


class SendEmailRequest(BaseModel):
    project_name: str
    project_secret: str
    to_email: str
    subject: str
    html_content: str
    text_content: str = ""
    email_type: str = "otp"


class ToolCallRequest(BaseModel):
    project_name: str
    project_secret: str
    role: str
    arguments: dict[str, Any] = {}


def _verify_project_secret(project_name: str, project_secret: str) -> bool:
    """Verify a project's secret against stored config."""
    import json
    from pathlib import Path

    for ws_dir in settings.workspace_root.iterdir():
        config_path = ws_dir / "spaces" / project_name / "project.json"
        if config_path.exists():
            try:
                data = json.loads(config_path.read_text())
                return data.get("project_secret") == project_secret
            except Exception:
                pass
    return False


@router.post("/send-email")
async def send_email(req: SendEmailRequest) -> dict[str, Any]:
    """Send an OTP email via AgentMail on behalf of a Spaces project."""
    if not _verify_project_secret(req.project_name, req.project_secret):
        raise HTTPException(status_code=403, detail="Invalid project credentials")

    if not settings.agentmail_enabled or not settings.agentmail_api_key:
        raise HTTPException(status_code=503, detail="Email service not configured")

    try:
        from lucy.integrations.agentmail_client import get_email_client

        client = get_email_client()
        await client.send_email(
            to=[req.to_email],
            subject=req.subject,
            text=req.text_content or req.subject,
            html=req.html_content,
        )

        logger.info(
            "spaces_otp_sent",
            project=req.project_name,
            to=req.to_email,
            email_type=req.email_type,
        )
        return {"success": True}

    except Exception as e:
        logger.error(
            "spaces_email_failed",
            project=req.project_name,
            error=str(e),
        )
        return {"success": False, "error": str(e)}


@router.post("/tools/call")
async def tool_call(req: ToolCallRequest) -> dict[str, Any]:
    """Execute a tool on behalf of a Spaces project.

    Supports: quick_ai_search, text2im, file_to_markdown.
    """
    if not _verify_project_secret(req.project_name, req.project_secret):
        raise HTTPException(status_code=403, detail="Invalid project credentials")

    try:
        if req.role == "quick_ai_search":
            result = await _tool_ai_search(req.arguments)
        elif req.role == "text2im":
            result = await _tool_image_gen(req.arguments)
        else:
            return {"success": False, "error": f"Unknown tool: {req.role}"}

        return {"success": True, "result": result}

    except Exception as e:
        logger.error(
            "spaces_tool_failed",
            project=req.project_name,
            tool=req.role,
            error=str(e),
        )
        return {"success": False, "error": str(e)}


async def _tool_ai_search(args: dict[str, Any]) -> dict[str, Any]:
    """Proxy to OpenRouter for quick AI-powered web search."""
    import httpx

    query = args.get("search_question", "")
    if not query:
        return {"search_response": "No query provided."}

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{settings.openrouter_base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.model_tier_fast,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a helpful search assistant. Provide a concise, "
                            "accurate answer to the user's query."
                        ),
                    },
                    {"role": "user", "content": query},
                ],
                "max_tokens": 1000,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        answer = data["choices"][0]["message"]["content"]
        return {"search_response": answer}


async def _tool_image_gen(args: dict[str, Any]) -> dict[str, Any]:
    """Proxy to OpenRouter for image generation."""
    prompt = args.get("prompt", "")
    if not prompt:
        return {"response_text": "No prompt provided."}

    return {
        "response_text": (
            f"Image generation requested: {prompt}. "
            "Image generation via Spaces is a coming-soon feature."
        ),
    }
