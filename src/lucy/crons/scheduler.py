"""CronScheduler — APScheduler-based proactivity engine.

Loads task.json files from each workspace's crons/ directory and
schedules them as recurring jobs. Each cron triggers a fresh Lucy
agent run with the task description as the instruction.

Capabilities:
- Cron expression validation before scheduling
- Per-job retry with exponential backoff
- Slack notification on persistent failures
- Timezone-aware scheduling
- Runtime cron creation/deletion with auto-reload
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from lucy.config import settings
from lucy.workspace.filesystem import get_workspace

logger = structlog.get_logger()

MAX_RETRIES = 2
RETRY_DELAY_BASE = 30
_MISFIRE_GRACE_TIME_S = 300
_HIGH_FREQ_THRESHOLD = 24
_MODERATE_FREQ_THRESHOLD = 6
_SCRIPT_TIMEOUT_S = 1800
_CONDITION_SCRIPT_TIMEOUT_S = 30


def validate_cron_expression(expr: str) -> str | None:
    """Return None if valid, error message if invalid."""
    try:
        CronTrigger.from_crontab(expr)
        return None
    except (ValueError, TypeError) as e:
        return str(e)


def _estimate_daily_runs(cron_expr: str) -> int:
    """Rough estimate of how many times a cron fires per day."""
    parts = cron_expr.strip().split()
    if len(parts) < 5:
        return 1

    minute, hour = parts[0], parts[1]

    def _count_field(field: str, total: int) -> int:
        if field == "*":
            return total
        if field.startswith("*/"):
            step = int(field[2:])
            return max(1, total // step)
        return len(field.split(","))

    minute_hits = _count_field(minute, 60)
    hour_hits = _count_field(hour, 24)
    return minute_hits * hour_hits


@dataclass
class CronConfig:
    """Parsed cron from a task.json file."""

    path: str
    cron: str
    title: str
    description: str
    workspace_dir: str
    type: str = "agent"  # "agent" or "script"
    condition_script_path: str = ""
    max_runs: int = 0
    depends_on: str = ""
    created_at: str = ""
    updated_at: str = ""
    timezone: str = ""
    max_retries: int = MAX_RETRIES
    notify_on_failure: bool = True
    delivery_channel: str = ""
    requesting_user_id: str = ""
    delivery_mode: str = "channel"

    @property
    def job_id(self) -> str:
        return f"{self.workspace_dir}:{self.path}"


class CronScheduler:
    """Discovers and schedules workspace crons via APScheduler."""

    def __init__(self, slack_client: Any = None) -> None:
        from apscheduler.events import EVENT_JOB_MISSED

        self.scheduler = AsyncIOScheduler(
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": _MISFIRE_GRACE_TIME_S,
            }
        )
        self.scheduler.add_listener(self._on_job_missed, EVENT_JOB_MISSED)
        self.slack_client = slack_client
        self._running = False

    @staticmethod
    def _on_job_missed(event: Any) -> None:
        logger.warning(
            "cron_job_missed",
            job_id=event.job_id,
            scheduled_run_time=str(event.scheduled_run_time),
        )

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
                    self._schedule_cron(ws_id, cron)
                    total_jobs += 1
                except Exception as e:
                    logger.error(
                        "cron_schedule_failed",
                        workspace_id=ws_id,
                        cron_path=cron.path,
                        error=str(e),
                    )

            self._schedule_slack_sync(ws_id)
            self._schedule_memory_consolidation(ws_id)
            total_jobs += 2

        self._schedule_humanize_pool_refresh()
        total_jobs += 1

        self._schedule_heartbeat_loop()
        total_jobs += 1

        self.scheduler.start()
        self._running = True
        logger.info("cron_scheduler_started", total_jobs=total_jobs)

    async def stop(self) -> None:
        if self._running:
            running_jobs = [
                job.name for job in self.scheduler.get_jobs()
                if job.next_run_time is not None
            ]
            if running_jobs:
                logger.info(
                    "cron_scheduler_stopping_with_jobs",
                    pending_jobs=running_jobs[:20],
                )
            self.scheduler.shutdown(wait=True)
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
                self._schedule_cron(workspace_id, cron)
            except Exception as e:
                logger.error(
                    "cron_reload_failed",
                    workspace_id=workspace_id,
                    cron_path=cron.path,
                    error=str(e),
                )

        self._schedule_slack_sync(workspace_id)
        self._schedule_memory_consolidation(workspace_id)

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

    def _schedule_cron(self, workspace_id: str, cron: CronConfig) -> None:
        """Schedule a single cron job with timezone support."""
        tz = cron.timezone if cron.timezone else None
        trigger = CronTrigger.from_crontab(cron.cron, timezone=tz)
        self.scheduler.add_job(
            self._run_cron,
            trigger=trigger,
            args=[workspace_id, cron],
            id=cron.job_id,
            name=cron.title,
            replace_existing=True,
        )
        logger.info(
            "cron_scheduled",
            workspace_id=workspace_id,
            cron_path=cron.path,
            schedule=cron.cron,
            title=cron.title,
            timezone=tz or "server",
        )

    async def create_cron(
        self,
        workspace_id: str,
        name: str,
        cron_expr: str,
        title: str,
        description: str,
        tz: str = "",
        notify_on_failure: bool = True,
        delivery_channel: str = "",
        requesting_user_id: str = "",
        delivery_mode: str = "channel",
        type: str = "agent",
        condition_script_path: str = "",
        max_runs: int = 0,
        depends_on: str = "",
    ) -> dict[str, Any]:
        """Create a new cron job: write task.json and register with scheduler.

        delivery_mode controls where the result is posted:
        - "channel": post to the delivery_channel (default)
        - "dm": DM the requesting_user_id directly

        Returns a result dict with status and details.
        """
        validation_err = validate_cron_expression(cron_expr)
        if validation_err:
            return {
                "success": False,
                "error": f"Invalid cron expression '{cron_expr}': {validation_err}",
            }

        runs_per_day = _estimate_daily_runs(cron_expr)
        cost_warning = ""
        if runs_per_day > _HIGH_FREQ_THRESHOLD and type == "agent":
            cost_warning = (
                f"This task will run ~{runs_per_day} times per day. "
                f"Each run uses LLM tokens. Consider a longer interval "
                f"if this is an agent-based task."
            )
        elif runs_per_day > _MODERATE_FREQ_THRESHOLD and type == "agent":
            cost_warning = (
                f"This task will run ~{runs_per_day} times per day. "
                f"Keep in mind each run uses LLM tokens."
            )

        slug = name.lower().replace(" ", "-")
        slug = "".join(c for c in slug if c.isalnum() or c == "-")
        path = f"/{slug}"

        now = datetime.now(timezone.utc).isoformat()
        task_data = {
            "path": path,
            "cron": cron_expr,
            "title": title,
            "description": description,
            "created_at": now,
            "updated_at": now,
            "type": type,
        }
        if condition_script_path:
            task_data["condition_script_path"] = condition_script_path
        if max_runs > 0:
            task_data["max_runs"] = max_runs
        if depends_on:
            task_data["depends_on"] = depends_on
        if tz:
            task_data["timezone"] = tz
        if not notify_on_failure:
            task_data["notify_on_failure"] = False
        if delivery_channel:
            task_data["delivery_channel"] = delivery_channel
        if requesting_user_id:
            task_data["requesting_user_id"] = requesting_user_id
        if delivery_mode != "channel":
            task_data["delivery_mode"] = delivery_mode

        ws = get_workspace(workspace_id)
        cron_dir = f"crons/{slug}"
        file_path = f"{cron_dir}/task.json"
        content = json.dumps(task_data, indent=2, ensure_ascii=False)
        await ws.write_file(file_path, content)

        # Create default LEARNINGS.md if it doesn't exist
        learnings_path = f"{cron_dir}/LEARNINGS.md"
        existing_learnings = await ws.read_file(learnings_path)
        if not existing_learnings and type == "agent":
            default_learnings = (
                f"# {title} - Learnings\n\n"
                "## Pending Items\n- [ ] Initial run pending\n\n"
                "## Team Dynamics & Context\n- \n\n"
                "## Infrastructure Notes\n- \n\n"
                "## Recent Actions\n- \n"
            )
            await ws.write_file(learnings_path, default_learnings)

        # Create discovery.md for workflow-discovery
        if slug == "workflow-discovery":
            discovery_path = f"{cron_dir}/discovery.md"
            existing_discovery = await ws.read_file(discovery_path)
            if not existing_discovery:
                default_discovery = (
                    "# Workflow Discovery Progress\n\n"
                    "## Team Members Investigated\n"
                    "| Person | Role | Investigated | Ideas Found | Proposals Made |\n"
                    "| ------ | ---- | ------------ | ----------- | -------------- |\n"
                    "|        |      |              |             |                |\n\n"
                    "## Connected Integrations\n- [ ] \n\n"
                    "## Ideas Per Person\n\n"
                    "## Proposals Made\n"
                    "| Workflow | For | Status | Implementation |\n"
                    "| -------- | --- | ------ | -------------- |\n"
                )
                await ws.write_file(discovery_path, default_discovery)

        count = await self.reload_workspace(workspace_id)
        result: dict[str, Any] = {
            "success": True,
            "cron_name": slug,
            "schedule": cron_expr,
            "title": title,
            "timezone": tz or "server default",
            "total_workspace_crons": count,
        }
        if cost_warning:
            result["cost_warning"] = cost_warning
        return result

    async def delete_cron(
        self, workspace_id: str, cron_name: str
    ) -> dict[str, Any]:
        """Delete a cron job: remove task.json directory and unschedule.

        Supports fuzzy matching: if exact slug not found, searches by
        title substring match.
        """
        import shutil

        slug = cron_name.lower().replace(" ", "-")
        slug = "".join(c for c in slug if c.isalnum() or c == "-")

        ws = get_workspace(workspace_id)
        cron_dir = ws.root / "crons" / slug

        if not cron_dir.is_dir():
            match = await self._fuzzy_find_cron(workspace_id, cron_name)
            if not match:
                available = await self._load_crons(workspace_id)
                names = [c.title for c in available]
                return {
                    "success": False,
                    "error": f"Cron '{cron_name}' not found.",
                    "available_crons": names,
                }
            slug = match.path.strip("/")
            cron_dir = ws.root / "crons" / slug

        job_id = f"{workspace_id}:/{slug}"
        try:
            self.scheduler.remove_job(job_id)
        except Exception as e:
            logger.warning("cron_job_remove_failed", job_id=job_id, error=str(e))

        shutil.rmtree(cron_dir, ignore_errors=True)
        logger.info("cron_deleted", workspace_id=workspace_id, cron_name=slug)
        return {"success": True, "deleted": slug}

    async def modify_cron(
        self,
        workspace_id: str,
        cron_name: str,
        new_cron_expr: str | None = None,
        new_description: str | None = None,
        new_title: str | None = None,
        new_tz: str | None = None,
    ) -> dict[str, Any]:
        """Update an existing cron's schedule, description, or title.

        Supports fuzzy matching: if exact slug not found, searches by
        title substring match.
        """
        slug = cron_name.lower().replace(" ", "-")
        slug = "".join(c for c in slug if c.isalnum() or c == "-")

        ws = get_workspace(workspace_id)
        task_file = ws.root / "crons" / slug / "task.json"

        if not task_file.is_file():
            match = await self._fuzzy_find_cron(workspace_id, cron_name)
            if not match:
                available = await self._load_crons(workspace_id)
                names = [c.title for c in available]
                return {
                    "success": False,
                    "error": f"Cron '{cron_name}' not found.",
                    "available_crons": names,
                }
            slug = match.path.strip("/")
            task_file = ws.root / "crons" / slug / "task.json"

        try:
            data = json.loads(task_file.read_text("utf-8"))
        except Exception as e:
            return {"success": False, "error": f"Failed to read task.json: {e}"}

        if new_cron_expr:
            validation_err = validate_cron_expression(new_cron_expr)
            if validation_err:
                return {
                    "success": False,
                    "error": f"Invalid cron expression: {validation_err}",
                }
            data["cron"] = new_cron_expr
        if new_description:
            data["description"] = new_description
        if new_title:
            data["title"] = new_title
        if new_tz is not None:
            data["timezone"] = new_tz

        data["updated_at"] = datetime.now(timezone.utc).isoformat()

        task_file.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        count = await self.reload_workspace(workspace_id)
        return {
            "success": True,
            "updated": slug,
            "total_workspace_crons": count,
        }

    def list_jobs(self) -> list[dict[str, Any]]:
        """Return a snapshot of all scheduled jobs with rich metadata."""
        jobs = []
        for job in self.scheduler.get_jobs():
            entry: dict[str, Any] = {
                "id": job.id,
                "name": job.name,
                "next_run": (
                    job.next_run_time.isoformat()
                    if job.next_run_time
                    else None
                ),
            }
            if hasattr(job.trigger, "FIELD_NAMES"):
                try:
                    fields = {
                        f.name: str(f) for f in job.trigger.fields
                    }
                    entry["schedule_fields"] = fields
                except Exception as e:
                    logger.warning("cron_schedule_fields_parse_failed", error=str(e))
            if job.args and len(job.args) >= 2:
                cron_cfg = job.args[1]
                if isinstance(cron_cfg, CronConfig):
                    entry["schedule"] = cron_cfg.cron
                    entry["description"] = cron_cfg.description[:200]
                    entry["created_at"] = cron_cfg.created_at
                    if cron_cfg.timezone:
                        entry["timezone"] = cron_cfg.timezone
            jobs.append(entry)
        return jobs

    def _schedule_memory_consolidation(self, workspace_id: str) -> None:
        """Schedule periodic memory consolidation for a workspace.

        Every 6 hours, promote session facts to permanent knowledge files.
        """
        job_id = f"{workspace_id}:_memory_consolidation"
        try:
            self.scheduler.add_job(
                self._run_memory_consolidation,
                trigger=CronTrigger.from_crontab("0 */6 * * *"),
                args=[workspace_id],
                id=job_id,
                name=f"Memory consolidation ({workspace_id})",
                replace_existing=True,
            )
            logger.info("memory_consolidation_cron_scheduled", workspace_id=workspace_id)
        except Exception as e:
            logger.error(
                "memory_consolidation_cron_schedule_failed",
                workspace_id=workspace_id,
                error=str(e),
            )

    async def _run_memory_consolidation(self, workspace_id: str) -> None:
        """Execute memory consolidation — session facts to permanent knowledge."""
        ws = get_workspace(workspace_id)
        try:
            from lucy.workspace.memory import consolidate_session_to_knowledge
            promoted = await consolidate_session_to_knowledge(ws)
            if promoted > 0:
                logger.info(
                    "memory_consolidation_run",
                    workspace_id=workspace_id,
                    promoted=promoted,
                )
        except Exception as e:
            logger.error(
                "memory_consolidation_failed",
                workspace_id=workspace_id,
                error=str(e),
            )

    def _schedule_humanize_pool_refresh(self) -> None:
        """Refresh LLM-generated message pools every 6 hours."""
        job_id = "_global:humanize_pool_refresh"
        try:
            self.scheduler.add_job(
                self._run_humanize_refresh,
                trigger=CronTrigger.from_crontab("0 */6 * * *"),
                id=job_id,
                name="Humanize pool refresh",
                replace_existing=True,
            )
            logger.info("humanize_pool_refresh_scheduled")
        except Exception as e:
            logger.error("humanize_pool_refresh_schedule_failed", error=str(e))

    async def _run_humanize_refresh(self) -> None:
        """Regenerate LLM message pools."""
        try:
            from lucy.pipeline.humanize import refresh_pools
            await refresh_pools()
        except Exception as e:
            logger.error("humanize_pool_refresh_failed", error=str(e))

    def _schedule_heartbeat_loop(self) -> None:
        """Run heartbeat condition evaluations every 30 seconds."""
        from apscheduler.triggers.interval import IntervalTrigger

        job_id = "_global:heartbeat_eval"
        try:
            self.scheduler.add_job(
                self._run_heartbeat_eval,
                trigger=IntervalTrigger(seconds=30),
                id=job_id,
                name="Heartbeat evaluation loop",
                replace_existing=True,
            )
            logger.info("heartbeat_eval_loop_scheduled")
        except Exception as e:
            logger.error("heartbeat_eval_schedule_failed", error=str(e))

    async def _run_heartbeat_eval(self) -> None:
        """Evaluate all due heartbeat monitors."""
        try:
            from lucy.crons.heartbeat import evaluate_due_heartbeats
            evaluated = await evaluate_due_heartbeats(self.slack_client)
            if evaluated > 0:
                logger.debug("heartbeat_eval_complete", evaluated=evaluated)
        except Exception as e:
            logger.warning("heartbeat_eval_failed", error=str(e))

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
                for required in ("path", "cron", "title", "description"):
                    if required not in data:
                        raise ValueError(f"missing required field: {required}")

                validation_err = validate_cron_expression(data["cron"])
                if validation_err:
                    logger.error(
                        "cron_invalid_expression",
                        workspace_id=workspace_id,
                        file=str(task_file),
                        cron=data["cron"],
                        error=validation_err,
                    )
                    continue

                configs.append(CronConfig(
                    path=data["path"],
                    cron=data["cron"],
                    title=data["title"],
                    description=data["description"],
                    workspace_dir=workspace_id,
                    type=data.get("type", "agent"),
                    condition_script_path=data.get("condition_script_path", ""),
                    max_runs=int(data.get("max_runs", 0)),
                    depends_on=data.get("depends_on", ""),
                    created_at=data.get("created_at", ""),
                    updated_at=data.get("updated_at", ""),
                    timezone=data.get("timezone", ""),
                    max_retries=int(data.get("max_retries", MAX_RETRIES)),
                    notify_on_failure=data.get("notify_on_failure", True),
                    delivery_channel=data.get("delivery_channel", ""),
                    requesting_user_id=data.get("requesting_user_id", ""),
                    delivery_mode=data.get("delivery_mode", "channel"),
                ))
            except Exception as e:
                logger.warning(
                    "cron_parse_failed",
                    workspace_id=workspace_id,
                    file=str(task_file),
                    error=str(e),
                )

        return configs

    def _build_cron_instruction(
        self, cron: CronConfig, learnings: str | None, global_context: str | None = None,
    ) -> str:
        """Build the instruction that the cron agent receives.

        The instruction frames Lucy as proactively reaching out rather
        than answering a question. It preserves full SOUL personality
        through the normal agent.run() pipeline while preventing the
        agent from recursively creating new crons or asking questions
        into the void. Includes a self-validation layer so the agent
        sanity-checks its own results before delivering them.
        """
        parts: list[str] = []

        parts.append(
            "You are Lucy, running a scheduled task. This is a proactive "
            "action, not a response to a user message. Write your output "
            "as if you're reaching out to a teammate: natural, warm, and "
            "useful. Your output will be posted to Slack automatically."
        )

        parts.append(
            "\nRules for this execution:\n"
            "- Do NOT create, modify, or delete any cron jobs\n"
            "- Do NOT ask clarifying questions (nobody is listening live)\n"
            "- Do NOT suggest setting up reminders or schedules\n"
            "- If the task requires data, use your tools to fetch it\n"
            "- If the task has nothing to report, return SKIP (literally "
            "the word SKIP and nothing else)\n"
            "- For heartbeat check-ins specifically, return HEARTBEAT_OK "
            "if everything looks fine and nothing needs action\n"
            "- Keep your response concise and actionable"
        )

        parts.append(
            "\n## Self-validation (important)\n"
            "Before returning your result, critically check it:\n"
            "1. Does your output actually answer the task? Re-read the "
            "task description and compare.\n"
            "2. If you fetched data, does it look reasonable? Check for "
            "empty results, error pages, stale data, or nonsensical "
            "values. If the data looks wrong, try a different approach "
            "or tool before giving up.\n"
            "3. If the result is clearly not what the user intended "
            "(e.g. wrong product, wrong metric, data from the wrong "
            "time period), note it in your output honestly. Do not "
            "deliver confidently wrong information.\n"
            "4. Log any issues or observations to "
            f"crons/{cron.path.strip('/')}/LEARNINGS.md so future "
            "runs can improve. Include what worked, what didn't, and "
            "what to try next time."
        )

        parts.append(f"\n## Task\n{cron.description}")

        if cron.requesting_user_id:
            parts.append(
                f"\nThis task was set up by <@{cron.requesting_user_id}>. "
                f"Address them naturally if relevant."
            )

        if learnings:
            parts.append(f"\n## Context from previous runs\n{learnings}")

        if global_context:
            parts.append(f"\n## Global Context\n{global_context}")

        from lucy.config import settings as _s
        if _s.agentmail_enabled and _s.agentmail_api_key:
            email_addr = f"lucy@{_s.agentmail_domain}"
            parts.append(
                f"\n## Email Capability\n"
                f"You have your own email address: {email_addr}\n"
                "You can send emails using the lucy_send_email tool. "
                "Use this for sending reports, notifications, or any "
                "communication that should come from your email identity."
            )

        return "\n".join(parts)

    def _resolve_delivery_target(self, cron: CronConfig) -> str | None:
        """Determine where to post the cron result.

        Priority:
        1. delivery_mode=dm + requesting_user_id -> DM the user
        2. delivery_mode=channel + delivery_channel -> post to channel
        3. delivery_channel exists -> post to channel (default)
        4. No delivery info -> log only (system crons)
        """
        if cron.delivery_mode == "dm" and cron.requesting_user_id:
            return cron.requesting_user_id
        if cron.delivery_channel:
            return cron.delivery_channel
        return None

    async def _run_cron(self, workspace_id: str, cron: CronConfig) -> None:
        """Execute a cron job through the full Lucy agent pipeline or as a script.

        Flow:
        0. Check condition script (if configured)
        1. Read LEARNINGS.md for accumulated context
        2. Build instruction with personality framing
        3. Run the full agent (or deterministic script)
        4. Deliver the result to the right Slack destination
        5. Log the execution for future learning
        6. Retry on failure with exponential backoff
        7. Enforce max_runs (self-deletion)
        """
        import time as _time
        import os

        t0 = _time.monotonic()
        logger.info(
            "cron_execution_start",
            workspace_id=workspace_id,
            cron_path=cron.path,
            title=cron.title,
            type=cron.type,
        )

        ws = get_workspace(workspace_id)
        cron_dir_name = cron.path.strip("/")

        # --- Phase 3.1: Dependency Check ---
        if cron.depends_on:
            dep_slug = cron.depends_on.lower().replace(" ", "-")
            dep_slug = "".join(c for c in dep_slug if c.isalnum() or c == "-")
            try:
                dep_log = await ws.read_file(f"crons/{dep_slug}/execution.log")
                if not dep_log:
                    logger.info("cron_dependency_not_met_no_log", workspace_id=workspace_id, cron_path=cron.path, depends_on=dep_slug)
                    return
                    
                import zoneinfo as _zi
                cron_tz = timezone.utc
                if cron.timezone:
                    try:
                        cron_tz = _zi.ZoneInfo(cron.timezone)
                    except Exception as e:
                        logger.warning("cron_timezone_parse_failed", timezone=cron.timezone, error=str(e))
                today = datetime.now(cron_tz).strftime("%Y-%m-%d")
                
                ran_today = False
                for line in reversed(dep_log.splitlines()):
                    if line.startswith("## ") and today in line:
                        if "FAILED" not in line:
                            ran_today = True
                        break
                
                if not ran_today:
                    logger.info("cron_dependency_not_met", workspace_id=workspace_id, cron_path=cron.path, depends_on=dep_slug)
                    return
            except Exception as e:
                logger.warning("cron_dependency_check_failed", error=str(e))

        # --- Phase 1.1: Condition Script Check ---
        if cron.condition_script_path:
            script_path = ws.root / cron.condition_script_path.lstrip("/")
            if script_path.exists():
                process = await asyncio.create_subprocess_exec(
                    "python3", str(script_path),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env={**os.environ, "WORKSPACE_ID": workspace_id}
                )
                try:
                    await asyncio.wait_for(process.communicate(), timeout=_CONDITION_SCRIPT_TIMEOUT_S)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
                    logger.warning(
                        "cron_condition_script_timeout",
                        workspace_id=workspace_id,
                        cron_path=cron.path,
                        script=str(script_path),
                    )
                    return
                if process.returncode != 0:
                    logger.info("cron_condition_unmet", workspace_id=workspace_id, cron_path=cron.path)
                    return
            else:
                logger.warning("cron_condition_script_not_found", workspace_id=workspace_id, path=str(script_path))

        learnings = await ws.read_file(f"crons/{cron_dir_name}/LEARNINGS.md")
        
        company_ctx = await ws.read_file("company/SKILL.md") or ""
        team_ctx = await ws.read_file("team/SKILL.md") or ""
        
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        
        global_context_parts = [f"Current Time: {now_utc}"]
        if company_ctx.strip():
            global_context_parts.append(f"\n[Company Context]\n{company_ctx.strip()}")
        if team_ctx.strip():
            global_context_parts.append(f"\n[Team Directory]\n{team_ctx.strip()}")
            
        try:
            from lucy.integrations.composio_client import get_composio_client
            cclient = get_composio_client()
            connected_apps = await cclient.get_connected_app_names_reliable(workspace_id)
            if connected_apps:
                global_context_parts.append(f"\n[Connected Integrations]\n{', '.join(connected_apps)}")
        except Exception as e:
            logger.debug("failed_to_inject_integrations", error=str(e))
            
        global_context = "\n".join(global_context_parts)
        
        instruction = self._build_cron_instruction(cron, learnings, global_context)

        last_error: Exception | None = None
        max_attempts = 1 if cron.type == "script" else (1 + cron.max_retries)
        delivery_target = self._resolve_delivery_target(cron)

        for attempt in range(1, max_attempts + 1):
            try:
                # --- Phase 1.2: Script vs Agent Execution ---
                if cron.type == "script":
                    script_file = cron.description.replace("Script:", "").strip()
                    if script_file.startswith("/work/"):
                        script_file = script_file.replace("/work/", "")
                    elif script_file.startswith("/workspace/"):
                        script_file = script_file.replace("/workspace/", "")
                    
                    target_script = ws.root / script_file.lstrip("/")
                    if not target_script.exists():
                        raise RuntimeError(f"Script file not found: {target_script}")
                    
                    _script_timeout = _SCRIPT_TIMEOUT_S
                    process = await asyncio.create_subprocess_exec(
                        "python3", str(target_script),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        env={
                            **os.environ,
                            "WORKSPACE_ID": workspace_id,
                            "WORKSPACE_ROOT": str(ws.root),
                        },
                    )
                    try:
                        stdout, stderr = await asyncio.wait_for(
                            process.communicate(),
                            timeout=_script_timeout,
                        )
                    except asyncio.TimeoutError:
                        process.kill()
                        await process.wait()
                        raise RuntimeError(
                            f"Script timed out after {_script_timeout}s"
                        )
                    if process.returncode != 0:
                        raise RuntimeError(f"Script failed (exit {process.returncode}): {stderr.decode()}")
                    
                    response = stdout.decode().strip()
                else:
                    from lucy.core.agent import AgentContext, get_agent
                    agent = get_agent()
                    ctx = AgentContext(
                        workspace_id=workspace_id,
                        channel_id=delivery_target,
                        user_slack_id=cron.requesting_user_id or None,
                        is_cron_execution=True,
                    )
                    response = await agent.run(
                        message=instruction,
                        ctx=ctx,
                        slack_client=self.slack_client,
                    )

                elapsed_ms = round((_time.monotonic() - t0) * 1000)
                _upper = response.strip().upper() if response else ""
                skip = (
                    not response
                    or _upper == "SKIP"
                    or _upper == "HEARTBEAT_OK"
                    or _upper.startswith("HEARTBEAT_OK")
                )

                if not skip and response and response.strip() and delivery_target and self.slack_client:
                    await self._deliver_to_slack(delivery_target, response)

                now = datetime.now(timezone.utc).isoformat()
                status = "skipped" if skip else "delivered"
                log_entry = f"\n## {now} (elapsed: {elapsed_ms}ms, status: {status})"
                if attempt > 1:
                    log_entry += f" [succeeded on attempt {attempt}]"
                log_entry += f"\n{(response or '')[:500]}\n"
                
                await ws.append_file(f"crons/{cron_dir_name}/execution.log", log_entry)

                from lucy.workspace.activity_log import log_activity
                await log_activity(ws, f"Cron '{cron.title}' {status} in {elapsed_ms}ms")

                logger.info(
                    "cron_execution_complete",
                    workspace_id=workspace_id,
                    cron_path=cron.path,
                    elapsed_ms=elapsed_ms,
                    attempt=attempt,
                    response_length=len(response) if response else 0,
                    delivered_to=delivery_target or "log_only",
                    status=status,
                )

                # --- Phase 1.3: Max Runs / Self-deleting ---
                if cron.max_runs > 0:
                    log_content = await ws.read_file(f"crons/{cron_dir_name}/execution.log")
                    run_count = sum(
                        1 for line in log_content.splitlines()
                        if line.startswith("## ") and "FAILED" not in line
                    )
                    if run_count >= cron.max_runs:
                        logger.info("cron_max_runs_reached", workspace_id=workspace_id, cron_path=cron.path)
                        await self.delete_cron(workspace_id, cron.path)

                return

            except Exception as e:
                last_error = e
                elapsed_ms = round((_time.monotonic() - t0) * 1000)
                logger.warning(
                    "cron_execution_attempt_failed",
                    workspace_id=workspace_id,
                    cron_path=cron.path,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    error=str(e),
                    elapsed_ms=elapsed_ms,
                )

                if attempt < max_attempts:
                    delay = RETRY_DELAY_BASE * (2 ** (attempt - 1))
                    await asyncio.sleep(delay)

        elapsed_ms = round((_time.monotonic() - t0) * 1000)
        error_str = str(last_error) if last_error else "unknown"

        logger.error(
            "cron_execution_failed",
            workspace_id=workspace_id,
            cron_path=cron.path,
            error=error_str,
            elapsed_ms=elapsed_ms,
            attempts=max_attempts,
            exc_info=True,
        )

        now = datetime.now(timezone.utc).isoformat()
        await ws.append_file(
            f"crons/{cron_dir_name}/execution.log",
            f"\n## {now} -- FAILED after {max_attempts} attempts ({elapsed_ms}ms)\n{error_str[:300]}\n",
        )

        if cron.notify_on_failure and self.slack_client:
            await self._notify_cron_failure(workspace_id, cron, error_str, max_attempts, elapsed_ms)

    async def _notify_cron_failure(
        self,
        workspace_id: str,
        cron: CronConfig,
        error: str,
        attempts: int,
        elapsed_ms: int,
    ) -> None:
        """Post a DM to the workspace owner when a cron fails persistently."""
        try:
            owner_id = await self._resolve_workspace_owner(workspace_id)
            if not owner_id:
                return

            msg = (
                f"Your scheduled task *{cron.title}* failed after "
                f"{attempts} attempts ({elapsed_ms}ms total).\n\n"
                f"Error: `{error[:200]}`\n\n"
                f"Schedule: `{cron.cron}`\n"
                f"I'll try again at the next scheduled run. "
                f"Let me know if you want me to look into this."
            )
            await self.slack_client.chat_postMessage(
                channel=owner_id,
                text=msg,
            )
        except Exception as e:
            logger.warning(
                "cron_failure_notification_failed",
                workspace_id=workspace_id,
                error=str(e),
            )

    async def _deliver_to_slack(
        self, channel: str, text: str,
    ) -> None:
        """Post the cron result to a Slack channel or DM.

        Supports raw text (passed through the output pipeline) OR
        raw JSON string containing {"blocks": [...] } for rich Block Kit output.
        """
        try:
            # Check if output is a Block Kit JSON payload
            text_trimmed = text.strip()
            if text_trimmed.startswith("{") and text_trimmed.endswith("}") and '"blocks"' in text_trimmed:
                try:
                    payload = json.loads(text_trimmed)
                    if "blocks" in payload:
                        await self.slack_client.chat_postMessage(
                            channel=channel,
                            blocks=payload["blocks"],
                            text=payload.get("text", "Automated report"),
                        )
                        logger.info("cron_result_delivered_blocks", channel=channel, blocks=len(payload["blocks"]))
                        return
                except json.JSONDecodeError:
                    pass  # Fallback to plain text processing if invalid JSON

            try:
                from lucy.pipeline.output import process_output
                from lucy.slack.blockkit import text_to_blocks
                from lucy.slack.rich_output import enhance_blocks, format_links

                formatted = await process_output(text)
                formatted = format_links(formatted)

                blocks = text_to_blocks(formatted)
                blocks = enhance_blocks(blocks) if blocks else blocks
            except Exception as fmt_exc:
                logger.warning(
                    "cron_delivery_formatting_failed",
                    channel=channel,
                    error=str(fmt_exc),
                )
                formatted = text
                blocks = None

            if blocks:
                await self.slack_client.chat_postMessage(
                    channel=channel,
                    blocks=blocks,
                    text=formatted[:200],
                )
            else:
                await self.slack_client.chat_postMessage(
                    channel=channel,
                    text=formatted,
                )
            logger.info(
                "cron_result_delivered",
                channel=channel,
                text_length=len(formatted),
                blocks=len(blocks) if blocks else 0,
            )
        except Exception as e:
            logger.warning(
                "cron_delivery_failed",
                channel=channel,
                error=str(e),
            )

    async def _fuzzy_find_cron(
        self, workspace_id: str, query: str
    ) -> CronConfig | None:
        """Find a cron by fuzzy matching on title or path."""
        crons = await self._load_crons(workspace_id)
        if not crons:
            return None

        query_lower = query.lower().strip()
        query_words = set(query_lower.replace("-", " ").split())

        best: CronConfig | None = None
        best_score = 0

        for cron in crons:
            title_lower = cron.title.lower()
            path_lower = cron.path.strip("/").replace("-", " ")

            if query_lower in title_lower or query_lower in path_lower:
                return cron

            title_words = set(title_lower.split())
            path_words = set(path_lower.split())
            all_words = title_words | path_words

            overlap = len(query_words & all_words)
            if overlap > best_score:
                best_score = overlap
                best = cron

        if best_score >= max(1, len(query_words) // 2):
            return best
        return None

    async def _resolve_workspace_owner(self, workspace_id: str) -> str | None:
        """Find the Slack user ID of the workspace owner for DM notifications."""
        try:
            from lucy.db.session import db_session
            from sqlalchemy import text

            async with db_session() as session:
                result = await session.execute(
                    text(
                        "SELECT slack_user_id FROM users "
                        "WHERE workspace_id = :ws_id AND role = 'owner' "
                        "LIMIT 1"
                    ),
                    {"ws_id": workspace_id},
                )
                row = result.fetchone()
                return row[0] if row else None
        except Exception as e:
            logger.warning("workspace_owner_lookup_failed", error=str(e))
            return None


# ── Singleton ───────────────────────────────────────────────────────────

_scheduler: CronScheduler | None = None


def get_scheduler(slack_client: Any = None) -> CronScheduler:
    """Get or create the singleton scheduler."""
    global _scheduler
    if _scheduler is None:
        _scheduler = CronScheduler(slack_client=slack_client)
    return _scheduler
