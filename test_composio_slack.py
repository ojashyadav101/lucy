import asyncio
from composio import Composio

async def main():
    composio = Composio(api_key="ak_IfLg3d5wH3adb4LS2-ZQ")
    # Using a known user id or workspace id. Let's try the workspace_id that was used before or default.
    # The default we used in onboarding is 'test_workspace'. But what is the actual workspace ID?
    # Let's list all connections across the whole account.
    connections = composio.connections.get()
    for conn in connections:
        print(f"Connection: {conn.appName}, Status: {conn.status}, Expected: {conn.expectedCredentials}")

    # Let's see if we have a Slack user connection
    slack_conns = [c for c in connections if c.appName.lower() == "slack"]
    for c in slack_conns:
        print(f"Slack connection id: {c.id}")

if __name__ == "__main__":
    asyncio.run(main())
