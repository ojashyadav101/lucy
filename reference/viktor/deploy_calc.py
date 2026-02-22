from sdk.tools import viktor_spaces_tools
import asyncio

async def main():
    result = await viktor_spaces_tools.deploy_app(
        project_name="calculator",
        environment="preview",
        commit_message="Basic lightweight calculator app"
    )
    print(f"Success: {result.success}")
    print(f"URL: {result.url}")
    print(f"Vercel URL: {result.vercel_url}")
    if result.error:
        print(f"Error: {result.error}")

asyncio.run(main())
