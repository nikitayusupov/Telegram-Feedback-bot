# student_flow/survey_handlers.py
import logging
from aiogram import F, Router, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from sqlmodel import select

from db import async_session
from models import Student, Response, Question, QuestionType, Group, Course
# Import the keyboard functions we might need
from utils.keyboards import get_scale_keyboard, get_skip_keyboard

logger = logging.getLogger(__name__)
router = Router()

# Constant for skipped answers
SKIPPED_ANSWER = "[SKIPPED]"

# ----- FSM States for Student receiving survey -----
class SurveyResponseStates(StatesGroup):
    awaiting_answer = State()

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
    group_id = data.get("group_id") # Make sure group_id is stored in state
    
    # Ensure all necessary context is present
    if not survey_id or not question_id or not group_id:
        logger.error(f"Missing survey_id/question_id/group_id in state for user {user_id} during save_response")
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

            # Fetch necessary data for denormalization
            question = await session.get(Question, question_id)
            if not question:
                logger.error(f"Cannot find Question {question_id} for survey {survey_id}, user {user_id}. Aborting response save.")
                return False
            
            group = await session.get(Group, group_id)
            if not group:
                logger.error(f"Cannot find Group {group_id} for survey {survey_id}, user {user_id}. Aborting response save.")
                return False
                
            course = await session.get(Course, group.course_id)
            if not course:
                # Less critical, maybe log warning and use placeholder?
                logger.warning(f"Cannot find Course {group.course_id} for group {group_id}, survey {survey_id}, user {user_id}. Using placeholder name.")
                course_name = f"[Deleted Course ID: {group.course_id}]"
            else:
                course_name = course.name

            # Create the denormalized Response object
            new_response = Response(
                survey_id=survey_id,
                student_tg_id=user_id,
                student_tg_username=student_username, # Add username
                course_name=course_name,
                group_name=group.name,
                question_text=question.text,
                question_type=question.q_type,
                answer=answer_text.strip()
                # answered_at is handled by default_factory
            )
            session.add(new_response)
            await session.commit()
            logger.info(f"Saved denormalized response for survey {survey_id}, user {user_id}, question ID {question_id} (Text: '{question.text[:20]}...').")
            return True # Indicate success
        except Exception as e:
            logger.exception(f"Failed to save denormalized response for survey {survey_id}, user {user_id}, question {question_id}: {e}")
            await session.rollback()
            return False # Indicate failure

# ----- Helper: Send Next Question or Complete -----
async def send_next_question_or_complete(bot: Bot, state: FSMContext, user_id: int):
    data = await state.get_data()
    group_id = data.get("group_id")
    current_order = data.get("current_question_order")
    
    if group_id is None or current_order is None:
        logger.error(f"Missing group_id or current_question_order in state for user {user_id} during send_next_question")
        await bot.send_message(user_id, "Произошла ошибка при получении следующего вопроса. Пожалуйста, свяжитесь с куратором.")
        await state.clear()
        return
        
    next_order = current_order + 1
    
    async with async_session() as session:
        stmt = select(Question).where(
            Question.group_id == group_id,
            Question.order == next_order
        ).order_by(Question.order) # Ensure order just in case
        result = await session.execute(stmt)
        next_question: Question | None = result.scalars().first()
        
    if next_question:
        # Send next question
        question_text = next_question.text
        reply_markup = None
        if next_question.q_type == QuestionType.scale:
            reply_markup = get_scale_keyboard().as_markup()
        else: # Text question
            reply_markup = get_skip_keyboard().as_markup()
            
        # Construct the text first
        message_text = f"Вопрос {next_order}:\n{question_text}"
        # Capture the sent message object
        sent_message = await bot.send_message(
            chat_id=user_id, 
            text=message_text, # Use the constructed text
            reply_markup=reply_markup
        )
        # Update state for the *new* current question AND the new message ID
        await state.update_data(
            current_question_id=next_question.id,
            current_question_order=next_question.order,
            last_question_message_id=sent_message.message_id # Update message ID
        )
        logger.info(f"Sent question {next_order} (ID: {next_question.id}) to user {user_id}, message ID {sent_message.message_id}.")
    else:
        await bot.send_message(user_id, "Спасибо! Опрос завершен. ✅", reply_markup=ReplyKeyboardRemove())
        await state.clear()
        logger.info(f"Survey completed for user {user_id} (last question order: {current_order})")

# ----- Handlers -----

# Handler for SCALE answers (Callback Query)
@router.callback_query(SurveyResponseStates.awaiting_answer, F.data.startswith("survey_answer:"))
async def handle_scale_answer(callback: CallbackQuery, state: FSMContext, bot: Bot):
    try:
        answer = callback.data.split(":")[1]
        user_id = callback.from_user.id
        data = await state.get_data() # Get state data
        last_msg_id = data.get("last_question_message_id") # Get message ID to delete
        
        # Attempt to save the response
        if await save_response(state, user_id, answer):
            # If saved, try deleting previous question message
            if last_msg_id:
                try:
                    await bot.delete_message(chat_id=user_id, message_id=last_msg_id)
                    logger.info(f"Deleted previous question message {last_msg_id} for user {user_id} after scale answer.")
                except Exception as e:
                    logger.warning(f"Could not delete previous question message {last_msg_id} for user {user_id}: {e}")
            # Send the next question or complete
            await send_next_question_or_complete(bot, state, user_id)
        else:
            # Saving failed (error already logged), inform user and clear state
            await callback.message.edit_text("Произошла ошибка при сохранении вашего ответа. Попробуйте позже или свяжитесь с куратором.", reply_markup=None)
            await state.clear()
            
        await callback.answer() # Acknowledge button press
            
    except (IndexError, ValueError):
        logger.warning(f"Invalid callback data received for scale answer: {callback.data}")
        await callback.answer("Ошибка: Некорректный ответ.", show_alert=True)

# Handler for SKIP button (Callback Query)
@router.callback_query(SurveyResponseStates.awaiting_answer, F.data == "survey_action:skip")
async def handle_skip_button(callback: CallbackQuery, state: FSMContext, bot: Bot):
    user_id = callback.from_user.id
    data = await state.get_data()
    last_msg_id = data.get("last_question_message_id")
    
    if await save_response(state, user_id, SKIPPED_ANSWER):
        # If saved, try deleting previous question message
        if last_msg_id:
            try:
                await bot.delete_message(chat_id=user_id, message_id=last_msg_id)
                logger.info(f"Deleted previous question message {last_msg_id} for user {user_id} after skip button.")
            except Exception as e:
                logger.warning(f"Could not delete previous question message {last_msg_id} for user {user_id}: {e}")
        # Send next question or complete
        await send_next_question_or_complete(bot, state, user_id)
    else:
        await callback.message.edit_text("Произошла ошибка при обработке пропуска. Попробуйте позже или свяжитесь с куратором.", reply_markup=None)
        await state.clear()
        
    await callback.answer("Вопрос пропущен")

# Handler for TEXT answers (Message)
@router.message(SurveyResponseStates.awaiting_answer, F.text)
async def handle_text_answer(message: Message, state: FSMContext, bot: Bot):
    answer = message.text
    user_id = message.from_user.id
    data = await state.get_data()
    last_msg_id = data.get("last_question_message_id")
    
    if await save_response(state, user_id, answer):
        # If saved, try deleting previous question message
        if last_msg_id:
            try:
                await bot.delete_message(chat_id=user_id, message_id=last_msg_id)
                logger.info(f"Deleted previous question message {last_msg_id} for user {user_id} after text answer.")
            except Exception as e:
                logger.warning(f"Could not delete previous question message {last_msg_id} for user {user_id}: {e}")
        # Send next question or complete
        await send_next_question_or_complete(bot, state, user_id)
    else:
        await message.answer("Произошла ошибка при сохранении вашего ответа. Попробуйте позже или свяжитесь с куратором.")
        await state.clear()

# Handler for /skip command
@router.message(SurveyResponseStates.awaiting_answer, Command("skip"))
async def handle_skip_command(message: Message, state: FSMContext, bot: Bot):
    user_id = message.from_user.id
    data = await state.get_data()
    last_msg_id = data.get("last_question_message_id")
    
    if await save_response(state, user_id, SKIPPED_ANSWER):
        # If saved, try deleting previous question message
        if last_msg_id:
            try:
                await bot.delete_message(chat_id=user_id, message_id=last_msg_id)
                logger.info(f"Deleted previous question message {last_msg_id} for user {user_id} after /skip command.")
            except Exception as e:
                logger.warning(f"Could not delete previous question message {last_msg_id} for user {user_id}: {e}")
        # Send confirmation and next question/complete
        await message.answer("Вопрос пропущен.") # Keep confirmation for command
        await send_next_question_or_complete(bot, state, user_id)
    else:
        await message.answer("Произошла ошибка при обработке пропуска. Попробуйте позже или свяжитесь с куратором.")
        await state.clear() 