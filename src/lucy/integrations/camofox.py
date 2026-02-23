"""CamoFox browser integration client.

Provides an async interface to the CamoFox anti-detection browser server.
CamoFox uses Camoufox (stealth Firefox) with C++-level anti-detection,
making it invisible to standard bot-detection systems.

Architecture:
    Lucy ──httpx──▶ CamoFox REST API (:9377)
                        │
                    Camoufox engine (persistent per-user profiles)
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from lucy.config import settings

logger = structlog.get_logger()

_REQUEST_TIMEOUT = 30.0
_NAVIGATE_TIMEOUT = 45.0


class CamoFoxError(Exception):
    """Raised when a CamoFox API call fails."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"CamoFox error {status_code}: {detail}")


class CamoFoxClient:
    """Async client for the CamoFox browser REST API."""

    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = (base_url or settings.camofox_url).rstrip("/")

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            timeout=_REQUEST_TIMEOUT,
        )

    async def _request(
        self,
        method: str,
        path: str,
        json_body: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        async with self._client() as client:
            resp = await client.request(
                method,
                path,
                json=json_body,
                timeout=timeout or _REQUEST_TIMEOUT,
            )

        if resp.status_code >= 400:
            detail = resp.text[:500]
            logger.warning(
                "camofox_api_error",
                status=resp.status_code,
                path=path,
                detail=detail,
            )
            raise CamoFoxError(resp.status_code, detail)

        if resp.headers.get("content-type", "").startswith("image/"):
            return {"screenshot": True, "content_type": resp.headers["content-type"]}

        try:
            return resp.json()
        except Exception:
            return {"text": resp.text}

    # ── Tab Management ────────────────────────────────────────────────

    async def create_tab(self, user_id: str | None = None) -> str:
        """Create a new browser tab. Returns the tab_id."""
        body: dict[str, Any] = {}
        if user_id:
            body["userId"] = user_id

        result = await self._request("POST", "/tabs", json_body=body)
        tab_id = result.get("id") or result.get("tab_id") or result.get("tabId", "")
        logger.info("camofox_tab_created", tab_id=tab_id, user_id=user_id)
        return str(tab_id)

    async def list_tabs(self) -> list[dict[str, Any]]:
        """List all open browser tabs."""
        result = await self._request("GET", "/tabs")
        return result.get("tabs", []) if isinstance(result, dict) else result

    async def close_tab(self, tab_id: str) -> None:
        """Close a browser tab."""
        await self._request("DELETE", f"/tabs/{tab_id}")
        logger.info("camofox_tab_closed", tab_id=tab_id)

    # ── Navigation ────────────────────────────────────────────────────

    async def navigate(self, tab_id: str, url: str) -> dict[str, Any]:
        """Navigate a tab to a URL (supports @search macros)."""
        return await self._request(
            "POST",
            f"/tabs/{tab_id}/navigate",
            json_body={"url": url},
            timeout=_NAVIGATE_TIMEOUT,
        )

    async def go_back(self, tab_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/tabs/{tab_id}/go_back")

    async def go_forward(self, tab_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/tabs/{tab_id}/go_forward")

    async def reload(self, tab_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/tabs/{tab_id}/reload")

    # ── Snapshot (Read Page) ──────────────────────────────────────────

    async def snapshot(self, tab_id: str) -> dict[str, Any]:
        """Get accessibility snapshot with eN element references."""
        return await self._request("GET", f"/tabs/{tab_id}/snapshot")

    # ── Interaction ───────────────────────────────────────────────────

    async def click(self, tab_id: str, ref: str) -> dict[str, Any]:
        return await self._request(
            "POST", f"/tabs/{tab_id}/click", json_body={"ref": ref},
        )

    async def type_text(self, tab_id: str, ref: str, text: str) -> dict[str, Any]:
        return await self._request(
            "POST", f"/tabs/{tab_id}/type", json_body={"ref": ref, "text": text},
        )

    async def fill(self, tab_id: str, ref: str, text: str) -> dict[str, Any]:
        return await self._request(
            "POST", f"/tabs/{tab_id}/fill", json_body={"ref": ref, "text": text},
        )

    async def press_key(self, tab_id: str, ref: str, key: str) -> dict[str, Any]:
        return await self._request(
            "POST", f"/tabs/{tab_id}/press", json_body={"ref": ref, "key": key},
        )

    async def select_option(self, tab_id: str, ref: str, value: str) -> dict[str, Any]:
        return await self._request(
            "POST", f"/tabs/{tab_id}/select", json_body={"ref": ref, "value": value},
        )

    async def scroll(
        self, tab_id: str, ref: str, direction: str = "down",
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/tabs/{tab_id}/scroll",
            json_body={"ref": ref, "direction": direction},
        )

    async def hover(self, tab_id: str, ref: str) -> dict[str, Any]:
        return await self._request(
            "POST", f"/tabs/{tab_id}/hover", json_body={"ref": ref},
        )

    # ── Screenshot ────────────────────────────────────────────────────

    async def screenshot(self, tab_id: str) -> bytes:
        """Take a full-page screenshot. Returns PNG bytes."""
        async with self._client() as client:
            resp = await client.get(
                f"/tabs/{tab_id}/screenshot",
                timeout=_REQUEST_TIMEOUT,
            )
        if resp.status_code >= 400:
            raise CamoFoxError(resp.status_code, resp.text[:500])
        return resp.content

    # ── Health ────────────────────────────────────────────────────────

    async def is_healthy(self) -> bool:
        """Check if CamoFox server is reachable."""
        try:
            await self._request("GET", "/tabs")
            return True
        except Exception:
            return False


_client: CamoFoxClient | None = None


def get_camofox_client() -> CamoFoxClient:
    """Get or create the singleton CamoFox client."""
    global _client
    if _client is None:
        _client = CamoFoxClient()
    return _client
