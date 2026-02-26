import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Setup paths
sys.path.insert(0, str(Path(__file__).parent / "src"))

from lucy.crons.scheduler import CronConfig, get_scheduler
from lucy.workspace.filesystem import get_workspace


async def run_heartbeat_e2e():
    print("\n--- End-to-End Test: Proactive Heartbeat ---\n")
    workspace_id = "8e302095-f4e6-4243-906f-55f6c3bd2583" # from test_cron_stress_v2.py
    ws = get_workspace(workspace_id)
    
    # Load keys
    keys = json.load(open("keys.json"))
    bot_token = keys["slack"]["bot_token"]
    
    # Init actual slack client
    from slack_sdk.web.async_client import AsyncWebClient
    slack_client = AsyncWebClient(token=bot_token)
    
    scheduler = get_scheduler(slack_client=slack_client)
    
    # Trigger the seeded heartbeat cron
    print("Loading seeded heartbeat cron...")
    crons = await scheduler._load_crons(workspace_id)
    heartbeat_cron = next((c for c in crons if c.path == "/heartbeat"), None)
    
    if not heartbeat_cron:
        print("  [FAIL] Could not find the seeded heartbeat cron in the workspace.")
        return
        
    print(f"  [PASS] Found '{heartbeat_cron.title}'.")
    
    print("Executing heartbeat cron (this will trigger the full LLM agent)...")
    await scheduler._run_cron(workspace_id, heartbeat_cron)
    
    print("Checking execution log...")
    log = await ws.read_file("crons/heartbeat/execution.log")
    
    if log and "status: delivered" in log:
        print("  [PASS] Heartbeat cron executed successfully and delivered output.")
        print(f"  --- Log Tail ---\n{log.splitlines()[-5:]}")
    elif log and "status: skipped" in log:
        print("  [PASS] Heartbeat cron executed successfully but decided to SKIP (no action needed).")
    else:
        print(f"  [FAIL] Heartbeat cron failed or had unexpected output.\n{log}")

if __name__ == "__main__":
    asyncio.run(run_heartbeat_e2e())
