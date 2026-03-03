import asyncio
import sys
import time

sys.path.insert(0, 'src')
from lucy.core.agent import LucyAgent, AgentContext
from lucy.workspace.connections import get_mcp_connection

async def main():
    agent = LucyAgent()
    ctx = AgentContext(
        workspace_id="8e302095-f4e6-4243-906f-55f6c3bd2583",
        user_name="Ojash",
        user_slack_id="U123456",
        team_id="T043VTH8V4N"
    )

    print("=== STARTING 10 MCP CONNECTION TEST ===")
    
    # 1. Connect to all 10 MCPs
    services = ["weather", "calc", "currency", "time", "translate", "fact", "string", "ip", "stock", "user"]
    for i, svc in enumerate(services):
        port = 8001 + i
        url = f"http://127.0.0.1:{port}/sse"
        print(f"\nConnecting to {svc} MCP at {url}...")
        
        msg = f"Connect to the {svc} MCP server at {url}"
        res = await agent.run(msg, ctx)
        print(f"Lucy: {res[:200]}...")
        
        # Verify connection
        conn = await get_mcp_connection(ctx.workspace_id, svc)
        if conn:
            print(f"  -> Successfully connected to {svc}")
        else:
            print(f"  -> Failed to connect to {svc}")
            
    print("\n=== TESTING INTER-WORKFLOW (CHAINING MCPS) ===")
    complex_query = (
        "Using the MCPs you just connected: "
        "1. Get the weather in Tokyo. "
        "2. Get the stock price of AAPL. "
        "3. Translate the weather result to Spanish. "
        "4. Calculate 15 * 42. "
        "Give me a final summary of all these things."
    )
    
    print(f"Sending complex query: {complex_query}")
    t0 = time.time()
    res = await agent.run(complex_query, ctx)
    t1 = time.time()
    
    print(f"\nLucy's Final Answer (took {t1-t0:.1f}s):")
    print(res)
    
    print("\nTest complete.")

if __name__ == "__main__":
    asyncio.run(main())
