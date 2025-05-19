#!/usr/bin/env python3
"""Script to wipe all tables from the database while preserving structure."""

import asyncio
import logging
from sqlalchemy.schema import DropTable, MetaData
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import inspect, text
from config import settings

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger("db_reset")

async def reset_database():
    """Drops all tables in the database, preserving the database itself."""
    logger.info(f"Connecting to database: {settings.database_url}")
    
    # Create engine
    engine = create_async_engine(settings.database_url)
    
    try:
        # Create connection
        async with engine.begin() as conn:
            # Get all table names using run_sync
            table_names = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
            
            if not table_names:
                logger.info("No tables found in database.")
                return
            
            logger.info(f"Found {len(table_names)} tables: {', '.join(table_names)}")
            
            # Create MetaData reflecting existing schema
            metadata = MetaData()
            await conn.run_sync(lambda sync_conn: metadata.reflect(bind=sync_conn))
            
            # First try directly dropping all tables
            try:
                # Create SQL to drop all tables
                drop_query = "DROP TABLE IF EXISTS "
                drop_query += ", ".join(f'"{table.name}"' for table in reversed(metadata.sorted_tables))
                drop_query += " CASCADE;"
                
                logger.info("Attempting to drop all tables with a single CASCADE command")
                await conn.execute(text(drop_query))
                logger.info("All tables dropped successfully in one operation!")
            except Exception as e:
                logger.warning(f"Bulk drop failed: {str(e)}")
                logger.info("Falling back to individual table drops...")
                
                # If bulk drop fails, try individually dropping each table with CASCADE
                for table in reversed(metadata.sorted_tables):
                    try:
                        logger.info(f"Dropping table: {table.name}")
                        await conn.execute(text(f'DROP TABLE IF EXISTS "{table.name}" CASCADE;'))
                    except Exception as e:
                        logger.error(f"Could not drop table {table.name}: {str(e)}")
            
            logger.info("Database reset complete!")
            logger.info("Next time you start the bot, tables will be recreated fresh.")
    except Exception as e:
        logger.error(f"Error resetting database: {e}")
        raise
    finally:
        await engine.dispose()

if __name__ == "__main__":
    asyncio.run(reset_database()) 