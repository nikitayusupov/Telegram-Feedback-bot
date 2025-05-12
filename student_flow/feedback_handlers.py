# student_flow/feedback_handlers.py
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, ReplyKeyboardRemove, CallbackQuery, InlineKeyboardButton
from aiogram import Bot
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
GOOGLE_SHEET_TAB_NAME = settings.gsheet_tab_name
GOOGLE_SHEET_CREDENTIALS_PATH = settings.google_credentials_path

import logging
logger = logging.getLogger(__name__)
# Import the keyboard utility
from utils.keyboards import get_course_selection_keyboard
# Import the constant
from utils.constants import NO_COURSES_FOUND

# Initialize Google Sheets manager
sheets_manager = GoogleSheetsManager(
    creds_path=GOOGLE_SHEET_CREDENTIALS_PATH,
    spreadsheet_url=GOOGLE_SHEETS_URL,
    sheet_name=GOOGLE_SHEET_TAB_NAME
)

# FSM States for Feedback
class FeedbackStates(StatesGroup):
    selecting_course = State()
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
    """Handles the course selection and asks for the topic."""
    try:
        course_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка при выборе курса.", show_alert=True)
        return

    await state.update_data(course_id=course_id)
    await state.set_state(FeedbackStates.topic)
    
    await callback.answer("Курс выбран!")
    # Edit the original message to remove buttons and ask for topic
    await callback.message.edit_text("Укажите тему для отзыва:")


@router.message(FeedbackStates.topic, F.text)
async def feedback_topic(msg: Message, state: FSMContext):
    """Stores the topic and asks for feedback text."""
    # course_id should already be in state data from previous step
    await state.update_data(topic=msg.text.strip())
    await state.set_state(FeedbackStates.text)
    await msg.answer("Напишите ваш отзыв:")


@router.message(FeedbackStates.text, F.text)
async def feedback_save(msg: Message, state: FSMContext):
    """Saves the feedback including the course ID."""
    data = await state.get_data()
    topic = data.get("topic", "<none>")
    course_id = data.get("course_id")
    
    if course_id is None:
        # Should not happen if flow is correct, but handle defensively
        await msg.answer("Ошибка: ID курса не найден. Пожалуйста, начните снова с /feedback.")
        await state.clear()
        return
        
    # Get student details
    user_id = msg.from_user.id
    raw_username = msg.from_user.username
    # Use user ID string as fallback username if none exists
    db_username = raw_username.lstrip('@') if raw_username else str(user_id) 

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
            student_tg_id=user_id, 
            student_tg_username=db_username,
            course_name=course_name,
            topic=topic,
            text=msg.text.strip(),
        )
        s.add(feedback)
        await s.commit()
        
        # After successful DB save, also save to Google Sheets
        feedback_data = {
            "timestamp": feedback.created_at,
            "student_username": db_username,
            "course_name": course_name,
            "topic": topic,
            "text": msg.text.strip()
        }
        
        # Save to Google Sheets asynchronously
        sheets_result = await sheets_manager.add_feedback(feedback_data)
        if not sheets_result:
            logger.error(f"Failed to save feedback to Google Sheets for user {user_id}")
            # We don't notify the user of this error since the DB save was successful
    
    await msg.answer("Спасибо! Отзыв записан ✅", reply_markup=ReplyKeyboardRemove()) 