import asyncio
import sys
import time
import random
import json
from pathlib import Path

sys.path.insert(0, 'src')
from lucy.core.agent import LucyAgent, AgentContext
from lucy.workspace.connections import get_mcp_connection
from lucy.workspace.filesystem import WorkspaceFS
from lucy.config import settings

QUERIES = [
    "Get the weather in London, convert the temperature to Celsius if it isn't already, and translate the result to French.",
    "What is the stock price of MSFT? Also, calculate 150 * 2.5.",
    "Get a random fact about space, reverse the string, and tell me the time in Tokyo.",
    "Look up user 42, get their IP info (assume IP 8.8.8.8), and translate the IP info to German.",
    "Convert 100 USD to EUR, add 50 to the result, and get the weather in Paris.",
]

async def main():
    agent = LucyAgent()
    ctx = AgentContext(
        workspace_id="8e302095-f4e6-4243-906f-55f6c3bd2583",
        user_name="Ojash",
        user_slack_id="U123456",
        team_id="T043VTH8V4N"
    )
    ws = WorkspaceFS(ctx.workspace_id, base_path=Path(settings.workspace_root))

    print("=== STARTING CONTINUOUS STRESS TEST ===")
    
    # Ensure connections exist
    services = ["weather", "calc", "currency", "time", "translate", "fact", "string", "ip", "stock", "user"]
    for i, svc in enumerate(services):
        conn = await get_mcp_connection(ws, svc)
        if not conn:
            port = 8001 + i
            url = f"http://127.0.0.1:{port}/sse"
            print(f"Connecting {svc} at {url}...")
            await agent.run(f"Connect to the {svc} MCP server at {url}", ctx)
    
    print("All MCPs connected. Starting stress test loop...")
    
    success_count = 0
    total_count = 0
    
    with open("stress_test_results.log", "a") as f:
        for i in range(20):
            q = random.choice(QUERIES)
            print(f"\n[{i+1}/20] Query: {q}")
            t0 = time.time()
            
            try:
                res = await agent.run(q, ctx)
                t1 = time.time()
                print(f"Response ({t1-t0:.1f}s): {res[:150]}...")
                
                log_entry = {
                    "iteration": i + 1,
                    "query": q,
                    "response": res,
                    "duration_s": round(t1 - t0, 1),
                    "status": "success" if "error" not in res.lower() else "error"
                }
                f.write(json.dumps(log_entry) + "\n")
                f.flush()
                
                if "error" not in res.lower():
                    success_count += 1
            except Exception as e:
                print(f"Error during run: {e}")
                f.write(json.dumps({"iteration": i+1, "query": q, "error": str(e)}) + "\n")
                
            total_count += 1
            
            # Sleep a bit to avoid rate limits
            await asyncio.sleep(5)
            
    print(f"\n=== STRESS TEST COMPLETE ===")
    print(f"Success Rate: {success_count}/{total_count} ({(success_count/total_count)*100:.1f}%)")

if __name__ == "__main__":
    asyncio.run(main())
