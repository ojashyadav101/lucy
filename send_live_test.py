import asyncio
import json
import ssl
from slack_sdk.web.async_client import AsyncWebClient
import certifi

async def main():
    keys = json.load(open("keys.json"))
    # We want to pose as the user. If we don't have a user token, we'll send it as the bot but maybe that's fine, or we use a user token if available.
    # Actually, the user just said "posing as me", but I can just use the bot token to post a message in the channel, or if there's a user token in keys.json I can use it.
    
    token = keys["slack"].get("user_token", keys["slack"]["bot_token"])
    
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    client = AsyncWebClient(token=token, ssl=ssl_context)
    
    channel_id = "C0AGNRMGALS" # #talk-to-lucy channel from previous scripts
    
    msg = """<@U0AG8LVAB4M> I want you to make a cron job that will be active only for the next 60 minutes. there i want it to tell me 5 different things in gaps of two minutes each. Those five different things have to be:

1. The price of bitcoin and a quick analysis of how it has been performing over the last week.
2. I want you to give me a summary of what emails I did I get today.
3. What is the weather like in Lucknow?
4. Top five AI news headlines this week.
5. I want you to go ahead and see what the new leaderboard on Open Router models looks like."""

    print("Sending message to Slack...")
    response = await client.chat_postMessage(
        channel=channel_id,
        text=msg,
        as_user=True
    )
    print(f"Message sent! Timestamp: {response['ts']}")

if __name__ == "__main__":
    asyncio.run(main())
