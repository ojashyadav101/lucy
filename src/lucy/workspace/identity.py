"""Workspace identity — cached facts about connected services.

Stores verified identifiers (GSC sites, Polar org, domains) so the agent
knows its environment without querying on every turn.  Data is persisted
in ``workspace_identity.json`` and lazily refreshed when stale.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import structlog

from lucy.workspace.filesystem import WorkspaceFS

logger = structlog.get_logger()

_IDENTITY_FILE = "workspace_identity.json"
_STALENESS_SECONDS = 60 * 60 * 24  # re-probe after 24 h


async def read_identity(ws: WorkspaceFS) -> dict[str, Any]:
    """Load the cached workspace identity, returning ``{}`` if absent."""
    raw = await ws.read_file(_IDENTITY_FILE)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


async def write_identity(
    ws: WorkspaceFS, updates: dict[str, Any],
) -> dict[str, Any]:
    """Merge *updates* into the persisted identity and return the result."""
    current = await read_identity(ws)
    current.update(updates)
    current["_updated_at"] = time.time()
    await ws.write_file(_IDENTITY_FILE, json.dumps(current, indent=2))
    logger.info(
        "workspace_identity_updated",
        workspace_id=ws.workspace_id,
        keys=list(updates.keys()),
    )
    return current


async def ensure_identity(
    ws: WorkspaceFS,
    connected_services: list[str],
) -> dict[str, Any]:
    """Return workspace identity, probing connected services if stale.

    Called once per agent turn (from prompt builder).  Reads the cache,
    and if it's older than 24 h or missing keys for a connected service,
    kicks off lightweight probes in the background.
    """
    identity = await read_identity(ws)
    updated_at = identity.get("_updated_at", 0)
    is_stale = (time.time() - updated_at) > _STALENESS_SECONDS

    services_lower = {s.lower() for s in connected_services}
    needs_probe: list[str] = []

    if ("google search console" in services_lower
            or "googlesearchconsole" in services_lower
            or "gsc" in services_lower):
        if is_stale or "gsc_verified_sites" not in identity:
            needs_probe.append("gsc")

    if ("polar.sh" in services_lower
            or "polar" in services_lower
            or "polarsh" in services_lower):
        if is_stale or "polar_organization" not in identity:
            needs_probe.append("polar")

    if not needs_probe:
        return identity

    new_facts_found = False
    for service in needs_probe:
        try:
            facts = await _probe_service(service, ws)
            if facts:
                identity.update(facts)
                new_facts_found = True
        except Exception as exc:
            # Log at WARNING, not DEBUG — these are external service failures
            # and should be visible in production logs per project error policy.
            logger.warning(
                "identity_probe_failed",
                service=service,
                error=str(exc),
            )

    # Only write (and reset _updated_at) if probes actually returned new data.
    # Without this guard, a temporary service outage would freeze the cache for
    # 24 hours — the next run would see a fresh timestamp and skip probing again.
    if new_facts_found:
        identity = await write_identity(ws, identity)

    return identity


async def _probe_service(
    service: str, ws: WorkspaceFS,
) -> dict[str, Any]:
    """Call a connected service's identity endpoint and return key facts."""
    if service == "gsc":
        return await _probe_gsc(ws)
    if service == "polar":
        return await _probe_polar()
    return {}


async def _probe_gsc(ws: WorkspaceFS) -> dict[str, Any]:
    """Discover verified GSC sites via the REST API connected accounts.

    Composio connections don't store verified site URLs in metadata,
    so we look up the connected account ID and use it to query the
    Composio REST API for connection details.
    """
    try:
        import httpx

        from lucy.integrations.composio_client import get_composio_client

        client = get_composio_client()
        api_key = client.api_key

        entity_ids = [
            ws.workspace_id,
            f"slack_{ws.workspace_id}",
            "default",
        ]

        async with httpx.AsyncClient(
            timeout=10.0, follow_redirects=True,
        ) as http:
            for eid in entity_ids:
                resp = await http.get(
                    "https://backend.composio.dev/api/v1/connectedAccounts",
                    params={
                        "user_uuid": eid,
                        "showActiveOnly": "true",
                    },
                    headers={"x-api-key": api_key},
                )
                if resp.status_code != 200:
                    continue

                items = resp.json().get("items", [])
                for item in items:
                    app = (item.get("appName") or "").lower()
                    if "search_console" not in app:
                        continue

                    conn_id = item.get("id", "")
                    if not conn_id:
                        continue

                    detail_resp = await http.get(
                        f"https://backend.composio.dev/api/v1/"
                        f"connectedAccounts/{conn_id}",
                        headers={"x-api-key": api_key},
                    )
                    if detail_resp.status_code != 200:
                        continue

                    detail = detail_resp.json()
                    member = detail.get("member") or {}
                    identifier = (
                        member.get("identifier")
                        or detail.get("memberIdentifier")
                        or ""
                    )
                    if identifier:
                        return {"gsc_verified_sites": [identifier]}

                if items:
                    break

    except Exception as exc:
        logger.debug("gsc_probe_failed", error=str(exc))

    return {}


async def _probe_polar() -> dict[str, Any]:
    """Fetch Polar.sh org identity via the custom wrapper's API key."""
    try:
        import httpx

        from lucy.config import settings

        keys_path = Path(settings.workspace_root).parent / "keys.json"
        if not keys_path.exists():
            return {}

        import asyncio as _asyncio
        _raw = await _asyncio.to_thread(keys_path.read_text, encoding="utf-8")
        keys_data = json.loads(_raw)
        api_key = (
            keys_data
            .get("custom_integrations", {})
            .get("polarsh", {})
            .get("api_key", "")
        )
        if not api_key:
            return {}

        async with httpx.AsyncClient(
            timeout=10.0, follow_redirects=True,
        ) as http:
            resp = await http.get(
                "https://api.polar.sh/v1/organizations",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if resp.status_code != 200:
                return {}

            data = resp.json()
            items = data.get("items") or data.get("result", [])
            if not items:
                return {}

            org = items[0]
            return {
                "polar_organization": {
                    "slug": org.get("slug", ""),
                    "id": org.get("id", ""),
                    "name": org.get("name", ""),
                },
            }
    except Exception as exc:
        logger.debug("polar_probe_failed", error=str(exc))
        return {}


def format_identity_for_prompt(identity: dict[str, Any]) -> str | None:
    """Format workspace identity as a prompt block.

    Returns ``None`` when there's nothing meaningful to inject.
    """
    display = {k: v for k, v in identity.items() if not k.startswith("_")}
    if not display:
        return None
    return (
        "<workspace_identity>\n"
        + json.dumps(display, indent=2)
        + "\n</workspace_identity>"
    )
