"""
db.py
~~~~~~~~~~~~~~~~~~
Lightweight database helper layer.

• Provides `async_session()` – an **async context manager** yielding an AsyncSession.
  Usage:
  ```python
  from feedback_bot.db import async_session
  async with async_session() as session:
      result = await session.exec(select(User))
  ```

• Automatically calls `create_all_tables()` on first import (safe for SQLite; does nothing
  if tables already exist).

• Re-exports core objects so other modules can just
  `from feedback_bot.db import async_engine, create_all_tables`.
"""

from __future__ import annotations

import contextlib
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from models import async_engine, create_all_tables

# ---------------------------------------------------------------------------
# Session factory                                                            
# ---------------------------------------------------------------------------
async_session_factory = async_sessionmaker(
    async_engine, expire_on_commit=False, autoflush=False
)

@contextlib.asynccontextmanager
async def async_session() -> AsyncGenerator[AsyncSession, None]:
    """Async context manager: `async with async_session() as s:`"""
    async with async_session_factory() as session:
        yield session


__all__ = [
    "async_engine",
    "async_session",
    "create_all_tables",
]
