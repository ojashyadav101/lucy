from sdk.tools import slack_admin_tools
import asyncio

async def main():
    result = await slack_admin_tools.coworker_list_slack_channels()
    for ch in result.channels:
        if 'lucy' in ch['name'].lower() or 'my-ai' in ch['name'].lower():
            print(f"#{ch['name']} - ID: {ch['id']} - access: {ch['bot_has_access']}")

asyncio.run(main())
