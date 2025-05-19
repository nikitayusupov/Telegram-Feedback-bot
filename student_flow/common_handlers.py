# student_flow/common_handlers.py
import logging
from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message
from sqlmodel import select
from aiogram.fsm.context import FSMContext
from aiogram import Bot
from student_flow.survey_handlers import SurveyResponseStates
from utils.auth_checks import get_user_role, set_commands_for_user

from db import async_session
from models import Student

logger = logging.getLogger(__name__)
router = Router()

@router.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext, bot: Bot):
    """Handles the /start command, logs user ID, ensures student exists, and cancels active surveys."""
    user_id = msg.from_user.id
    username = msg.from_user.username
    db_username = username.lower().lstrip('@') if username else str(user_id)

    # --- Cancel active state if any ---
    current_state = await state.get_state()
    # Check if *any* state is active
    if current_state is not None: 
        state_name = current_state # Store state name before clearing
        logger.info(f"User {user_id} used /start, cancelling previous state: {state_name}")
        data = await state.get_data()
        await state.clear() # Clear state first
        await msg.answer("(Предыдущая операция отменена)") # Generic cancellation message
        
        # Specific cleanup if it was a survey
        if state_name == SurveyResponseStates.answering:
            last_msg_id = data.get("last_question_message_id")
            if last_msg_id:
                try:
                    await bot.delete_message(chat_id=user_id, message_id=last_msg_id)
                    logger.info(f"Deleted last survey question message {last_msg_id} for user {user_id} due to /start.")
                except Exception as e:
                    logger.warning(f"Could not delete last survey question message {last_msg_id} for user {user_id} on /start cancel: {e}")
    # --- End state cancellation ---

    async with async_session() as session:
        try:
            # Try to find the student by username (if available) or user ID
            stmt = select(Student)
            if username:
                stmt = stmt.where(Student.tg_username == db_username)
            else:
                # If no username, we can't reliably link, but log the attempt
                logger.warning(f"User {user_id} started bot without username. Cannot reliably link to existing DB record if username changes later.")
                # Optionally, try finding by user_id if we assume it was stored before
                # stmt = stmt.where(Student.tg_user_id == user_id)
                # For now, let's prioritize creating if username doesn't match
                pass # We'll handle creation/update below

            result = await session.execute(stmt)
            student = result.scalars().first()

            if student:
                # Student found, update their tg_user_id if it's missing or different
                if student.tg_user_id != user_id:
                    student.tg_user_id = user_id
                    session.add(student) # Mark for update
                    await session.commit()
                    await session.refresh(student)
                    logger.info(f"Updated tg_user_id for student '{db_username}' (ID: {student.id}) to {user_id}")
                else:
                    logger.info(f"Student '{db_username}' (ID: {student.id}) started the bot (user_id: {user_id} already known).")
            else:
                # Student not found by username, create new one
                # Ensure username uniqueness is handled by DB constraint
                new_student = Student(tg_username=db_username, tg_user_id=user_id)
                session.add(new_student)
                await session.commit()
                await session.refresh(new_student)
                logger.info(f"Created new student '{db_username}' with ID {new_student.id} and tg_user_id {user_id}.")

        except Exception as e:
            logger.exception(f"Error handling /start for user {user_id} ('{username}'): {e}")
            await session.rollback()
            # Avoid sending error message on /start if DB fails

    # Send welcome message regardless of DB outcome
    role = await get_user_role(user_id, username)
    await set_commands_for_user(bot, user_id, role)
    await msg.answer(
        "👋 Добро пожаловать в Feedback Bot! Используйте /help для просмотра доступных команд."
    )