"""
models.py
~~~~~~~~~~~~~~~~~~~~~~

SQLModel data-layer for the Feedback-Bot project.

• `async_engine`           – lazy-created AsyncEngine
• `create_all_tables()`    – coroutine to create tables if they don't exist
• Declarative classes:
      Course, Group, Student, GroupStudent,
      Question, Survey, Response, Feedback
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import (
    SQLModel,
    Field,
)
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import UniqueConstraint, Column, BigInteger
from sqlalchemy.dialects.postgresql import TIMESTAMP

# Import settings to access database_url
from config import settings

# --------------------------------------------------------------------------- #
# Configuration                                                               #
# --------------------------------------------------------------------------- #

DB_URL = settings.database_url

# --------------------------------------------------------------------------- #
# Enum helpers                                                                #
# --------------------------------------------------------------------------- #
class QuestionType(str, enum.Enum):
    scale = "scale"
    text = "text"

# --------------------------------------------------------------------------- #
# Core tables                                                                 #
# --------------------------------------------------------------------------- #

class Course(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)

class Group(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    course_id: int = Field(foreign_key="course.id")
    
    __table_args__ = (UniqueConstraint("name", "course_id", name="uq_group_name_course_id"),)


class Curator(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    tg_username: str = Field(unique=True, index=True)


class CuratorGroup(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    curator_id: int = Field(foreign_key="curator.id")
    group_id: int = Field(foreign_key="group.id")


class CuratorCourse(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    curator_id: int = Field(foreign_key="curator.id")
    course_id: int = Field(foreign_key="course.id")


class Student(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    tg_username: str = Field(index=True, unique=True)
    tg_user_id: Optional[int] = Field(
        default=None, 
        sa_column=Column(BigInteger, unique=True, index=True)
    )


class GroupStudent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    group_id: int = Field(foreign_key="group.id")
    student_id: int = Field(foreign_key="student.id")


class Question(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    survey_id: int = Field(foreign_key="survey.id")
    text: str
    q_type: QuestionType = Field(sa_column_kwargs={"default": QuestionType.scale})
    order: int


class Survey(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    group_id: int = Field(foreign_key="group.id")
    title: str = Field(default="", max_length=1000)
    started_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(TIMESTAMP(timezone=True), nullable=False)
    )


class Response(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    survey_id: int = Field(index=True)
    student_tg_id: int = Field(sa_column=Column(BigInteger, index=True))
    student_tg_username: str = Field(default="")
    course_name: str = Field(default="")
    group_name: str = Field(default="")
    survey_title: str = Field(default="")
    question_text: str = Field(default="")
    question_type: QuestionType
    answer: str
    answered_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(TIMESTAMP(timezone=True), nullable=False)
    )


class Feedback(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    student_tg_id: int = Field(sa_column=Column(BigInteger, index=True))
    student_tg_username: str = Field(default="")
    course_name: str = Field(default="")
    topic: str
    text: str
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(TIMESTAMP(timezone=True), nullable=False)
    )


# --------------------------------------------------------------------------- #
# Engine helpers                                                              #
# --------------------------------------------------------------------------- #
async_engine = create_async_engine(DB_URL, echo=False)


async def create_all_tables() -> None:
    """Create database schema (run once on startup)."""
    async with async_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


__all__ = [
    # tables
    "Course",
    "Group",
    "Student",
    "GroupStudent",
    "Question",
    "Survey",
    "Response",
    "Feedback",
    "QuestionType",
    # engine utilities
    "async_engine",
    "create_all_tables",
]
