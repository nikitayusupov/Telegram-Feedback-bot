#!/usr/bin/env python3
"""
Migration script to add tg_user_id field to the Curator table.

This enables curators to receive notifications about new feedback from students.
"""

import asyncio
import logging
from sqlalchemy import text
from db import async_session

logger = logging.getLogger(__name__)

async def add_curator_user_id_field():
    """Add tg_user_id field to the Curator table."""
    async with async_session() as session:
        try:
            # Check if the column already exists
            check_query = text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'curator' AND column_name = 'tg_user_id'
            """)
            result = await session.execute(check_query)
            existing_column = result.scalar()
            
            if existing_column:
                logger.info("Column 'tg_user_id' already exists in 'curator' table. Skipping migration.")
                return
            
            # Add the column
            alter_query = text("""
                ALTER TABLE curator 
                ADD COLUMN tg_user_id BIGINT UNIQUE
            """)
            await session.execute(alter_query)
            
            # Create index on the new column
            index_query = text("""
                CREATE INDEX ix_curator_tg_user_id ON curator (tg_user_id)
            """)
            await session.execute(index_query)
            
            await session.commit()
            logger.info("Successfully added 'tg_user_id' column to 'curator' table with index.")
            
        except Exception as e:
            logger.error(f"Error during curator user_id migration: {e}")
            await session.rollback()
            raise

async def main():
    """Run the migration."""
    logging.basicConfig(level=logging.INFO)
    logger.info("Starting curator user_id migration...")
    
    try:
        await add_curator_user_id_field()
        logger.info("Migration completed successfully!")
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main()) 