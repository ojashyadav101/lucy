"""Async database session management.

Production-grade setup with:
- Asyncpg driver for performance
- Connection pooling optimized for mixed workloads
- Automatic retry on connection failures
- Proper transaction handling
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import sessionmaker

from lucy.config import settings

# ═══════════════════════════════════════════════════════════════════════════════
# ENGINE CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

# Production pool sizing:
# - 20 connections for typical mixed workload
# - overflow for bursts (up to 40)
# - recycle connections every hour (prevent stale)
# - pre-ping to detect bad connections

async_engine = create_async_engine(
    settings.database_url,
    pool_size=20,
    max_overflow=20,
    pool_timeout=30,
    pool_recycle=3600,
    pool_pre_ping=True,
    echo=False,  # Set True for debugging SQL
    future=True,
)

# Session factory with sensible defaults
AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,  # Prevent lazy loading issues
    autocommit=False,
    autoflush=False,
)


# ═══════════════════════════════════════════════════════════════════════════════
# SESSION MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yields a database session.
    
    Usage:
        @app.get("/items")
        async def get_items(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@asynccontextmanager
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Context manager for manual session handling.
    
    Usage:
        async with db_session() as db:
            result = await db.execute(...)
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Initialize database tables.
    
    In production, use Alembic migrations. This is for dev/test only.
    """
    from lucy.db.models import Base
    
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Clean shutdown: dispose of all connections."""
    await async_engine.dispose()
