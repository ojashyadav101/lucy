"""MCP server manager (Stage 1).

Discovers, installs, and runs MCP servers on the OpenClaw VPS
using the Gateway's exec/process tools.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from lucy.integrations.grounded_search import IntegrationClassification
from lucy.integrations.openclaw_gateway import OpenClawGatewayError, get_gateway_client

logger = structlog.get_logger()

_MCP_INSTALL_DIR = "/home/lucy-oclaw/mcp-servers"


@dataclass
class MCPInstallResult:
    """Outcome of an MCP server install attempt."""

    success: bool = False
    service_name: str = ""
    session_id: str | None = None
    install_log: str = ""
    error: str | None = None
    needs_api_key: bool = False
    api_key_env_var: str | None = None


async def install_mcp_server(classification: IntegrationClassification) -> MCPInstallResult:
    """Install and start an MCP server on the VPS.

    Steps:
      1. Ensure the install directory exists.
      2. Install the MCP package (npm or pip).
      3. Start the server as a background process.
    """
    service = classification.service_name
    repo_url = classification.mcp_repo_url or ""

    if not repo_url:
        return MCPInstallResult(
            service_name=service,
            error="No MCP repo URL provided by grounded search",
        )

    try:
        gw = await get_gateway_client()
    except Exception as e:
        return MCPInstallResult(service_name=service, error=f"Gateway unavailable: {e}")

    # Determine install strategy from the repo URL / package name
    is_npm = (
        repo_url.startswith("@")
        or "npmjs.com" in repo_url
        or not repo_url.startswith("http")
    )

    slug = service.lower().replace(" ", "-").replace("_", "-")

    try:
        # 1. Create install directory
        await gw.exec_command(f"mkdir -p {_MCP_INSTALL_DIR}/{slug}")

        # 2. Install package
        if is_npm:
            package_name = repo_url if not repo_url.startswith("http") else repo_url
            install_result = await gw.exec_command(
                f"cd {_MCP_INSTALL_DIR}/{slug} && npm init -y 2>/dev/null; "
                f"npm install {package_name}",
                timeout=120,
            )
        else:
            install_result = await gw.exec_command(
                f"cd {_MCP_INSTALL_DIR}/{slug} && "
                f"git clone --depth 1 {repo_url} . 2>/dev/null || true && "
                f"if [ -f package.json ]; then npm install; "
                f"elif [ -f requirements.txt ]; then pip install -r requirements.txt; "
                f"elif [ -f pyproject.toml ]; then pip install .; fi",
                timeout=180,
            )

        install_log = _extract_output(install_result)

        logger.info(
            "mcp_server_installed",
            service=service,
            slug=slug,
            is_npm=is_npm,
        )

        # 3. Try to start the server as a background process
        start_cmd = _build_start_command(slug, is_npm, repo_url)
        session_id = await gw.start_background(
            start_cmd,
            workdir=f"{_MCP_INSTALL_DIR}/{slug}",
        )

        needs_key = classification.auth_method in ("api_key", "bearer_token", "oauth2")
        env_var = f"{slug.upper().replace('-', '_')}_API_KEY" if needs_key else None

        return MCPInstallResult(
            success=True,
            service_name=service,
            session_id=session_id,
            install_log=install_log[:500],
            needs_api_key=needs_key,
            api_key_env_var=env_var,
        )

    except OpenClawGatewayError as e:
        logger.error("mcp_install_failed", service=service, error=str(e))
        return MCPInstallResult(service_name=service, error=str(e))
    except Exception as e:
        logger.error("mcp_install_unexpected_error", service=service, error=str(e))
        return MCPInstallResult(service_name=service, error=f"Unexpected: {e}")


async def stop_mcp_server(session_id: str) -> bool:
    """Stop a running MCP server by its background session ID."""
    try:
        gw = await get_gateway_client()
        await gw.kill_process(session_id)
        return True
    except Exception as e:
        logger.warning("mcp_stop_failed", session_id=session_id, error=str(e))
        return False


async def list_running_mcp_servers() -> list[dict[str, Any]]:
    """List MCP server processes currently running on the VPS."""
    try:
        gw = await get_gateway_client()
        processes = await gw.list_processes()
        return [
            p for p in processes
            if isinstance(p, dict) and "mcp" in str(p.get("command", "")).lower()
        ]
    except Exception:
        return []


def _build_start_command(slug: str, is_npm: bool, repo_url: str) -> str:
    """Build the shell command to start the MCP server."""
    if is_npm:
        package_name = repo_url if not repo_url.startswith("http") else slug
        bin_name = package_name.split("/")[-1] if "/" in package_name else package_name
        return f"cd {_MCP_INSTALL_DIR}/{slug} && npx {bin_name} 2>&1"
    return (
        f"cd {_MCP_INSTALL_DIR}/{slug} && "
        f"if [ -f index.js ]; then node index.js; "
        f"elif [ -f main.py ]; then python3 main.py; "
        f"elif [ -f server.py ]; then python3 server.py; "
        f"else echo 'No entry point found'; fi 2>&1"
    )


def _extract_output(result: dict[str, Any]) -> str:
    """Pull human-readable output from an exec result."""
    if isinstance(result, dict):
        return result.get("output", result.get("stdout", str(result)))[:1000]
    return str(result)[:1000]
