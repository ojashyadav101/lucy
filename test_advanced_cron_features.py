import asyncio
import os
import sys
import json
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent / "src"))

from lucy.crons.scheduler import CronConfig, get_scheduler
from lucy.workspace.filesystem import get_workspace
from lucy.workspace.activity_log import log_activity
from lucy.workspace.slack_reader import get_local_messages

async def test_advanced_features():
    ws_id = "test_workspace_adv_features"
    ws = get_workspace(ws_id)
    ws_root = ws.root
    
    # Setup test workspace
    os.makedirs(ws_root, exist_ok=True)
    
    print("\n--- Testing Global Activity Log ---")
    await log_activity(ws, "Test activity")
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_file = ws_root / "logs" / f"{date_str}.md"
    assert log_file.exists(), "Global log file not created"
    content = log_file.read_text()
    assert "Test activity" in content, "Activity not logged"
    print("  [PASS] Global activity log works.")
    
    print("\n--- Testing Slack Reader ---")
    log_dir = ws_root / "slack_logs" / "test-channel"
    log_dir.mkdir(parents=True, exist_ok=True)
    msg_file = log_dir / f"{date_str}.md"
    msg_file.write_text("[10:00:00] <user1> Hello Lucy!")
    messages = await get_local_messages(ws, "2020-01-01T00:00:00Z", ["test-channel"])
    assert "Hello Lucy!" in messages, "Failed to read local slack messages"
    print("  [PASS] Local slack reader works.")

    print("\n--- Testing Depends On ---")
    os.makedirs(ws_root / "crons" / "dep-target", exist_ok=True)
    os.makedirs(ws_root / "crons" / "dep-source", exist_ok=True)
    
    # Write a successful execution log for target
    now = datetime.now(timezone.utc).isoformat()
    (ws_root / "crons" / "dep-target" / "execution.log").write_text(f"## {now} (elapsed: 100ms, status: delivered)\nSuccess!")
    
    # Mock slack client
    class MockSlackClient:
        async def chat_postMessage(self, **kwargs):
            self.last_kwargs = kwargs

    mock_slack = MockSlackClient()
    scheduler = get_scheduler(slack_client=mock_slack)
    
    cron_config = CronConfig(
        path="/dep-source",
        cron="* * * * *",
        title="Source Cron",
        description="print('Hello')",
        workspace_dir=ws_id,
        type="script",
        depends_on="dep-target",
        delivery_mode="channel",
        delivery_channel="C123"
    )
    
    # Create the python script for the cron
    (ws_root / "print('Hello')").write_text("print('Hello')")
    
    await scheduler._run_cron(ws_id, cron_config)
    
    exec_log = (ws_root / "crons" / "dep-source" / "execution.log").read_text()
    assert "status: delivered" in exec_log, "Cron did not execute despite dependency met"
    print("  [PASS] depends_on executes when condition met.")
    
    print("\n--- Testing Block Kit Output ---")
    cron_config_block = CronConfig(
        path="/block-cron",
        cron="* * * * *",
        title="Block Cron",
        description="print('{\"blocks\": [{\"type\": \"section\", \"text\": {\"type\": \"mrkdwn\", \"text\": \"Hello Block\"}}]}')",
        workspace_dir=ws_id,
        type="script",
        delivery_mode="channel",
        delivery_channel="C123"
    )
    (ws_root / "crons" / "block-cron").mkdir(parents=True, exist_ok=True)
    (ws_root / "print('{\"blocks\": [{\"type\": \"section\", \"text\": {\"type\": \"mrkdwn\", \"text\": \"Hello Block\"}}]}')").write_text("print('{\"blocks\": [{\"type\": \"section\", \"text\": {\"type\": \"mrkdwn\", \"text\": \"Hello Block\"}}]}')")
    
    await scheduler._run_cron(ws_id, cron_config_block)
    assert hasattr(mock_slack, 'last_kwargs') and "blocks" in mock_slack.last_kwargs, "Block kit payload not detected or passed"
    print("  [PASS] Block Kit output detection works.")
    
    print("\n--- Testing Max Runs (Self-Deleting) ---")
    cron_config_max = CronConfig(
        path="/max-runs-cron",
        cron="* * * * *",
        title="Max Runs Cron",
        description="print('Hello')",
        workspace_dir=ws_id,
        type="script",
        max_runs=1,
        delivery_mode="channel",
        delivery_channel="C123"
    )
    (ws_root / "crons" / "max-runs-cron").mkdir(parents=True, exist_ok=True)
    
    await scheduler._run_cron(ws_id, cron_config_max)
    # The cron should now be deleted since max_runs=1
    assert not (ws_root / "crons" / "max-runs-cron").exists(), "Cron directory not deleted after max_runs"
    print("  [PASS] Max runs self-deletion works.")
    
    print("\nAll Advanced Cron Features Passed!")

if __name__ == "__main__":
    asyncio.run(test_advanced_features())
