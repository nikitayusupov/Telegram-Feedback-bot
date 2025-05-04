from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
# Import necessary for state check and bot object
from aiogram.fsm.context import FSMContext
from aiogram import Bot
from student_flow.survey_handlers import SurveyResponseStates # Import the state

# Import checkers from other flows
from utils.auth_checks import is_admin # Use centralized check
# Import models and session for DB check
from db import async_session
from models import Curator
from sqlmodel import select

import logging
logger = logging.getLogger(__name__)

router = Router()

@router.message(Command("help"))
async def cmd_help(msg: Message, state: FSMContext, bot: Bot): # Add state and bot
    user_id = msg.from_user.id
    username = msg.from_user.username

    # --- Cancel active state if any ---
    current_state = await state.get_state()
    # Check if *any* state is active
    if current_state is not None: 
        state_name = current_state # Store state name before clearing
        logger.info(f"User {user_id} used /help, cancelling previous state: {state_name}")
        data = await state.get_data()
        await state.clear() # Clear state first
        await msg.answer("(Предыдущая операция отменена)") # Generic cancellation message
        
        # Specific cleanup if it was a survey
        if state_name == SurveyResponseStates.awaiting_answer:
            last_msg_id = data.get("last_question_message_id")
            if last_msg_id:
                try:
                    await bot.delete_message(chat_id=user_id, message_id=last_msg_id)
                    logger.info(f"Deleted last survey question message {last_msg_id} for user {user_id} due to /help.")
                except Exception as e:
                    logger.warning(f"Could not delete last survey question message {last_msg_id} for user {user_id} on /help cancel: {e}")
        # Proceed to show help anyway after cancelling
    # --- End state cancellation ---

    user_is_admin = is_admin(user_id, username)
    
    # --- Async DB Check for Curator --- 
    user_is_curator = False
    if username and username.startswith('@'):
        # Use username without '@' for DB lookup
        db_username = username.lower().lstrip('@')
        async with async_session() as session:
            result = await session.execute(
                select(Curator).where(Curator.tg_username == db_username)
            )
            if result.scalars().first():
                user_is_curator = True
    # --- End DB Check --- 
    
    # Define command blocks
    student_cmds = (
        "<b>Команды для студентов:</b>\n"
        "  Отзывы:\n"
        "    /feedback - Оставить отзыв о курсе"
    )
    curator_cmds = (
        "\n\n<b>Команды для кураторов:</b>\n"
        "  Работа с группами:\n"
        "    /set_group - Создать новую группу\n"
        "    /list_groups - Посмотреть список групп\n"
        "  Работа с опросами:\n"
        "    /set_recipients - Задать список студентов группы\n"
        "    /set_questions - Создать опрос для группы\n"
        "    /send_now - Отправить опрос группе"
    )
    admin_cmds = (
        "\n\n<b>Команды для администраторов:</b>\n"
        "  Работа с курсами:\n"
        "    /list_courses - Список всех курсов\n"
        "    /create_course - Создать новый курс\n"
        "    /delete_course - Удалить курс\n"
        "  Работа с кураторами:\n"
        "    /add_curator - Добавить куратора к курсу"
    )
    
    # Determine help text based on role
    if user_is_admin:
        # Admins see all commands
        help_text = student_cmds + curator_cmds + admin_cmds
    elif user_is_curator:
        # Curators see student and curator commands
        help_text = student_cmds + curator_cmds
    else:
        # Students see only student commands
        help_text = student_cmds

    await msg.answer(help_text) 