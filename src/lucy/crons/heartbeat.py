"""Heartbeat monitoring service — condition-based alerting.

Unlike cron jobs (time-based, periodic), heartbeats are condition-based
monitors that check for specific states and alert immediately when
conditions are met. They support:

- api_health: Check if a URL returns a healthy HTTP status
- page_content: Check if a page contains or lacks specific text
- metric_threshold: Compare a numeric value against a threshold
- custom: Run a Python script that returns JSON with a "triggered" key

Heartbeats run on a shared evaluation loop (every 30s) rather than
individual APScheduler jobs. This keeps overhead minimal even with
hundreds of monitors — each evaluation is a lightweight HTTP check
or script execution, not an LLM call.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import httpx
import structlog

from lucy.config import settings

logger = structlog.get_logger()

_EVAL_INTERVAL_SECONDS = 30
_HTTP_TIMEOUT = 15.0
_DEFAULT_CHECK_INTERVAL_S = 300
_DEFAULT_ALERT_COOLDOWN_S = 3600
_MIN_CHECK_INTERVAL_S = 30
_DEFAULT_EXPECTED_STATUS = 200
_SCRIPT_TIMEOUT_S = 30.0
_CONSECUTIVE_FAILURES_ERROR_THRESHOLD = 3
_consecutive_failures: dict[str, int] = {}


async def create_heartbeat(
    workspace_id: str,
    name: str,
    condition_type: str,
    condition_config: dict[str, Any],
    check_interval_seconds: int = _DEFAULT_CHECK_INTERVAL_S,
    alert_channel_id: str | None = None,
    alert_template: str = "Condition triggered: {name}",
    alert_cooldown_seconds: int = _DEFAULT_ALERT_COOLDOWN_S,
    description: str | None = None,
) -> dict[str, Any]:
    """Create a new heartbeat monitor in the database.

    ``alert_channel_id`` accepts either a Slack channel ID string
    (e.g. ``C01234567``) or a DB UUID.  Slack IDs are stored inside
    ``condition_config["_slack_alert_channel"]`` so no schema migration
    is needed.
    """
    from lucy.db import AsyncSessionLocal
    from lucy.db.models import Heartbeat, HeartbeatStatus

    valid_types = {"api_health", "page_content", "metric_threshold", "custom"}
    if condition_type not in valid_types:
        return {"error": f"Invalid condition_type '{condition_type}'. Must be one of: {', '.join(sorted(valid_types))}"}

    if check_interval_seconds < _MIN_CHECK_INTERVAL_S:
        check_interval_seconds = _MIN_CHECK_INTERVAL_S

    config_with_channel = dict(condition_config)
    if alert_channel_id:
        config_with_channel["_slack_alert_channel"] = alert_channel_id

    async with AsyncSessionLocal() as session:
        hb = Heartbeat(
            workspace_id=UUID(workspace_id) if isinstance(workspace_id, str) else workspace_id,
            name=name,
            description=description,
            condition_type=condition_type,
            condition_config=config_with_channel,
            check_interval_seconds=check_interval_seconds,
            alert_template=alert_template,
            alert_cooldown_seconds=alert_cooldown_seconds,
            is_active=True,
            current_status=HeartbeatStatus.HEALTHY,
        )

        session.add(hb)
        await session.commit()
        await session.refresh(hb)

        logger.info(
            "heartbeat_created",
            heartbeat_id=str(hb.id),
            name=name,
            condition_type=condition_type,
            interval_s=check_interval_seconds,
            workspace_id=workspace_id,
        )

        return {
            "heartbeat_id": str(hb.id),
            "name": hb.name,
            "condition_type": hb.condition_type,
            "check_interval_seconds": hb.check_interval_seconds,
            "status": "created",
            "message": (
                f"Heartbeat monitor '{name}' created. "
                f"Checking every {check_interval_seconds}s. "
                f"Will alert when condition is triggered."
            ),
        }


async def delete_heartbeat(
    workspace_id: str,
    name: str,
) -> dict[str, Any]:
    """Delete a heartbeat monitor by name."""
    from sqlalchemy import select

    from lucy.db import AsyncSessionLocal
    from lucy.db.models import Heartbeat

    async with AsyncSessionLocal() as session:
        ws_uuid = UUID(workspace_id) if isinstance(workspace_id, str) else workspace_id
        stmt = select(Heartbeat).where(
            Heartbeat.workspace_id == ws_uuid,
            Heartbeat.name == name,
        )
        result = await session.execute(stmt)
        hb = result.scalar_one_or_none()

        if not hb:
            name_lower = name.lower().replace("-", " ").replace("_", " ")
            stmt_fuzzy = select(Heartbeat).where(
                Heartbeat.workspace_id == ws_uuid,
                Heartbeat.is_active.is_(True),
            )
            result_fuzzy = await session.execute(stmt_fuzzy)
            all_hbs = result_fuzzy.scalars().all()
            for candidate in all_hbs:
                cname = candidate.name.lower().replace("-", " ").replace("_", " ")
                if cname == name_lower or name_lower in cname:
                    hb = candidate
                    break

        if not hb:
            return {"error": f"No heartbeat monitor named '{name}' found."}

        await session.delete(hb)
        await session.commit()

        logger.info(
            "heartbeat_deleted",
            name=name,
            workspace_id=workspace_id,
        )
        return {"status": "deleted", "name": name}


async def list_heartbeats(
    workspace_id: str,
) -> list[dict[str, Any]]:
    """List all heartbeat monitors for a workspace."""
    from sqlalchemy import select

    from lucy.db import AsyncSessionLocal
    from lucy.db.models import Heartbeat

    async with AsyncSessionLocal() as session:
        ws_uuid = UUID(workspace_id) if isinstance(workspace_id, str) else workspace_id
        stmt = select(Heartbeat).where(
            Heartbeat.workspace_id == ws_uuid,
        ).order_by(Heartbeat.created_at.desc())
        result = await session.execute(stmt)
        heartbeats = result.scalars().all()

        return [
            {
                "name": hb.name,
                "description": hb.description,
                "condition_type": hb.condition_type,
                "condition_config": hb.condition_config,
                "check_interval_seconds": hb.check_interval_seconds,
                "is_active": hb.is_active,
                "current_status": hb.current_status.value if hb.current_status else "unknown",
                "check_count": hb.check_count,
                "trigger_count": hb.trigger_count,
                "last_check_at": hb.last_check_at.isoformat() if hb.last_check_at else None,
                "created_at": hb.created_at.isoformat() if hb.created_at else None,
            }
            for hb in heartbeats
        ]


# ═══════════════════════════════════════════════════════════════════════════
# CONDITION EVALUATORS
# ═══════════════════════════════════════════════════════════════════════════

async def _eval_api_health(config: dict[str, Any]) -> dict[str, Any]:
    """Check if a URL returns a healthy HTTP status.

    Config:
        url: str — the URL to check
        expected_status: int — expected HTTP status (default 200)
        timeout: float — request timeout in seconds (default 15)
    """
    url = config.get("url", "")
    expected = config.get("expected_status", _DEFAULT_EXPECTED_STATUS)
    timeout = config.get("timeout", _HTTP_TIMEOUT)

    if not url:
        return {"triggered": False, "error": "No URL configured"}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, follow_redirects=True)
            triggered = resp.status_code != expected
            return {
                "triggered": triggered,
                "status_code": resp.status_code,
                "expected": expected,
                "response_time_ms": round(resp.elapsed.total_seconds() * 1000),
                "detail": (
                    f"URL returned {resp.status_code} (expected {expected})"
                    if triggered
                    else f"URL healthy: {resp.status_code}"
                ),
            }
    except httpx.TimeoutException:
        return {
            "triggered": True,
            "error": "timeout",
            "detail": f"URL did not respond within {timeout}s",
        }
    except Exception as exc:
        return {
            "triggered": True,
            "error": str(exc),
            "detail": f"Connection failed: {type(exc).__name__}",
        }


async def _eval_page_content(config: dict[str, Any]) -> dict[str, Any]:
    """Check if a page contains or lacks specific text.

    Config:
        url: str — the URL to fetch
        contains: str | None — text that MUST be present (trigger if absent)
        not_contains: str | None — text that must NOT be present (trigger if found)
        regex: str | None — regex pattern to match (trigger if matched)
    """
    url = config.get("url", "")
    contains = config.get("contains")
    not_contains = config.get("not_contains")
    regex_pattern = config.get("regex")

    if not url:
        return {"triggered": False, "error": "No URL configured"}

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(url, follow_redirects=True)
            body = resp.text

        if contains and contains not in body:
            return {
                "triggered": True,
                "detail": f"Expected text '{contains[:50]}' not found on page",
                "status_code": resp.status_code,
            }

        if not_contains and not_contains in body:
            return {
                "triggered": True,
                "detail": f"Unwanted text '{not_contains[:50]}' found on page",
                "status_code": resp.status_code,
            }

        if regex_pattern:
            match = re.search(regex_pattern, body, re.IGNORECASE)
            if match:
                return {
                    "triggered": True,
                    "detail": f"Pattern matched: '{match.group()[:60]}'",
                    "status_code": resp.status_code,
                }

        return {
            "triggered": False,
            "detail": "No conditions triggered",
            "status_code": resp.status_code,
            "content_length": len(body),
        }
    except Exception as exc:
        return {
            "triggered": True,
            "error": str(exc),
            "detail": f"Failed to fetch page: {type(exc).__name__}",
        }


async def _eval_metric_threshold(config: dict[str, Any]) -> dict[str, Any]:
    """Check a numeric value against a threshold.

    Config:
        url: str — API endpoint returning JSON
        json_path: str — dot-separated path to the numeric value (e.g. "data.count")
        operator: str — comparison operator: ">", "<", ">=", "<=", "==", "!="
        threshold: float — the threshold value
    """
    url = config.get("url", "")
    json_path = config.get("json_path", "")
    operator = config.get("operator", ">")
    threshold = config.get("threshold", 0)

    if not url or not json_path:
        return {"triggered": False, "error": "url and json_path are required"}

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(url, follow_redirects=True)
            data = resp.json()

        value = data
        for key in json_path.split("."):
            if isinstance(value, dict):
                value = value.get(key)
            elif isinstance(value, list) and key.isdigit():
                value = value[int(key)]
            else:
                return {"triggered": False, "error": f"Cannot traverse path '{json_path}' in response"}

        if value is None:
            return {"triggered": False, "error": f"Value at '{json_path}' is null"}

        value = float(value)
        threshold = float(threshold)

        ops = {
            ">": value > threshold,
            "<": value < threshold,
            ">=": value >= threshold,
            "<=": value <= threshold,
            "==": value == threshold,
            "!=": value != threshold,
        }
        triggered = ops.get(operator, False)

        return {
            "triggered": triggered,
            "value": value,
            "threshold": threshold,
            "operator": operator,
            "detail": f"Value {value} {operator} {threshold} = {triggered}",
        }
    except Exception as exc:
        return {
            "triggered": True,
            "error": str(exc),
            "detail": f"Metric check failed: {type(exc).__name__}",
        }


async def _eval_custom(config: dict[str, Any]) -> dict[str, Any]:
    """Run a custom Python script that returns JSON with a 'triggered' key.

    Config:
        script_path: str — path to the Python script (relative to workspace root)
        workspace_id: str — workspace ID for context
    """
    import os

    script_path = config.get("script_path", "")
    workspace_id = config.get("workspace_id", "")

    if not script_path:
        return {"triggered": False, "error": "No script_path configured"}

    full_path = settings.workspace_root / workspace_id / script_path
    if not full_path.exists():
        return {"triggered": False, "error": f"Script not found: {script_path}"}

    try:
        process = await asyncio.create_subprocess_exec(
            "python3", str(full_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={
                **os.environ,
                "WORKSPACE_ID": workspace_id,
                "WORKSPACE_ROOT": str(settings.workspace_root / workspace_id),
            },
        )
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=_SCRIPT_TIMEOUT_S,
        )

        if process.returncode != 0:
            return {
                "triggered": True,
                "error": f"Script exited with code {process.returncode}",
                "stderr": stderr.decode()[:200] if stderr else "",
            }

        output = stdout.decode().strip()
        try:
            result = json.loads(output)
            if not isinstance(result, dict):
                result = {"triggered": bool(result)}
            if "triggered" not in result:
                result["triggered"] = False
            return result
        except json.JSONDecodeError:
            return {
                "triggered": process.returncode != 0,
                "raw_output": output[:200],
            }
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return {"triggered": True, "error": "Script timed out after 30s"}
    except Exception as exc:
        return {"triggered": True, "error": str(exc)}


_EVALUATORS: dict[str, Any] = {
    "api_health": _eval_api_health,
    "page_content": _eval_page_content,
    "metric_threshold": _eval_metric_threshold,
    "custom": _eval_custom,
}


# ═══════════════════════════════════════════════════════════════════════════
# EVALUATION LOOP
# ═══════════════════════════════════════════════════════════════════════════

async def evaluate_due_heartbeats(slack_client: Any) -> int:
    """Check all heartbeats that are due for evaluation.

    Returns the number of heartbeats evaluated.
    """
    from sqlalchemy import select, update

    from lucy.db import AsyncSessionLocal
    from lucy.db.models import Heartbeat, HeartbeatStatus

    now = datetime.now(timezone.utc)
    evaluated = 0

    async with AsyncSessionLocal() as session:
        stmt = select(Heartbeat).where(
            Heartbeat.is_active.is_(True),
            Heartbeat.current_status.in_([
                HeartbeatStatus.HEALTHY,
                HeartbeatStatus.TRIGGERED,
            ]),
        )
        result = await session.execute(stmt)
        heartbeats = result.scalars().all()

        for hb in heartbeats:
            if hb.last_check_at:
                elapsed = (now - hb.last_check_at).total_seconds()
                if elapsed < hb.check_interval_seconds:
                    continue

            evaluator = _EVALUATORS.get(hb.condition_type)
            if not evaluator:
                logger.warning(
                    "heartbeat_unknown_condition",
                    name=hb.name,
                    condition_type=hb.condition_type,
                )
                continue

            try:
                config = dict(hb.condition_config or {})
                if hb.condition_type == "custom":
                    config["workspace_id"] = str(hb.workspace_id)

                check_result = await evaluator(config)
                triggered = check_result.get("triggered", False)

                hb.check_count = (hb.check_count or 0) + 1
                hb.last_check_at = now
                hb.last_check_result = check_result

                if triggered:
                    should_alert = True

                    if hb.last_alert_at:
                        since_last_alert = (now - hb.last_alert_at).total_seconds()
                        if since_last_alert < hb.alert_cooldown_seconds:
                            should_alert = False

                    if should_alert:
                        hb.current_status = HeartbeatStatus.TRIGGERED
                        hb.trigger_count = (hb.trigger_count or 0) + 1
                        hb.last_alert_at = now

                        await _send_alert(
                            hb, check_result, slack_client,
                        )
                else:
                    _consecutive_failures.pop(str(hb.id), None)
                    if hb.current_status == HeartbeatStatus.TRIGGERED:
                        hb.current_status = HeartbeatStatus.HEALTHY

                evaluated += 1

                logger.debug(
                    "heartbeat_evaluated",
                    name=hb.name,
                    triggered=triggered,
                    check_count=hb.check_count,
                )

            except Exception as exc:
                hb_key = str(hb.id)
                _consecutive_failures[hb_key] = _consecutive_failures.get(hb_key, 0) + 1
                fail_count = _consecutive_failures[hb_key]
                log_level = "error" if fail_count >= _CONSECUTIVE_FAILURES_ERROR_THRESHOLD else "warning"
                getattr(logger, log_level)(
                    "heartbeat_eval_error",
                    name=hb.name,
                    error=str(exc),
                    consecutive_failures=fail_count,
                )

        if evaluated:
            await session.commit()

    return evaluated


async def _send_alert(
    hb: Any,
    check_result: dict[str, Any],
    slack_client: Any,
) -> None:
    """Post an alert to Slack when a heartbeat condition fires."""
    if not slack_client:
        logger.warning("heartbeat_alert_no_client", name=hb.name)
        return

    detail = check_result.get("detail", "Condition triggered")
    template = hb.alert_template or "Condition triggered: {name}"
    try:
        message = template.format(
            name=hb.name,
            detail=detail,
            **{k: v for k, v in check_result.items() if isinstance(v, (str, int, float))},
        )
    except (KeyError, IndexError):
        message = f"Monitor alert: {hb.name} — {detail}"

    config = hb.condition_config or {}
    channel: str | None = config.get("_slack_alert_channel")

    if not channel and hb.alert_channel_id:
        from sqlalchemy import select as _select

        from lucy.db import AsyncSessionLocal as _ASL
        from lucy.db.models import Channel

        async with _ASL() as _sess:
            stmt_ch = _select(Channel).where(Channel.id == hb.alert_channel_id)
            result_ch = await _sess.execute(stmt_ch)
            ch_record = result_ch.scalar_one_or_none()
            if ch_record and hasattr(ch_record, "slack_id"):
                channel = ch_record.slack_id

    if not channel:
        from sqlalchemy import select

        from lucy.db import AsyncSessionLocal
        from lucy.db.models import Workspace

        async with AsyncSessionLocal() as session:
            stmt = select(Workspace).where(Workspace.id == hb.workspace_id)
            result = await session.execute(stmt)
            ws = result.scalar_one_or_none()
            if ws and hasattr(ws, "default_channel_id") and ws.default_channel_id:
                channel = str(ws.default_channel_id)

    if not channel:
        logger.warning("heartbeat_alert_no_channel", name=hb.name)
        return

    try:
        await slack_client.chat_postMessage(
            channel=channel,
            text=f"🚨 *Monitor Alert: {hb.name}*\n{message}",
        )
        logger.info(
            "heartbeat_alert_sent",
            name=hb.name,
            channel=channel,
            detail=detail[:100],
        )
    except Exception as exc:
        logger.warning(
            "heartbeat_alert_failed",
            name=hb.name,
            error=str(exc),
        )
