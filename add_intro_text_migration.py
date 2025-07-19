"""
Migration script to add intro_text field to survey table.
Run this once to update existing database schema.
"""

import asyncio
import logging
from sqlalchemy import text
from db import async_engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def add_intro_text_field():
    """Add intro_text field to survey table."""
    try:
        async with async_engine.begin() as conn:
            # Check if column already exists
            check_query = text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'survey' AND column_name = 'intro_text'
            """)
            result = await conn.execute(check_query)
            existing_column = result.fetchone()
            
            if existing_column:
                logger.info("Column 'intro_text' already exists in survey table.")
                return
            
            # Add the new column
            alter_query = text("""
                ALTER TABLE survey 
                ADD COLUMN intro_text TEXT
            """)
            await conn.execute(alter_query)
            logger.info("Successfully added 'intro_text' column to survey table.")
            
    except Exception as e:
        logger.error(f"Error adding intro_text field: {e}")
        raise

async def main():
    """Run the migration."""
    logger.info("Starting intro_text field migration...")
    await add_intro_text_field()
    logger.info("Migration completed successfully!")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        raise