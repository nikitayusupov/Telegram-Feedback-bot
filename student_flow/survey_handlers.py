# student_flow/survey_handlers.py
import logging
from aiogram import F, Router, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from sqlmodel import select

from db import async_session
from models import Student, Response, Question, QuestionType, Group, Course, Survey
# Import the keyboard functions we might need
from utils.keyboards import get_scale_keyboard, get_skip_keyboard

logger = logging.getLogger(__name__)
router = Router()

# Constant for skipped answers
SKIPPED_ANSWER = "[SKIPPED]"

# ----- FSM States for Student receiving survey -----
class SurveyResponseStates(StatesGroup):
    answering = State()

# ----- Helper: Get Student ID -----
async def get_student_id(user_id: int) -> int | None:
    async with async_session() as session:
        result = await session.execute(select(Student.id).where(Student.tg_user_id == user_id))
        student_id = result.scalars().first()
        if not student_id:
            logger.error(f"Could not find Student record for user_id {user_id} while processing survey response.")
        return student_id

# ----- Helper: Save Response -----
async def save_response(state: FSMContext, user_id: int, answer_text: str):
    data = await state.get_data()
    survey_id = data.get("survey_id")
    question_id = data.get("current_question_id")
    survey_title = data.get("survey_title", "")
    course_name = data.get("course_name", "")
    group_name = data.get("group_name", "")
    question_type = data.get("question_type")
    
    # Ensure all necessary context is present
    if not survey_id or not question_id:
        logger.error(f"Missing survey_id/question_id in state for user {user_id} during save_response")
        return False # Indicate failure

    async with async_session() as session:
        try:
            # Fetch student username first
            student_result = await session.execute(select(Student).where(Student.tg_user_id == user_id))
            student = student_result.scalars().first()
            student_username = student.tg_username if student else f"[Unknown User ID: {user_id}]"
            if not student:
                 logger.error(f"Cannot find Student with tg_user_id {user_id} for survey {survey_id}. Aborting response save.")
                 return False

            # Fetch the question to get its text
            question = await session.get(Question, question_id)
            if not question:
                logger.error(f"Cannot find Question {question_id} for survey {survey_id}, user {user_id}. Aborting response save.")
                return False

            # Create the denormalized Response object
            new_response = Response(
                survey_id=survey_id,
                student_tg_id=user_id,
                student_tg_username=student_username,
                course_name=course_name,
                group_name=group_name,
                survey_title=survey_title,
                question_text=question.text,
                question_type=question_type or question.q_type,
                answer=answer_text.strip()
                # answered_at is handled by default_factory
            )
            session.add(new_response)
            await session.commit()
            logger.info(f"Saved response for survey {survey_id} '{survey_title}', user {user_id}, question ID {question_id}.")
            return True # Indicate success
        except Exception as e:
            logger.exception(f"Failed to save response for survey {survey_id}, user {user_id}, question {question_id}: {e}")
            await session.rollback()
            return False # Indicate failure

# ----- Helper: Send Next Question or Complete -----
async def send_next_question_or_complete(bot: Bot, state: FSMContext, user_id: int):
    data = await state.get_data()
    survey_id = data.get("survey_id")
    question_order = data.get("question_order", 1)
    survey_title = data.get("survey_title", "")
    course_name = data.get("course_name", "")
    
    if survey_id is None or question_order is None:
        logger.error(f"Missing survey_id or question_order in state for user {user_id} during send_next_question")
        await bot.send_message(user_id, "Произошла ошибка при получении следующего вопроса. Пожалуйста, свяжитесь с куратором.")
        await state.clear()
        return
        
    next_order = question_order + 1
    
    async with async_session() as session:
        stmt = select(Question).where(
            Question.survey_id == survey_id,
            Question.order == next_order
        ).order_by(Question.order) # Ensure order just in case
        result = await session.execute(stmt)
        next_question: Question | None = result.scalars().first()
        
    if next_question:
        # Send next question
        question_text = next_question.text
        
        # Create message text with survey title and course name
        message_text = f"📊 <b>Опрос '{survey_title}' по курсу '{course_name}'</b>\n\n<b>Вопрос {next_order}:</b> {question_text}"
        
        # Select keyboard based on question type
        if next_question.q_type == QuestionType.scale:
            keyboard = get_scale_keyboard()
            message_text += "\n\nОцените по шкале от 1 до 10:"
        else: # Text question
            keyboard = get_skip_keyboard()
            message_text += "\n\nВведите ваш ответ или нажмите «Пропустить»:"
            
        # Send the message
        sent_message = await bot.send_message(
            chat_id=user_id, 
            text=message_text,
            reply_markup=keyboard.as_markup()
        )
        
        # Update state for the next question
        await state.update_data(
            current_question_id=next_question.id,
            question_type=next_question.q_type,
            question_order=next_order
        )
        logger.info(f"Sent question {next_order} (ID: {next_question.id}) to user {user_id}.")
    else:
        await bot.send_message(user_id, f"Спасибо! Опрос '{survey_title}' завершен. ✅", reply_markup=ReplyKeyboardRemove())
        await state.clear()
        logger.info(f"Survey completed for user {user_id} (last question order: {question_order})")

# ----- Handlers -----

# Handler for SCALE answers (Callback Query)
@router.callback_query(SurveyResponseStates.answering, F.data.startswith("survey_answer:"))
async def handle_scale_answer(callback: CallbackQuery, state: FSMContext, bot: Bot):
    try:
        answer = callback.data.split(":")[1]
        user_id = callback.from_user.id
        
        # Attempt to save the response
        if await save_response(state, user_id, answer):
            # Let the user know their answer was recorded
            await callback.answer("Ответ записан", show_alert=False)
            # Send the next question or complete
            await send_next_question_or_complete(bot, state, user_id)
        else:
            # Saving failed (error already logged), inform user and clear state
            await callback.message.edit_text("Произошла ошибка при сохранении вашего ответа. Попробуйте позже или свяжитесь с куратором.", reply_markup=None)
            await state.clear()
            await callback.answer("Ошибка сохранения", show_alert=True)
    except (IndexError, ValueError):
        logger.warning(f"Invalid callback data received for scale answer: {callback.data}")
        await callback.answer("Ошибка: Некорректный ответ.", show_alert=True)

# Handler for SKIP button (Callback Query)
@router.callback_query(SurveyResponseStates.answering, F.data == "survey_action:skip")
async def handle_skip_button(callback: CallbackQuery, state: FSMContext, bot: Bot):
    user_id = callback.from_user.id
    
    if await save_response(state, user_id, SKIPPED_ANSWER):
        await callback.answer("Вопрос пропущен", show_alert=False)
        # Send next question or complete
        await send_next_question_or_complete(bot, state, user_id)
    else:
        await callback.message.edit_text("Произошла ошибка при обработке пропуска. Попробуйте позже или свяжитесь с куратором.", reply_markup=None)
        await state.clear()
        await callback.answer("Ошибка обработки", show_alert=True)

# Handler for TEXT answers (Messages)
@router.message(SurveyResponseStates.answering, F.text)
async def handle_text_answer(message: Message, state: FSMContext, bot: Bot):
    user_id = message.from_user.id
    answer_text = message.text
    
    # Make sure this is a valid answer (not empty, not a command)
    if not answer_text or answer_text.startswith('/'):
        await message.answer("Пожалуйста, введите текстовый ответ или нажмите кнопку «Пропустить».")
        return
    
    if await save_response(state, user_id, answer_text):
        # Send next question or complete
        await send_next_question_or_complete(bot, state, user_id)
    else:
        await message.answer("Произошла ошибка при сохранении вашего ответа. Попробуйте позже или свяжитесь с куратором.")
        await state.clear()

# Handler for /skip command as alternative to button
@router.message(SurveyResponseStates.answering, Command("skip"))
async def handle_skip_command(message: Message, state: FSMContext, bot: Bot):
    user_id = message.from_user.id
    
    if await save_response(state, user_id, SKIPPED_ANSWER):
        # Send next question or complete
        await send_next_question_or_complete(bot, state, user_id)
    else:
        await message.answer("Произошла ошибка при обработке пропуска. Попробуйте позже или свяжитесь с куратором.")
        await state.clear() 