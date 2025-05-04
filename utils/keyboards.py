from typing import Optional

from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlmodel import select

from db import async_session
from models import Course, Group


async def get_course_selection_keyboard(callback_prefix: str) -> Optional[InlineKeyboardBuilder]:
    """Queries courses and returns an InlineKeyboardBuilder for selection.

    Args:
        callback_prefix: A unique prefix for callback data to route correctly.

    Returns:
        An InlineKeyboardBuilder instance or None if no courses exist.
    """
    async with async_session() as session:
        result = await session.execute(select(Course).order_by(Course.name))
        courses = result.scalars().all()

    if not courses:
        return None

    builder = InlineKeyboardBuilder()
    for course in courses:
        builder.add(InlineKeyboardButton(
            text=course.name,
            callback_data=f"{callback_prefix}:{course.id}"
        ))
    # Adjust layout if many courses
    builder.adjust(2) # Example: 2 columns
    return builder


async def get_confirmation_keyboard(yes_callback: str, no_callback: str) -> InlineKeyboardBuilder:
    """Creates a simple Yes/Cancel inline keyboard.

    Args:
        yes_callback: Callback data string for the 'Yes' button.
        no_callback: Callback data string for the 'Cancel' button.

    Returns:
        An InlineKeyboardBuilder instance.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да", callback_data=yes_callback)
    builder.button(text="❌ Отмена", callback_data=no_callback)
    builder.adjust(2)
    return builder


async def get_group_selection_keyboard(course_id: int, callback_prefix: str) -> Optional[InlineKeyboardBuilder]:
    """Queries groups for a specific course and returns an InlineKeyboardBuilder.

    Args:
        course_id: The ID of the course to fetch groups for.
        callback_prefix: A unique prefix for callback data.

    Returns:
        An InlineKeyboardBuilder instance or None if no groups exist for the course.
    """
    async with async_session() as session:
        result = await session.execute(
            select(Group)
            .where(Group.course_id == course_id)
            .order_by(Group.name)
        )
        groups = result.scalars().all()

    if not groups:
        return None

    builder = InlineKeyboardBuilder()
    for group in groups:
        builder.add(InlineKeyboardButton(
            text=group.name,
            callback_data=f"{callback_prefix}:{group.id}"
        ))
    builder.adjust(2) # Adjust layout
    return builder


def get_question_type_keyboard(current_question_count: int, max_questions: int) -> InlineKeyboardBuilder:
    """Creates the keyboard for selecting question type (Scale/Text) or finishing.

    Args:
        current_question_count: The current number of questions.
        max_questions: The maximum number of questions.

    Returns:
        An InlineKeyboardBuilder instance.
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📊 Шкала (1-10)", callback_data="sq_qtype:scale"),
        InlineKeyboardButton(text="📝 Текст", callback_data="sq_qtype:text")
    )
    # Add finish button if below the max question limit
    if current_question_count < max_questions:
        builder.row(InlineKeyboardButton(text="✅ Завершить и сохранить", callback_data="sq_qtype:finish"))
    return builder


def get_scale_keyboard() -> InlineKeyboardBuilder:
    """Creates the keyboard for scale (1-10) questions."""
    builder = InlineKeyboardBuilder()
    buttons = [InlineKeyboardButton(text=str(i), callback_data=f"survey_answer:{i}") for i in range(1, 11)]
    # Arrange buttons (e.g., 5 per row)
    builder.row(*buttons[:5])
    builder.row(*buttons[5:])
    # Add skip button
    builder.row(InlineKeyboardButton(text="Пропустить", callback_data="survey_action:skip"))
    return builder


def get_skip_keyboard() -> InlineKeyboardBuilder:
    """Creates a keyboard with only a Skip button."""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Пропустить", callback_data="survey_action:skip"))
    return builder 