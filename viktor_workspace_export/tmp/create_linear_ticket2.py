import asyncio
from sdk.tools.mcp_linear import linear_create_issue

async def main():
    result = await linear_create_issue(
        team="85af8f8d-4910-44e1-a166-18ab63a1fd26",
        title="Auto cleanup not working for stale pending workspaces",
        description="""## Bug Report

**Reported by:** Ojash (via Slack #mentions)

### Description
The auto cleanup mechanism for stale/abandoned workspaces is not working. Multiple "Untitled Workspace" entries with `pending.setup` status are persisting in the workspace list instead of being automatically cleaned up.

### Screenshot
The workspace switcher shows at least 5 "Untitled Workspace" entries all stuck in `pending.setup` state that should have been cleaned up automatically.

### Context from Team Discussion
- Naman suspects this may have broken after the **2.0 merge**
- The cleanup was originally based on **heartbeat-based logic**
- Pankaj built this as part of the onboarding flow (per Shashwat)
- Needs investigation into what changed in 2.0 that affected the cleanup process

### Expected Behavior
Stale workspaces that remain in `pending.setup` state should be automatically cleaned up after a timeout period.

### Actual Behavior
Workspaces in `pending.setup` status persist indefinitely without being cleaned up.

### Suggested Investigation
1. Check if the heartbeat-based cleanup logic is still active post-2.0 merge
2. Verify the cleanup cron/job is running
3. Review 2.0 merge changes that may have affected workspace lifecycle management
4. Check with Pankaj on the original onboarding cleanup implementation""",
        labels=["45d3e98d-9f74-446d-8259-529667a8b378"],
        priority=2,
        state="980f7fcc-29e1-4196-bb2a-ebf2939e85ee",
        project="1cc461ae-4cc1-4892-80dd-b1f03b8ded94"
    )
    print(result)

asyncio.run(main())
