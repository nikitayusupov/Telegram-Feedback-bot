# student_flow/feedback_handlers.py
import logging
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, ReplyKeyboardRemove, CallbackQuery, InlineKeyboardButton
from aiogram import Bot
from aiogram.utils.keyboard import InlineKeyboardBuilder
from student_flow.survey_handlers import SurveyResponseStates
from sqlmodel import select

# Assuming db imports are needed here if feedback interacts with DB directly
# If async_session and Feedback model are used here, add these imports:
from db import async_session
from models import Feedback, Course
from utils.sheets import GoogleSheetsManager
from config import settings

# Use settings from config
GOOGLE_SHEETS_URL = settings.gsheet_url

# Get logger instance
logger = logging.getLogger(__name__)
GOOGLE_SHEET_TAB_NAME = settings.gsheet_tab_name
GOOGLE_SHEET_CREDENTIALS_PATH = settings.google_credentials_path

import logging
logger = logging.getLogger(__name__)
# Import the keyboard utility
from utils.keyboards import get_course_selection_keyboard
# Import the constant
from utils.constants import NO_COURSES_FOUND
# Import the notification utility
from utils.notifications import notify_curators_about_feedback

# Initialize Google Sheets manager (optional)
try:
    sheets_manager = GoogleSheetsManager(
        creds_path=GOOGLE_SHEET_CREDENTIALS_PATH,
        spreadsheet_url=GOOGLE_SHEETS_URL,
        sheet_name=GOOGLE_SHEET_TAB_NAME
    )
    logger.info("Google Sheets integration initialized successfully")
except Exception as e:
    logger.warning(f"Google Sheets integration disabled: {e}")
    sheets_manager = None

# FSM States for Feedback
class FeedbackStates(StatesGroup):
    selecting_course = State()
    selecting_anonymity = State()
    topic = State()
    text = State()

router = Router()


# ----- Feedback flow ---------------------------------------------------------
@router.message(Command("feedback"))
async def feedback_begin(msg: Message, state: FSMContext, bot: Bot):
    """Starts the feedback process, cancelling any active state first."""
    user_id = msg.from_user.id
    
    # --- Cancel active state if any ---
    current_state = await state.get_state()
    if current_state is not None:
        state_name = current_state # Store state name before clearing
        logger.info(f"User {user_id} used /feedback, cancelling previous state: {state_name}")
        data = await state.get_data()
        last_msg_id = data.get("last_question_message_id") if state_name == SurveyResponseStates.awaiting_answer else None # Only get msg_id if it was survey state
        await state.clear() # Clear state first
        await msg.answer("(Предыдущая операция отменена)")
        if last_msg_id:
            try:
                await bot.delete_message(chat_id=user_id, message_id=last_msg_id)
                logger.info(f"Deleted last survey question message {last_msg_id} for user {user_id} due to /feedback.")
            except Exception as e:
                logger.warning(f"Could not delete last survey question message {last_msg_id} for user {user_id} on /feedback cancel: {e}")
        # Proceed to start feedback flow
    # --- End state cancellation ---
    
    # Use the utility function to get the keyboard
    builder = await get_course_selection_keyboard(callback_prefix="fb_select_course")

    if builder is None:
        await msg.answer(NO_COURSES_FOUND)
        await state.clear()
        return

    await msg.answer(
        "Выберите курс для вашего отзыва:", 
        reply_markup=builder.as_markup()
    )
    await state.set_state(FeedbackStates.selecting_course)


@router.callback_query(FeedbackStates.selecting_course, F.data.startswith("fb_select_course:"))
async def feedback_course_selected(callback: CallbackQuery, state: FSMContext):
    """Handles the course selection and asks for anonymity preference."""
    try:
        course_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        try:
            await callback.answer("Ошибка при выборе курса.", show_alert=True)
        except Exception as e:
            logger.warning(f"Failed to send error callback answer: {e}")
        return

    await state.update_data(course_id=course_id)
    await state.set_state(FeedbackStates.selecting_anonymity)
    
    # Try to send callback answer, but don't let network issues stop the flow
    try:
        await callback.answer("Курс выбран!")
    except Exception as e:
        # Log the error but continue with the main flow
        logger.warning(f"Failed to send callback answer: {e}")
    
    # Create anonymity selection keyboard
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="🔒 Анонимно", callback_data="fb_anonymity:anonymous"))
    builder.add(InlineKeyboardButton(text="👤 С указанием имени", callback_data="fb_anonymity:named"))
    builder.adjust(1)  # Each button in separate row (full width)
    
    try:
        await callback.message.edit_text(
            "Как вы хотите отправить отзыв?",
            reply_markup=builder.as_markup()
        )
    except Exception as e:
        logger.error(f"Failed to edit message for anonymity selection: {e}")
        # If editing fails, send a new message instead
        await callback.message.answer(
            "Как вы хотите отправить отзыв?",
            reply_markup=builder.as_markup()
        )

@router.callback_query(FeedbackStates.selecting_anonymity, F.data.startswith("fb_anonymity:"))
async def feedback_anonymity_selected(callback: CallbackQuery, state: FSMContext):
    """Handles anonymity selection and asks for the topic."""
    try:
        anonymity_choice = callback.data.split(":")[1]
    except (IndexError, ValueError):
        try:
            await callback.answer("Ошибка при выборе анонимности.", show_alert=True)
        except Exception as e:
            logger.warning(f"Failed to send error callback answer: {e}")
        return

    is_anonymous = (anonymity_choice == "anonymous")
    await state.update_data(is_anonymous=is_anonymous)
    await state.set_state(FeedbackStates.topic)
    
    anonymity_text = "анонимно" if is_anonymous else "с указанием имени"
    # Try to send callback answer, but don't let network issues stop the flow
    try:
        await callback.answer(f"Отзыв будет отправлен {anonymity_text}")
    except Exception as e:
        logger.warning(f"Failed to send callback answer: {e}")
    
    # Edit the original message to remove buttons and ask for topic
    try:
        await callback.message.edit_text("Укажите тему для отзыва:")
    except Exception as e:
        logger.error(f"Failed to edit message for topic input: {e}")
        # If editing fails, send a new message instead
        await callback.message.answer("Укажите тему для отзыва:")


@router.message(FeedbackStates.topic, F.text)
async def feedback_topic(msg: Message, state: FSMContext):
    """Stores the topic and asks for feedback text."""
    # course_id should already be in state data from previous step
    await state.update_data(topic=msg.text.strip())
    await state.set_state(FeedbackStates.text)
    await msg.answer("Напишите ваш отзыв:")


@router.message(FeedbackStates.text, F.text)
async def feedback_save(msg: Message, state: FSMContext, bot: Bot):
    """Saves the feedback including the course ID and anonymity preference."""
    data = await state.get_data()
    topic = data.get("topic", "<none>")
    course_id = data.get("course_id")
    is_anonymous = data.get("is_anonymous", False)
    
    if course_id is None:
        # Should not happen if flow is correct, but handle defensively
        await msg.answer("Ошибка: ID курса не найден. Пожалуйста, начните снова с /feedback.")
        await state.clear()
        return
        
    # Get student details
    user_id = msg.from_user.id
    raw_username = msg.from_user.username
    # Use user ID string as fallback username if none exists, but respect anonymity
    if is_anonymous:
        db_username = "Аноним"
        display_username = "Аноним"
    else:
        db_username = raw_username.lstrip('@') if raw_username else str(user_id)
        display_username = db_username

    await state.clear()
    async with async_session() as s:
        # Fetch Course Name
        course = await s.get(Course, course_id)
        if not course:
            logger.error(f"Cannot find Course {course_id} when saving feedback from user {user_id}. Storing ID fallback.")
            course_name = f"[Deleted Course ID: {course_id}]"
        else:
            course_name = course.name
            
        # Create denormalized Feedback object
        feedback = Feedback(
            student_tg_id=user_id if not is_anonymous else 0,  # Store 0 for anonymous
            student_tg_username=db_username,
            course_name=course_name,
            topic=topic,
            text=msg.text.strip(),
            is_anonymous=is_anonymous,
        )
        s.add(feedback)
        await s.commit()
        
        # After successful DB save, also save to Google Sheets
        feedback_data = {
            "timestamp": feedback.created_at,
            "student_username": display_username,
            "course_name": course_name,
            "topic": topic,
            "text": msg.text.strip()
        }
        
        # Save to Google Sheets asynchronously (if available)
        if sheets_manager:
            sheets_result = await sheets_manager.add_feedback(feedback_data)
            if not sheets_result:
                logger.error(f"Failed to save feedback to Google Sheets for user {user_id}")
                # We don't notify the user of this error since the DB save was successful
        else:
            logger.warning(f"Google Sheets integration not available, skipping for user {user_id}")
        
        # Send notification to curators of this course
        try:
            notified_curators = await notify_curators_about_feedback(
                bot=bot,
                course_id=course_id,
                student_username=display_username,
                topic=topic,
                feedback_text=msg.text.strip(),
                course_name=course_name
            )
            if notified_curators > 0:
                logger.info(f"Notified {notified_curators} curators about feedback from user {user_id} for course '{course_name}'")
            else:
                logger.info(f"No curators to notify about feedback from user {user_id} for course '{course_name}'")
        except Exception as e:
            logger.error(f"Error sending curator notifications for feedback from user {user_id}: {e}")
            # We don't notify the user of this error since the main feedback save was successful
    
    anonymity_confirmation = " (анонимно)" if is_anonymous else ""
    await msg.answer(f"Спасибо! Отзыв записан{anonymity_confirmation} ✅", reply_markup=ReplyKeyboardRemove()) 