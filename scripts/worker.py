#!/usr/bin/env python3
"""Background worker for processing Lucy tasks.

This worker polls the database for pending tasks and executes them
via OpenClaw. Runs independently of the Slack bot.

Usage:
    python scripts/worker.py              # Run with default 5s poll interval
    python scripts/worker.py --interval 10  # 10 second poll interval
    python scripts/worker.py --once         # Process one batch and exit
"""

import argparse
import asyncio
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import structlog
from lucy.core.agent import LucyAgent
from lucy.config import settings

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer()
        if settings.env == "development"
        else structlog.processors.JSONRenderer(),
    ],
)
logger = structlog.get_logger()


async def run_worker(interval: float, once: bool = False) -> int:
    """Run the background worker.
    
    Args:
        interval: Seconds between polls
        once: If True, process one batch and exit
    
    Returns:
        Exit code (0 = success)
    """
    agent = LucyAgent()
    
    if once:
        logger.info("worker_run_once", interval=interval)
        # Just run one iteration
        await agent.run_worker(poll_interval=interval)
        return 0
    
    logger.info("worker_starting", interval=interval)
    
    # Handle shutdown signals
    def signal_handler(sig: int, frame: Any) -> None:
        logger.info("shutdown_signal_received", signal=sig)
        agent.stop()
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        await agent.run_worker(poll_interval=interval)
        return 0
    except Exception as e:
        logger.error("worker_fatal_error", error=str(e))
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Lucy background task worker"
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Poll interval in seconds (default: 5)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process one batch and exit",
    )
    args = parser.parse_args()
    
    try:
        return asyncio.run(run_worker(args.interval, args.once))
    except KeyboardInterrupt:
        logger.info("worker_interrupted")
        return 0


if __name__ == "__main__":
    sys.exit(main())
