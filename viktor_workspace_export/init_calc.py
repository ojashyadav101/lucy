from sdk.tools import viktor_spaces_tools
import asyncio

async def main():
    result = await viktor_spaces_tools.init_app_project(
        project_name="calculator",
        description="A lightweight calculator web app"
    )
    print(f"Success: {result.success}")
    print(f"Path: {result.sandbox_path}")
    print(f"Dev URL: {result.convex_url_dev}")
    if result.error:
        print(f"Error: {result.error}")

asyncio.run(main())
