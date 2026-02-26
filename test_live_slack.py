import asyncio
import json
import os
import sys
from pathlib import Path

# Setup paths
sys.path.insert(0, str(Path(__file__).parent / "src"))

from lucy.crons.scheduler import CronConfig, get_scheduler
from slack_sdk.web.async_client import AsyncWebClient

async def run_live_tests():
    keys = json.load(open("keys.json"))
    bot_token = keys["slack"]["bot_token"]
    
    import ssl
    import certifi
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    slack_client = AsyncWebClient(token=bot_token, ssl=ssl_context)
    scheduler = get_scheduler(slack_client=slack_client)
    ws_id = "8e302095-f4e6-4243-906f-55f6c3bd2583"
    ws_root = Path(f"workspaces/{ws_id}")
    os.makedirs(ws_root, exist_ok=True)
    
    channel_id = "C0AGNRMGALS" # #talk-to-lucy
    
    print("1. Testing Script Cron...")
    script_path1 = "live_script_1.py"
    (ws_root / script_path1).write_text("print('Hello from a deterministic Script Cron! No LLMs were harmed (or used) in the making of this message.')")
    
    cron_script = CronConfig(
        path="/test-live-script",
        cron="* * * * *",
        title="Live Script Test",
        description=script_path1,
        workspace_dir=ws_id,
        type="script",
        delivery_mode="channel",
        delivery_channel=channel_id
    )
    os.makedirs(ws_root / "crons" / "test-live-script", exist_ok=True)
    await scheduler._run_cron(ws_id, cron_script)
    
    print("2. Testing Block Kit Cron...")
    script_path2 = "live_script_2.py"
    block_payload = {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "Live Block Kit Test :rocket:",
                    "emoji": True
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "This message was sent using *Block Kit* natively parsed from a cron output. The new scheduler automatically detects JSON block payloads!"
                }
            }
        ]
    }
    (ws_root / script_path2).write_text(f"print({json.dumps(json.dumps(block_payload))})")
    
    cron_block = CronConfig(
        path="/test-live-block",
        cron="* * * * *",
        title="Live Block Test",
        description=script_path2,
        workspace_dir=ws_id,
        type="script",
        delivery_mode="channel",
        delivery_channel=channel_id
    )
    os.makedirs(ws_root / "crons" / "test-live-block", exist_ok=True)
    await scheduler._run_cron(ws_id, cron_block)
    
    print("3. Testing Agent Cron with De-AI Engine...")
    cron_agent = CronConfig(
        path="/test-live-agent",
        cron="* * * * *",
        title="Live Agent Test",
        description="Write exactly one short sentence saying you are testing the new De-AI engine. Then write exactly one more short sentence saying you will delve into the details. Then end the message with exactly: 'Hope this helps!'",
        workspace_dir=ws_id,
        type="agent",
        delivery_mode="channel",
        delivery_channel=channel_id
    )
    os.makedirs(ws_root / "crons" / "test-live-agent", exist_ok=True)
    await scheduler._run_cron(ws_id, cron_agent)
    
    print("Live tests completed! Check #talk-to-lucy.")

if __name__ == "__main__":
    asyncio.run(run_live_tests())