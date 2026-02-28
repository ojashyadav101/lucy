#!/usr/bin/env python3
"""Initialize Lucy database.

Usage:
    python scripts/init_db.py              # Create tables (dev only)
    python scripts/init_db.py --migrate     # Run Alembic migrations (production)
    python scripts/init_db.py --reset       # Drop and recreate (DANGER)
"""

import argparse
import asyncio
import sys

# Add src to path
sys.path.insert(0, "src")

from sqlalchemy import text

from lucy.db.session import async_engine, init_db, close_db
from lucy.db.models import Base
from lucy.config import settings


async def create_extensions() -> None:
    """Create PostgreSQL extensions."""
    async with async_engine.begin() as conn:
        # uuid-ossp for UUID generation (optional, we use Python uuid4)
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\""))
        # pgcrypto for encryption (future: encrypted credentials)
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS \"pgcrypto\""))
        print("✓ PostgreSQL extensions created")


async def drop_tables() -> None:
    """Drop all tables (DANGER)."""
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    print("✓ All tables dropped")


async def create_tables() -> None:
    """Create all tables from models (dev only)."""
    await init_db()
    print("✓ Tables created from models")


async def run_migrations() -> None:
    """Run Alembic migrations (production)."""
    import alembic.config
    import alembic.command

    alembic_cfg = alembic.config.Config("alembic.ini")
    alembic.command.upgrade(alembic_cfg, "head")
    print("✓ Alembic migrations applied")


async def verify_connection() -> None:
    """Test database connection."""
    async with async_engine.connect() as conn:
        result = await conn.execute(text("SELECT version()"))
        version = result.scalar()
        print(f"✓ Connected to: {version}")

        result = await conn.execute(text("SELECT current_database(), current_user"))
        row = result.fetchone()
        print(f"✓ Database: {row[0]}, User: {row[1]}")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize Lucy database")
    parser.add_argument("--migrate", action="store_true", help="Run Alembic migrations")
    parser.add_argument("--reset", action="store_true", help="Drop and recreate tables (DANGER)")
    args = parser.parse_args()

    print(f"Database URL: {settings.database_url.replace(':lucy@', ':****@')}")
    print()

    try:
        # Test connection first
        await verify_connection()
        print()

        if args.reset:
            print("⚠️  DANGER: Dropping all tables...")
            await drop_tables()
            print()

        if args.migrate:
            await create_extensions()
            await run_migrations()
        else:
            await create_extensions()
            await create_tables()

        print()
        print("✅ Database initialization complete!")
        return 0

    except Exception as e:
        print(f"❌ Error: {e}")
        return 1

    finally:
        await close_db()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
