"""CronScheduler — APScheduler-based proactivity engine.

Loads task.json files from each workspace's crons/ directory and
schedules them as recurring jobs. Each cron triggers a fresh Lucy
agent run with the task description as the instruction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from lucy.config import settings
from lucy.workspace.filesystem import WorkspaceFS, get_workspace

logger = structlog.get_logger()


@dataclass
class CronConfig:
    """Parsed cron from a task.json file."""

    path: str
    cron: str
    title: str
    description: str
    workspace_dir: str
    created_at: str = ""
    updated_at: str = ""

    @property
    def job_id(self) -> str:
        return f"{self.workspace_dir}:{self.path}"


class CronScheduler:
    """Discovers and schedules workspace crons via APScheduler."""

    def __init__(self, slack_client: Any = None) -> None:
        self.scheduler = AsyncIOScheduler(
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": 300,
            }
        )
        self.slack_client = slack_client
        self._running = False

    async def start(self) -> None:
        """Discover all workspaces and schedule their crons."""
        base = settings.workspace_root
        if not base.is_dir():
            logger.info("no_workspace_root", path=str(base))
            return

        total_jobs = 0
        for ws_dir in sorted(base.iterdir()):
            if not ws_dir.is_dir():
                continue
            ws_id = ws_dir.name
            crons = await self._load_crons(ws_id)
            for cron in crons:
                try:
                    trigger = CronTrigger.from_crontab(cron.cron)
                    self.scheduler.add_job(
                        self._run_cron,
                        trigger=trigger,
                        args=[ws_id, cron],
                        id=cron.job_id,
                        name=cron.title,
                        replace_existing=True,
                    )
                    total_jobs += 1
                    logger.info(
                        "cron_scheduled",
                        workspace_id=ws_id,
                        cron_path=cron.path,
                        schedule=cron.cron,
                        title=cron.title,
                    )
                except Exception as e:
                    logger.error(
                        "cron_schedule_failed",
                        workspace_id=ws_id,
                        cron_path=cron.path,
                        error=str(e),
                    )

            self._schedule_slack_sync(ws_id)
            total_jobs += 1

        self.scheduler.start()
        self._running = True
        logger.info("cron_scheduler_started", total_jobs=total_jobs)

    async def stop(self) -> None:
        if self._running:
            self.scheduler.shutdown(wait=False)
            self._running = False
            logger.info("cron_scheduler_stopped")

    async def reload_workspace(self, workspace_id: str) -> int:
        """Reload crons for a single workspace (e.g. after editing task.json).

        Returns the number of crons loaded.
        """
        # Remove existing jobs for this workspace
        for job in self.scheduler.get_jobs():
            if job.id.startswith(f"{workspace_id}:"):
                self.scheduler.remove_job(job.id)

        crons = await self._load_crons(workspace_id)
        for cron in crons:
            try:
                trigger = CronTrigger.from_crontab(cron.cron)
                self.scheduler.add_job(
                    self._run_cron,
                    trigger=trigger,
                    args=[workspace_id, cron],
                    id=cron.job_id,
                    name=cron.title,
                    replace_existing=True,
                )
            except Exception as e:
                logger.error(
                    "cron_reload_failed",
                    workspace_id=workspace_id,
                    cron_path=cron.path,
                    error=str(e),
                )

        logger.info(
            "workspace_crons_reloaded",
            workspace_id=workspace_id,
            count=len(crons),
        )
        return len(crons)

    async def trigger_now(self, workspace_id: str, cron_path: str) -> bool:
        """Manually trigger a cron immediately (for testing)."""
        crons = await self._load_crons(workspace_id)
        target = next((c for c in crons if c.path == cron_path), None)
        if not target:
            return False
        await self._run_cron(workspace_id, target)
        return True

    def list_jobs(self) -> list[dict[str, Any]]:
        """Return a snapshot of all scheduled jobs."""
        jobs = []
        for job in self.scheduler.get_jobs():
            jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run": (
                    job.next_run_time.isoformat()
                    if job.next_run_time
                    else None
                ),
            })
        return jobs

    def _schedule_slack_sync(self, workspace_id: str) -> None:
        """Register the lightweight Slack message sync cron for a workspace."""
        job_id = f"{workspace_id}:_slack_sync"
        try:
            self.scheduler.add_job(
                self._run_slack_sync,
                trigger=CronTrigger.from_crontab("*/10 * * * *"),
                args=[workspace_id],
                id=job_id,
                name=f"Slack sync ({workspace_id})",
                replace_existing=True,
            )
            logger.info("slack_sync_cron_scheduled", workspace_id=workspace_id)
        except Exception as e:
            logger.error(
                "slack_sync_cron_schedule_failed",
                workspace_id=workspace_id,
                error=str(e),
            )

    async def _run_slack_sync(self, workspace_id: str) -> None:
        """Execute the Slack message sync (no agent needed — direct I/O)."""
        if not self.slack_client:
            return

        import time as _time

        t0 = _time.monotonic()
        ws = get_workspace(workspace_id)

        try:
            from lucy.workspace.slack_sync import (
                get_last_sync_ts,
                save_last_sync_ts,
                sync_channel_messages,
            )

            since_ts = await get_last_sync_ts(ws)
            count = await sync_channel_messages(ws, self.slack_client, since_ts)

            now_ts = str(_time.time())
            await save_last_sync_ts(ws, now_ts)

            elapsed_ms = round((_time.monotonic() - t0) * 1000)
            logger.info(
                "slack_sync_cron_complete",
                workspace_id=workspace_id,
                messages_synced=count,
                elapsed_ms=elapsed_ms,
            )
        except Exception as e:
            logger.error(
                "slack_sync_cron_failed",
                workspace_id=workspace_id,
                error=str(e),
                exc_info=True,
            )

    # ── Internal ────────────────────────────────────────────────────────

    async def _load_crons(self, workspace_id: str) -> list[CronConfig]:
        """Load all task.json files from a workspace's crons/ directory."""
        ws = get_workspace(workspace_id)
        crons_dir = ws.root / "crons"
        if not crons_dir.is_dir():
            return []

        configs: list[CronConfig] = []
        for entry in sorted(crons_dir.iterdir()):
            if not entry.is_dir():
                continue
            task_file = entry / "task.json"
            if not task_file.is_file():
                continue
            try:
                data = json.loads(task_file.read_text("utf-8"))
                configs.append(CronConfig(
                    path=data["path"],
                    cron=data["cron"],
                    title=data["title"],
                    description=data["description"],
                    workspace_dir=workspace_id,
                    created_at=data.get("created_at", ""),
                    updated_at=data.get("updated_at", ""),
                ))
            except Exception as e:
                logger.warning(
                    "cron_parse_failed",
                    workspace_id=workspace_id,
                    file=str(task_file),
                    error=str(e),
                )

        return configs

    async def _run_cron(self, workspace_id: str, cron: CronConfig) -> None:
        """Execute a single cron job.

        1. Read LEARNINGS.md for accumulated context
        2. Run the agent with the cron description + learnings
        3. Log the execution
        """
        import time as _time

        t0 = _time.monotonic()
        logger.info(
            "cron_execution_start",
            workspace_id=workspace_id,
            cron_path=cron.path,
            title=cron.title,
        )

        ws = get_workspace(workspace_id)

        # Read accumulated learnings for this cron
        cron_dir_name = cron.path.strip("/")
        learnings = await ws.read_file(
            f"crons/{cron_dir_name}/LEARNINGS.md"
        )

        # Build the instruction for the agent
        instruction_parts = [cron.description]
        if learnings:
            instruction_parts.append(
                f"\n\n## Accumulated Learnings\n{learnings}"
            )

        instruction = "\n".join(instruction_parts)

        try:
            from lucy.core.agent import AgentContext, get_agent

            agent = get_agent()
            ctx = AgentContext(workspace_id=workspace_id)

            response = await agent.run(
                message=instruction,
                ctx=ctx,
                slack_client=self.slack_client,
            )

            elapsed_ms = round((_time.monotonic() - t0) * 1000)

            # Log execution to the cron's execution.log
            now = datetime.now(timezone.utc).isoformat()
            log_entry = (
                f"\n## {now} (elapsed: {elapsed_ms}ms)\n"
                f"{response[:500]}\n"
            )
            await ws.append_file(
                f"crons/{cron_dir_name}/execution.log", log_entry
            )

            # Also log to the daily activity log
            from lucy.workspace.activity_log import log_activity

            await log_activity(
                ws,
                f"Cron '{cron.title}' completed in {elapsed_ms}ms",
            )

            logger.info(
                "cron_execution_complete",
                workspace_id=workspace_id,
                cron_path=cron.path,
                elapsed_ms=elapsed_ms,
                response_length=len(response),
            )

        except Exception as e:
            elapsed_ms = round((_time.monotonic() - t0) * 1000)
            logger.error(
                "cron_execution_failed",
                workspace_id=workspace_id,
                cron_path=cron.path,
                error=str(e),
                elapsed_ms=elapsed_ms,
                exc_info=True,
            )

            # Log the failure
            now = datetime.now(timezone.utc).isoformat()
            await ws.append_file(
                f"crons/{cron_dir_name}/execution.log",
                f"\n## {now} — FAILED ({elapsed_ms}ms)\n{str(e)[:300]}\n",
            )


# ── Singleton ───────────────────────────────────────────────────────────

_scheduler: CronScheduler | None = None


def get_scheduler(slack_client: Any = None) -> CronScheduler:
    """Get or create the singleton scheduler."""
    global _scheduler
    if _scheduler is None:
        _scheduler = CronScheduler(slack_client=slack_client)
    return _scheduler
