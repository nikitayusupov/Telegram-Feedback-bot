"""
Migration script to add is_anonymous field to feedback table.
Run this once to update existing database schema.
"""

import asyncio
import logging
from sqlalchemy import text
from db import async_engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def add_anonymity_field():
    """Add is_anonymous field to feedback table."""
    try:
        async with async_engine.begin() as conn:
            # Check if column already exists
            check_query = text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'feedback' AND column_name = 'is_anonymous'
            """)
            result = await conn.execute(check_query)
            existing_column = result.fetchone()
            
            if existing_column:
                logger.info("Column 'is_anonymous' already exists in feedback table.")
                return
            
            # Add the new column
            alter_query = text("""
                ALTER TABLE feedback 
                ADD COLUMN is_anonymous BOOLEAN DEFAULT FALSE NOT NULL
            """)
            await conn.execute(alter_query)
            logger.info("Successfully added 'is_anonymous' column to feedback table.")
            
    except Exception as e:
        logger.error(f"Error adding anonymity field: {e}")
        raise

async def main():
    """Run the migration."""
    logger.info("Starting anonymity field migration...")
    await add_anonymity_field()
    logger.info("Migration completed successfully!")

if __name__ == "__main__":
    asyncio.run(main()) 