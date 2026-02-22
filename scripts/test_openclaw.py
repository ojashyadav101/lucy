#!/usr/bin/env python3
"""Test OpenClaw connection and basic functionality.

This script verifies:
1. OpenClaw gateway is reachable (167.86.82.46:18791)
2. Health check passes
3. Chat completion works with OpenAI-compatible API

Usage:
    python scripts/test_openclaw.py
"""

import asyncio
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import structlog
from lucy.core.openclaw import OpenClawClient, ChatConfig, OpenClawError
from lucy.config import settings

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
)
logger = structlog.get_logger()


async def test_openclaw() -> int:
    """Run all OpenClaw tests."""
    logger.info("testing_openclaw", base_url=settings.openclaw_base_url)
    
    client = OpenClawClient()
    
    try:
        # Test 1: Health check
        logger.info("test_1_health_check")
        health = await client.health_check()
        logger.info("health_check_passed", status=health.get("status"))
        
        # Test 2: Chat completion
        logger.info("test_2_chat_completion")
        fake_workspace_id = uuid4()
        fake_user_id = uuid4()
        
        response = await client.chat_completion(
            messages=[{"role": "user", "content": "Say 'OpenClaw connection successful' and nothing else."}],
            config=ChatConfig(
                model="kimi",
                system_prompt="You are Lucy, a helpful AI coworker. Be concise.",
                max_tokens=500,
            ),
            workspace_id=fake_workspace_id,
            user_id=fake_user_id,
        )
        
        logger.info(
            "message_sent",
            content_length=len(response.content),
            has_tool_calls=response.tool_calls is not None,
        )
        
        print()
        print("=" * 50)
        print("RESPONSE FROM OPENCLAW:")
        print("-" * 50)
        print(response.content)
        print("=" * 50)
        print()
        
        logger.info("all_tests_passed")
        return 0
        
    except OpenClawError as e:
        logger.error("openclaw_error", error=str(e), status_code=e.status_code)
        print()
        print(f"❌ OpenClaw error: {e}")
        if e.status_code:
            print(f"   HTTP status: {e.status_code}")
        print()
        print("Troubleshooting:")
        print("  1. Check if OpenClaw gateway is running on VPS")
        print("     $ ssh root@167.86.82.46 'systemctl status openclaw-lucy'")
        print()
        print("  2. Verify API is enabled in openclaw.json:")
        print("     $ ssh root@167.86.82.46 'cat /home/lucy-oclaw/.openclaw/openclaw.json'")
        print("     Ensure it contains: \"api_enabled\": true")
        print()
        print("  3. Check firewall/iptables rules on VPS:")
        print("     $ ssh root@167.86.82.46 'iptables -t nat -L PREROUTING'")
        print()
        print("  4. View OpenClaw logs:")
        print("     $ ssh root@167.86.82.46 'journalctl -u openclaw-lucy -f'")
        return 1
        
    except Exception as e:
        logger.error("unexpected_error", error=str(e))
        print()
        print(f"❌ Unexpected error: {e}")
        return 1
        
    finally:
        await client.close()


def main() -> int:
    print()
    print("🤝 Lucy OpenClaw Connection Test")
    print(f"   Gateway: {settings.openclaw_base_url}")
    print()
    
    return asyncio.run(test_openclaw())


if __name__ == "__main__":
    sys.exit(main())
