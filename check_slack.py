import asyncio
import json
import httpx
import certifi

async def main():
    keys = json.load(open("keys.json"))
    bot_token = keys["slack"]["bot_token"]
    
    headers = {
        "Authorization": f"Bearer {bot_token}",
        "Content-Type": "application/json",
    }
    
    async with httpx.AsyncClient(verify=certifi.where()) as client:
        r = await client.get(
            "https://slack.com/api/conversations.history",
            headers=headers,
            params={"channel": "C0AGNRMGALS", "limit": 10},
        )
        data = r.json()
        for msg in data.get("messages", []):
            print(f"User: {msg.get('user', msg.get('bot_id'))}")
            print(f"Text: {msg.get('text')}")
            print("-" * 40)

if __name__ == "__main__":
    asyncio.run(main())
