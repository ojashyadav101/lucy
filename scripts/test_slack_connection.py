#!/usr/bin/env python3
"""Test Slack connection and basic functionality.

This script verifies:
1. Slack API credentials are valid
2. Bot can fetch team info
3. Bot can send test message (optional)

Usage:
    python scripts/test_slack_connection.py
    python scripts/test_slack_connection.py --send-test  # Send actual test message
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from slack_bolt.async_app import AsyncApp
from slack_sdk.errors import SlackApiError

from lucy.config import settings
import structlog

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
)
logger = structlog.get_logger()


async def test_connection() -> int:
    """Test Slack API connection."""
    logger.info("testing_slack_connection")
    
    app = AsyncApp(
        token=settings.slack_bot_token,
        signing_secret=settings.slack_signing_secret,
    )
    
    try:
        # Test 1: Auth test
        logger.info("testing_auth")
        auth_result = await app.client.auth_test()
        logger.info(
            "auth_success",
            team=auth_result.get("team"),
            user=auth_result.get("user"),
            user_id=auth_result.get("user_id"),
        )
        
        # Test 2: Fetch team info
        logger.info("fetching_team_info")
        team_info = await app.client.team_info()
        team = team_info.get("team", {})
        logger.info(
            "team_info",
            name=team.get("name"),
            domain=team.get("domain"),
            id=team.get("id"),
        )
        
        # Test 3: Fetch bot user info
        logger.info("fetching_bot_info")
        users_info = await app.client.users_info(user=auth_result.get("user_id"))
        user = users_info.get("user", {})
        logger.info(
            "bot_info",
            name=user.get("name"),
            real_name=user.get("real_name"),
        )
        
        return 0
        
    except SlackApiError as e:
        logger.error("slack_api_error", error=str(e))
        return 1
    except Exception as e:
        logger.error("unexpected_error", error=str(e))
        return 1


async def send_test_message(channel: str) -> int:
    """Send a test message to verify posting works."""
    logger.info("sending_test_message", channel=channel)
    
    app = AsyncApp(
        token=settings.slack_bot_token,
        signing_secret=settings.slack_signing_secret,
    )
    
    try:
        result = await app.client.chat_postMessage(
            channel=channel,
            text="🤝 Lucy test message — connection successful!",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "🤝 *Lucy Test*\n\nConnection successful! I'm ready to help.",
                    },
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": "Try: `@Lucy hello`",
                        }
                    ],
                },
            ],
        )
        
        logger.info("message_sent", ts=result.get("ts"))
        return 0
        
    except SlackApiError as e:
        logger.error("failed_to_send_message", error=str(e))
        return 1
    except Exception as e:
        logger.error("unexpected_error", error=str(e))
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Test Slack connection")
    parser.add_argument("--send-test", action="store_true", help="Send actual test message")
    parser.add_argument("--channel", default="#general", help="Channel to send test message")
    args = parser.parse_args()
    
    if args.send_test:
        return asyncio.run(send_test_message(args.channel))
    else:
        return asyncio.run(test_connection())


if __name__ == "__main__":
    sys.exit(main())
