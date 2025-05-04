import logging
import asyncio
from typing import List, Optional

from aiogram import F, Router, Bot, Dispatcher
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from sqlmodel import select

from curator_flow.group_handlers import curator_guard
from db import async_session
from models import Course, Group, Question, Student, Survey, GroupStudent, QuestionType
from utils.keyboards import (
    get_course_selection_keyboard, 
    get_group_selection_keyboard,
    get_scale_keyboard,
    get_skip_keyboard
)
from utils.constants import NO_COURSES_FOUND
from student_flow.survey_handlers import SurveyResponseStates

logger = logging.getLogger(__name__)
router = Router()

# ----- FSM States -----
class SendSurveyStates(StatesGroup):
    selecting_course = State()
    selecting_group = State()
    confirming_send = State() # Optional: Add confirmation before sending

# ----- Command Handler -----
@router.message(Command("send_now"))
@curator_guard
async def send_now_start(msg: Message, state: FSMContext):
    """Starts the flow to send a survey to a group."""
    # Cancel previous operation if any
    current_state = await state.get_state()
    if current_state is not None:
        logger.info(f"User {msg.from_user.id} initiated /send_now, cancelling previous state: {current_state}")
        await state.clear()
        await msg.answer("(Предыдущая операция отменена)")
        
    # await state.clear() # No longer needed here
    if msg.text.strip() != "/send_now":
        await msg.answer("Пожалуйста, используйте команду /send_now без аргументов.")
        return

    builder = await get_course_selection_keyboard(callback_prefix="ss_select_course")
    if builder is None:
        await msg.answer(NO_COURSES_FOUND)
        return

    await msg.answer("1/2: Выберите курс для отправки опроса:", reply_markup=builder.as_markup())
    await state.set_state(SendSurveyStates.selecting_course)

# ----- Callback Handlers (Course/Group Selection) -----
@router.callback_query(SendSurveyStates.selecting_course, F.data.startswith("ss_select_course:"))
async def send_now_course_selected(callback: CallbackQuery, state: FSMContext):
    """Handles course selection and shows group keyboard."""
    try:
        course_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка выбора курса.", show_alert=True)
        await state.clear()
        return

    group_builder = await get_group_selection_keyboard(course_id, callback_prefix="ss_select_group")
    if group_builder is None:
        await callback.message.edit_text("В этом курсе нет групп. Сначала создайте группу (/set_group).", reply_markup=None)
        await callback.answer()
        await state.clear()
        return

    await state.update_data(course_id=course_id)
    await callback.message.edit_text("2/2: Выберите группу для отправки опроса:", reply_markup=group_builder.as_markup())
    await state.set_state(SendSurveyStates.selecting_group)
    await callback.answer()

# Helper function to send the first question and set student state
async def initiate_survey_for_student(bot: Bot, dp: Dispatcher, student: Student, first_question: Question, survey_id: int):
    if not student.tg_user_id:
        logger.warning(f"Cannot initiate survey for student '{student.tg_username}' (ID: {student.id}) - missing tg_user_id")
        return False # Indicate failure

    chat_id = student.tg_user_id
    question_text = first_question.text
    reply_markup = None

    if first_question.q_type == QuestionType.scale:
        keyboard = get_scale_keyboard()
        reply_markup = keyboard.as_markup()
    # For text questions, ensure skip button is added
    else:
        # Use the dedicated skip keyboard function
        keyboard = get_skip_keyboard() 
        reply_markup = keyboard.as_markup()
        
    try:
        # Capture the sent message object
        sent_message = await bot.send_message(
            chat_id=chat_id,
            text=f"Вопрос 1:\n{question_text}",
            reply_markup=reply_markup
        )
        
        # Set the student's FSM state programmatically
        student_fsm_context = dp.fsm.resolve_context(bot=bot, chat_id=chat_id, user_id=chat_id)
        await student_fsm_context.set_state(SurveyResponseStates.awaiting_answer)
        await student_fsm_context.set_data({
            "survey_id": survey_id,
            "current_question_id": first_question.id,
            "current_question_order": first_question.order,
            "group_id": first_question.group_id, # Store group_id for potential later use
            "last_question_message_id": sent_message.message_id # Store message ID
        })
        logger.info(f"Successfully initiated survey {survey_id} for student '{student.tg_username}' (ID: {student.id}, ChatID: {chat_id}) with question ID {first_question.id}, message ID {sent_message.message_id}")
        return True # Indicate success

    except TelegramForbiddenError:
        logger.warning(f"Failed to send survey to student '{student.tg_username}' (ChatID: {chat_id}). Bot blocked or chat deactivated.")
        return False
    except TelegramBadRequest as e:
        logger.warning(f"Failed to send survey to student '{student.tg_username}' (ChatID: {chat_id}). Bad request: {e}")
        return False
    except Exception as e:
        logger.exception(f"Unexpected error sending survey to student '{student.tg_username}' (ChatID: {chat_id}): {e}")
        return False

# Handler for selecting group and initiating the send process
@router.callback_query(SendSurveyStates.selecting_group, F.data.startswith("ss_select_group:"))
async def send_now_group_selected(callback: CallbackQuery, state: FSMContext, bot: Bot, dp_instance: Dispatcher):
    """Handles group selection, validates, and starts sending the survey."""
    try:
        group_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка выбора группы.", show_alert=True)
        await state.clear()
        return

    # Edit message to show processing
    await callback.message.edit_text("Проверяем группу и готовим опрос...", reply_markup=None)
    await callback.answer()

    async with async_session() as session:
        # 1. Get Group Name
        group = await session.get(Group, group_id)
        if not group:
            await callback.message.edit_text("Ошибка: Выбранная группа не найдена.")
            await state.clear()
            return
        group_name = group.name
        logger.info(f"Curator {callback.from_user.id} initiated survey send for group '{group_name}' (ID: {group_id})")

        # 2. Validate: Check for questions
        questions_stmt = select(Question).where(Question.group_id == group_id).order_by(Question.order)
        questions_result = await session.execute(questions_stmt)
        questions: List[Question] = questions_result.scalars().all()
        if not questions:
            logger.warning(f"Survey send cancelled for group '{group_name}': No questions found.")
            await callback.message.edit_text(f"⚠️ Невозможно отправить опрос для группы '{group_name}': вопросы не заданы. Используйте /set_questions.")
            await state.clear()
            return
        first_question = questions[0]

        # 3. Validate: Check for students and get their IDs
        students_stmt = (
            select(Student)
            .join(GroupStudent, Student.id == GroupStudent.student_id)
            .where(GroupStudent.group_id == group_id)
        )
        students_result = await session.execute(students_stmt)
        students: List[Student] = students_result.scalars().all()
        if not students:
            logger.warning(f"Survey send cancelled for group '{group_name}': No students found.")
            await callback.message.edit_text(f"⚠️ Невозможно отправить опрос для группы '{group_name}': нет студентов. Используйте /set_recipients.")
            await state.clear()
            return
            
        reachable_students = [s for s in students if s.tg_user_id is not None]
        unreachable_students = [s for s in students if s.tg_user_id is None]

        if not reachable_students:
            logger.warning(f"Survey send cancelled for group '{group_name}': No students with known user IDs.")
            usernames = ", ".join([f"@{s.tg_username}" for s in unreachable_students])
            await callback.message.edit_text(f"⚠️ Невозможно отправить опрос для группы '{group_name}': нет студентов, которые запустили бота (/start). Неизвестные ID для: {usernames}")
            await state.clear()
            return

        # 4. Create Survey Record
        new_survey = Survey(group_id=group_id)
        session.add(new_survey)
        await session.commit()
        await session.refresh(new_survey)
        survey_id = new_survey.id
        logger.info(f"Created Survey record with ID {survey_id} for group '{group_name}' (ID: {group_id})")

    # 5. Initiate sending concurrently (with delays)
    await callback.message.edit_text(f"Начинаем отправку опроса группе '{group_name}' ({len(reachable_students)}/{len(students)} студентов)...", reply_markup=None)
    
    send_tasks = []
    successful_sends = 0
    failed_sends = 0

    for student in reachable_students:
        # Launch the task
        task = asyncio.create_task(
            initiate_survey_for_student(bot, dp_instance, student, first_question, survey_id)
        )
        send_tasks.append(task)
        # Add a small delay between *launching* tasks to avoid hitting rate limits immediately
        await asyncio.sleep(0.1) 

    # Wait for all sending tasks to complete and gather results
    results = await asyncio.gather(*send_tasks)
    successful_sends = sum(1 for res in results if res is True)
    failed_sends = len(results) - successful_sends

    # 6. Report back to curator
    final_message_lines = [f"✅ Отправка опроса группе '{group_name}' завершена."]
    final_message_lines.append(f"    Успешно отправлено: {successful_sends}")
    if failed_sends > 0:
        final_message_lines.append(f"    Не удалось отправить: {failed_sends} (проверьте логи)")
    if unreachable_students:
        usernames = ", ".join([f"@{s.tg_username}" for s in unreachable_students])
        final_message_lines.append(f"    Не найдены ID (не /start): {len(unreachable_students)} ({usernames})")
        
    await callback.message.edit_text("\n".join(final_message_lines))
    await state.clear()
