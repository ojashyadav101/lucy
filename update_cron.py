import re

with open("src/lucy/crons/scheduler.py", "r", encoding="utf-8") as f:
    content = f.read()

start = content.find("    async def _run_cron(self, workspace_id: str, cron: CronConfig) -> None:")
end = content.find("    async def _notify_cron_failure(")

if start == -1 or end == -1:
    print("Could not find start or end")
    exit(1)

old_run_cron = content[start:end]

new_run_cron = """    async def _run_cron(self, workspace_id: str, cron: CronConfig) -> None:
        \"\"\"Execute a cron job through the full Lucy agent pipeline or as a script.

        Flow:
        0. Check condition script (if configured)
        1. Read LEARNINGS.md for accumulated context
        2. Build instruction with personality framing
        3. Run the full agent (or deterministic script)
        4. Deliver the result to the right Slack destination
        5. Log the execution for future learning
        6. Retry on failure with exponential backoff
        7. Enforce max_runs (self-deletion)
        \"\"\"
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
                await process.communicate()
                if process.returncode != 0:
                    logger.info("cron_condition_unmet", workspace_id=workspace_id, cron_path=cron.path)
                    return
            else:
                logger.warning("cron_condition_script_not_found", workspace_id=workspace_id, path=str(script_path))

        learnings = await ws.read_file(f"crons/{cron_dir_name}/LEARNINGS.md")
        instruction = self._build_cron_instruction(cron, learnings)

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
                    
                    process = await asyncio.create_subprocess_exec(
                        "python3", str(target_script),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        env={**os.environ, "WORKSPACE_ID": workspace_id}
                    )
                    stdout, stderr = await process.communicate()
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
                    )
                    response = await agent.run(
                        message=instruction,
                        ctx=ctx,
                        slack_client=self.slack_client,
                    )

                elapsed_ms = round((_time.monotonic() - t0) * 1000)
                skip = (response.strip().upper() == "SKIP" if response else True)

                if not skip and response and response.strip() and delivery_target and self.slack_client:
                    await self._deliver_to_slack(delivery_target, response)

                now = datetime.now(timezone.utc).isoformat()
                status = "skipped" if skip else "delivered"
                log_entry = f"\\n## {now} (elapsed: {elapsed_ms}ms, status: {status})"
                if attempt > 1:
                    log_entry += f" [succeeded on attempt {attempt}]"
                log_entry += f"\\n{response[:500]}\\n"
                
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
                    run_count = sum(1 for line in log_content.splitlines() if line.startswith("## "))
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
            f"\\n## {now} -- FAILED after {max_attempts} attempts ({elapsed_ms}ms)\\n{error_str[:300]}\\n",
        )

        if cron.notify_on_failure and self.slack_client:
            await self._notify_cron_failure(workspace_id, cron, error_str, max_attempts, elapsed_ms)

"""

new_content = content[:start] + new_run_cron + content[end:]
with open("src/lucy/crons/scheduler.py", "w", encoding="utf-8") as f:
    f.write(new_content)

print("Updated _run_cron")
