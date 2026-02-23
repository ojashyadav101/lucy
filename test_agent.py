import asyncio
import structlog
from lucy.core.agent import get_agent, AgentContext

async def main():
    agent = get_agent()
    # A dummy context
    ctx = AgentContext(workspace_id="test_workspace")
    
    print("Sending message to Lucy...")
    tools = await agent._get_meta_tools("test_workspace")
    print("Available tools:")
    for t in tools:
        print(t.get("function", {}).get("name"))

    response = await agent.run(
        message="List all the files in the current directory using the remote bash tool.",
        ctx=ctx,
        slack_client=None
    )
    
    print("\n=== LUCY'S RESPONSE ===")
    print(response)
    print("=======================")

if __name__ == "__main__":
    asyncio.run(main())
