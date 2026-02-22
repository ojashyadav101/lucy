#!/usr/bin/env python
"""Add ThreadConversation table for smart thread tracking."""

import asyncio
import sys
sys.path.insert(0, "src")

from sqlalchemy import text
from lucy.db.session import AsyncSessionLocal


async def add_thread_table():
    """Create the thread_conversations table."""
    async with AsyncSessionLocal() as db:
        # Check if table exists
        result = await db.execute(
            text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'thread_conversations'
                )
            """)
        )
        exists = result.scalar()
        
        if exists:
            print("✓ Table 'thread_conversations' already exists")
            return
        
        # Create table
        await db.execute(
            text("""
                CREATE TABLE thread_conversations (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                    channel_id UUID NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
                    slack_channel_id VARCHAR(32) NOT NULL,
                    thread_ts VARCHAR(50) NOT NULL,
                    initiator_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
                    slack_initiator_id VARCHAR(32) NOT NULL,
                    is_active BOOLEAN DEFAULT TRUE,
                    status VARCHAR(20) DEFAULT 'active' NOT NULL,
                    participant_slack_ids JSONB DEFAULT '[]'::jsonb,
                    message_count INTEGER DEFAULT 1,
                    last_message_at TIMESTAMP WITH TIME ZONE NOT NULL,
                    lucy_last_responded_at TIMESTAMP WITH TIME ZONE,
                    auto_close_after_minutes INTEGER DEFAULT 30,
                    last_intent VARCHAR(50),
                    conversation_summary TEXT,
                    last_task_id UUID REFERENCES tasks(id) ON DELETE SET NULL,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
                    closed_at TIMESTAMP WITH TIME ZONE
                )
            """)
        )
        
        # Create indexes
        await db.execute(
            text("""
                CREATE UNIQUE INDEX uix_thread_channel_thread 
                ON thread_conversations (channel_id, thread_ts)
            """)
        )
        await db.execute(
            text("""
                CREATE INDEX ix_threads_workspace_active 
                ON thread_conversations (workspace_id, is_active)
            """)
        )
        await db.execute(
            text("""
                CREATE INDEX ix_threads_last_activity 
                ON thread_conversations (last_message_at)
            """)
        )
        
        await db.commit()
        print("✓ Created 'thread_conversations' table with indexes")


if __name__ == "__main__":
    asyncio.run(add_thread_table())
