import asyncio
import os
import sys
from pathlib import Path

# Setup paths
sys.path.insert(0, str(Path(__file__).parent / "src"))

from lucy.crons.scheduler import CronConfig, get_scheduler
from lucy.workspace.filesystem import get_workspace


async def run_tests():
    workspace_id = "test_workspace_cron_features"
    ws = get_workspace(workspace_id)
    
    # Create mock script
    script_path = ws.root / "test_script.py"
    script_path.write_text("print('Hello from script cron!')")
    
    # Create condition script
    condition_path = ws.root / "condition.py"
    condition_path.write_text("import sys; sys.exit(1)  # Skip execution")
    
    scheduler = get_scheduler()
    
    # Test 1: Script execution
    print("Testing Script Execution...")
    cron1 = CronConfig(
        path="/test-script-cron",
        cron="* * * * *",
        title="Test Script Cron",
        description=f"Script: {script_path.name}",
        workspace_dir=workspace_id,
        type="script",
    )
    
    try:
        await scheduler._run_cron(workspace_id, cron1)
        log = await ws.read_file("crons/test-script-cron/execution.log")
        if log and "Hello from script cron!" in log:
            print("  [PASS] Script execution successful and output logged.")
        else:
            print(f"  [FAIL] Output not found in log: {log}")
    except Exception as e:
        print(f"  [FAIL] Exception during script execution: {e}")
        
    # Test 2: Condition Script
    print("Testing Condition Script...")
    cron2 = CronConfig(
        path="/test-condition-cron",
        cron="* * * * *",
        title="Test Condition Cron",
        description="Run something.",
        workspace_dir=workspace_id,
        condition_script_path=condition_path.name,
    )
    
    try:
        await scheduler._run_cron(workspace_id, cron2)
        log = await ws.read_file("crons/test-condition-cron/execution.log")
        if not log:
            print("  [PASS] Cron correctly skipped due to condition script exit code 1.")
        else:
            print(f"  [FAIL] Cron executed despite condition script: {log}")
    except Exception as e:
        print(f"  [FAIL] Exception during condition check: {e}")

if __name__ == "__main__":
    asyncio.run(run_tests())
