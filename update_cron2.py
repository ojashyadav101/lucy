import re

with open("src/lucy/crons/scheduler.py", "r", encoding="utf-8") as f:
    content = f.read()

# Replace signature of _build_cron_instruction
old_sig = """    def _build_cron_instruction(
        self, cron: CronConfig, learnings: str | None,
    ) -> str:"""
new_sig = """    def _build_cron_instruction(
        self, cron: CronConfig, learnings: str | None, global_context: str | None = None,
    ) -> str:"""
content = content.replace(old_sig, new_sig)

# Add appending global context
old_ret = """        if learnings:
            parts.append(f"\\n## Context from previous runs\\n{learnings}")

        return "\\n".join(parts)"""
new_ret = """        if learnings:
            parts.append(f"\\n## Context from previous runs\\n{learnings}")

        if global_context:
            parts.append(f"\\n## Global Context\\n{global_context}")

        return "\\n".join(parts)"""
content = content.replace(old_ret, new_ret)

# Update _run_cron to fetch and pass global context
old_learnings = """        learnings = await ws.read_file(f"crons/{cron_dir_name}/LEARNINGS.md")
        instruction = self._build_cron_instruction(cron, learnings)"""

new_learnings = """        learnings = await ws.read_file(f"crons/{cron_dir_name}/LEARNINGS.md")
        
        company_ctx = await ws.read_file("company/SKILL.md") or ""
        team_ctx = await ws.read_file("team/SKILL.md") or ""
        
        from datetime import datetime, timezone
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        
        global_context_parts = [f"Current Time: {now_utc}"]
        if company_ctx.strip():
            global_context_parts.append(f"\\n[Company Context]\\n{company_ctx.strip()}")
        if team_ctx.strip():
            global_context_parts.append(f"\\n[Team Directory]\\n{team_ctx.strip()}")
            
        global_context = "\\n".join(global_context_parts)
        
        instruction = self._build_cron_instruction(cron, learnings, global_context)"""
content = content.replace(old_learnings, new_learnings)

with open("src/lucy/crons/scheduler.py", "w", encoding="utf-8") as f:
    f.write(content)

print("Updated _build_cron_instruction and _run_cron")
