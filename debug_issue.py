import asyncio
import os
import sys
from pathlib import Path
import json

sys.path.insert(0, str(Path(__file__).parent / "src"))

from lucy.slack.handlers import _handle_message

class MockContext(dict):
    pass

class MockSay:
    def __init__(self):
        self.messages = []
    async def __call__(self, **kwargs):
        print(f"SAY CALLED: {kwargs}")
        self.messages.append(kwargs)

async def main():
    keys = json.load(open("keys.json"))
    bot_token = keys["slack"]["bot_token"]
    
    from slack_sdk.web.async_client import AsyncWebClient
    import ssl
    import certifi
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    client = AsyncWebClient(token=bot_token, ssl=ssl_context)
    
    say = MockSay()
    ctx = MockContext({"workspace_id": "8e302095-f4e6-4243-906f-55f6c3bd2583"})
    
    prompt = "I want you to make a cron job that will be active only for the next ten minutes. In each of those ten minutes, I want you to tell me three different things, or let's say five different things, in gaps of two minutes each. Those five different things have to be:\n1. The price of bitcoin and a quick analysis of how it has been performing over the last week.\n2. I want you to give me a summary of what emails I did I get today.\n3. What is the weather like in Lucknow?\n4. Top five AI news headlines this week.\n5. I want you to go ahead and see what the new leaderboard on Open Router models looks like."
    
    await _handle_message(
        text=prompt,
        channel_id="C0AGNRMGALS",
        thread_ts=None,
        event_ts=None,
        say=say,
        client=client,
        context=ctx,
        user_id="U0442G9T150"
    )

if __name__ == "__main__":
    asyncio.run(main())
