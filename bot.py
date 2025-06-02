"""
bot.py

Main entry-point.

• Bootstraps aiogram-3 dispatcher and includes routers from packages.
• Initialises logging, database schema, and starts polling.

"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats

from config import settings
# Import create_all_tables from db module
from db import create_all_tables 
# Import advanced logging configuration
from utils.logging_config import setup_logging

# --- Import Routers --- 
from student_flow import common_handlers as student_common_router
from student_flow import feedback_handlers as student_feedback_router
from curator_flow import group_handlers as curator_group_router
from admin_flow import course_handlers as admin_course_router
from admin_flow import curator_handlers as admin_curator_router
from common_flow import common_handlers as common_router
# Import curator recipients router
from curator_flow import recipients_handlers as curator_recipients_router
# Import curator question handlers router
from curator_flow import question_handlers as curator_question_router
# Import curator send survey router
from curator_flow import send_survey_handlers as curator_send_survey_router
# Import list surveys router
from curator_flow import list_surveys_handlers as curator_list_surveys_router
# Import list questions router
from curator_flow import list_questions_handlers as curator_list_questions_router
# Import student survey handlers router
from student_flow import survey_handlers as student_survey_router

# ---------------------------------------------------------------------------
# Logging Setup
# ---------------------------------------------------------------------------
# Setup advanced logging with daily rotation (file only, no console output)
logger = setup_logging(log_level=logging.INFO, log_dir="logs", console_output=False)


# ---------------------------------------------------------------------------
# Entrypoint                                                                 
# ---------------------------------------------------------------------------
def main():
    asyncio.run(async_main())

async def async_main():
    # Explicitly creating default bot properties for clarity
    default_properties = DefaultBotProperties(parse_mode=ParseMode.HTML)
    bot = Bot(settings.bot_token, default=default_properties)
    dp = Dispatcher()
    # Add dispatcher instance to workflow data for injection
    dp["dp_instance"] = dp 

    # --- Set bot commands in menu ---
    # Define commands for different roles (can be refined later)
    shared_commands = [
        BotCommand(command="start", description="🏁 Начать/Перезапустить бота"),
        BotCommand(command="help", description="❓ Показать справку"),
        BotCommand(command="feedback", description="✍️ Отправить отзыв"),
    ]
    curator_commands = [
        BotCommand(command="set_group", description="👥 Создать группу"),
        BotCommand(command="list_groups", description="📄 Список групп для курса"),
        BotCommand(command="set_recipients", description="👤 Задать состав группы"),
        BotCommand(command="list_recipients", description="📋 Показать список студентов в группе"),
        BotCommand(command="add_recipient", description="➕ Добавить студента в группу"),
        BotCommand(command="delete_recipient", description="🗑️ Удалить студента из группы"),
        BotCommand(command="set_questions", description="❓ Задать вопросы для опроса"),
        BotCommand(command="list_questions", description="📋 Показать вопросы опроса"),
        BotCommand(command="create_survey", description="📝 Создать новый опрос"),
        BotCommand(command="list_surveys", description="📊 Показать список опросов группы"),
        BotCommand(command="send_now", description="▶️ Отправить опрос группе")
    ]
    admin_commands = [
        BotCommand(command="list_courses", description="📚 Список всех курсов"),
        BotCommand(command="create_course", description="➕ Создать новый курс"),
        BotCommand(command="delete_course", description="🗑️ Удалить курс"),
        BotCommand(command="list_curators", description="👥 Список всех кураторов"),
        BotCommand(command="list_links", description="🔗 Ссылки на Google Sheets"),
        BotCommand(command="cleanup_surveys", description="🧹 Очистить опросы без названия"),
    ]

    # Set commands for all private chats (simplest approach first)
    all_commands = shared_commands + curator_commands + admin_commands
    await bot.set_my_commands(commands=all_commands, scope=BotCommandScopeAllPrivateChats())
    logger.info("Bot commands menu set.")

    # --- Include Routers ---
    dp.include_router(common_router.router) # Include common router (handles /help)
    dp.include_router(student_common_router.router) # Still needed for /start
    dp.include_router(student_feedback_router.router)
    dp.include_router(curator_group_router.router)
    dp.include_router(admin_course_router.router)
    dp.include_router(admin_curator_router.router)
    dp.include_router(curator_recipients_router.router)
    dp.include_router(curator_question_router.router) # Include question router
    dp.include_router(curator_send_survey_router.router) # Include send survey router
    dp.include_router(curator_list_surveys_router.router) # Include list surveys router
    dp.include_router(curator_list_questions_router.router) # Include list questions router
    dp.include_router(student_survey_router.router) # Include student survey router
    # Add other routers here as you create them

    # --- Database initialization ---
    # Moved from db.py to run in the main async context
    logger.info("Initializing database schema...")
    await create_all_tables() 
    logger.info("Database schema initialized (or already exists).")

    # --- Start Polling ---
    logger.info("Starting bot polling...")
    # Allow graceful shutdown
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped")
