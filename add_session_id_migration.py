"""
Migration script to add session_id field to response table.
Run this once to update existing database schema.
"""

import asyncio
import logging
from sqlalchemy import text
from db import async_engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def add_session_id_field():
    """Add session_id field to response table."""
    try:
        async with async_engine.begin() as conn:
            # Check if column already exists
            check_query = text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'response' AND column_name = 'session_id'
            """)
            result = await conn.execute(check_query)
            existing_column = result.fetchone()
            
            if existing_column:
                logger.info("Column 'session_id' already exists in response table.")
                return
            
            # Add the new column
            alter_query = text("""
                ALTER TABLE response 
                ADD COLUMN session_id TEXT
            """)
            await conn.execute(alter_query)
            logger.info("Successfully added 'session_id' column to response table.")
            
    except Exception as e:
        logger.error(f"Error adding session_id field: {e}")
        raise

async def main():
    """Run the migration."""
    logger.info("Starting session_id field migration...")
    await add_session_id_field()
    logger.info("Migration completed successfully!")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        raise 