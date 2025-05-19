import logging
import asyncio
from typing import List, Optional, Dict, Any
from datetime import datetime

from aiogram import F, Router, Bot, Dispatcher
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton
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
class CreateSurveyStates(StatesGroup):
    selecting_course = State()
    selecting_group = State()
    entering_title = State()

class SendSurveyStates(StatesGroup):
    selecting_course = State()
    selecting_group = State()
    selecting_survey = State()

# ----- Create Survey Command -----
@router.message(Command("create_survey"))
@curator_guard
async def create_survey_start(msg: Message, state: FSMContext):
    """Starts the flow to create a new survey."""
    # Cancel previous operation if any
    current_state = await state.get_state()
    if current_state is not None:
        logger.info(f"User {msg.from_user.id} initiated /create_survey, cancelling previous state: {current_state}")
        await state.clear()
        await msg.answer("(Предыдущая операция отменена)")
        
    builder = await get_course_selection_keyboard(callback_prefix="cs_select_course")
    if builder is None:
        await msg.answer(NO_COURSES_FOUND)
        return

    await msg.answer("1/3: Выберите курс для создания опроса:", reply_markup=builder.as_markup())
    await state.set_state(CreateSurveyStates.selecting_course)

@router.callback_query(CreateSurveyStates.selecting_course, F.data.startswith("cs_select_course:"))
async def create_survey_course_selected(callback: CallbackQuery, state: FSMContext):
    """Handles course selection and shows group keyboard."""
    try:
        course_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка выбора курса.", show_alert=True)
        await state.clear()
        return

    group_builder = await get_group_selection_keyboard(course_id, callback_prefix="cs_select_group")
    if group_builder is None:
        await callback.message.edit_text("В этом курсе нет групп. Сначала создайте группу (/set_group).")
        await callback.answer()
        await state.clear()
        return

    await state.update_data(course_id=course_id)
    await callback.message.edit_text("2/3: Выберите группу для создания опроса:", reply_markup=group_builder.as_markup())
    await state.set_state(CreateSurveyStates.selecting_group)
    await callback.answer()

@router.callback_query(CreateSurveyStates.selecting_group, F.data.startswith("cs_select_group:"))
async def create_survey_group_selected(callback: CallbackQuery, state: FSMContext):
    """Handles group selection and prompts for survey title."""
    try:
        group_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка выбора группы.", show_alert=True)
        await state.clear()
        return

    async with async_session() as session:
        # Проверяем существование группы
        group = await session.get(Group, group_id)
        if not group:
            await callback.message.edit_text("Ошибка: Выбранная группа не найдена.")
            await callback.answer()
            await state.clear()
            return

    # Сохраняем ID группы и имя группы в FSM
    await state.update_data(group_id=group_id, group_name=group.name)
    
    # Запрашиваем название опроса
    await callback.message.edit_text(
        f"3/3: Введите название для опроса группы '{group.name}':\n"
        "(до 1000 символов)"
    )
    await state.set_state(CreateSurveyStates.entering_title)
    await callback.answer()

@router.message(CreateSurveyStates.entering_title, F.text)
async def create_survey_title_entered(msg: Message, state: FSMContext):
    """Handles survey title input and creates the survey in the database."""
    data = await state.get_data()
    group_id = data.get("group_id")
    group_name = data.get("group_name", "Неизвестная группа")
    
    if not group_id:
        await msg.answer("Ошибка: Потерян контекст группы. Начните сначала с /create_survey.")
        await state.clear()
        return
    
    # Валидация названия опроса
    survey_title = msg.text.strip()
    if len(survey_title) > 1000:
        await msg.answer("Название опроса слишком длинное (более 1000 символов). Пожалуйста, введите более короткое название.")
        return  # Сохраняем состояние FSM для повторного ввода
    
    if not survey_title:
        await msg.answer("Название опроса не может быть пустым. Пожалуйста, введите название.")
        return  # Сохраняем состояние FSM для повторного ввода
    
    # Проверяем, существует ли уже опрос с таким названием для этой группы
    async with async_session() as session:
        existing_survey_stmt = select(Survey).where(
            Survey.group_id == group_id,
            Survey.title == survey_title
        )
        existing_survey_result = await session.execute(existing_survey_stmt)
        existing_survey = existing_survey_result.scalars().first()
        
        if existing_survey:
            await msg.answer(
                f"⚠️ Опрос с названием '{survey_title}' уже существует для группы '{group_name}'.\n"
                "Пожалуйста, выберите другое название."
            )
            return  # Сохраняем состояние FSM для повторного ввода
        
        # Создаем запись опроса с названием
        new_survey = Survey(group_id=group_id, title=survey_title)
        session.add(new_survey)
        await session.commit()
        await session.refresh(new_survey)
        survey_id = new_survey.id
        logger.info(f"Created Survey record with ID {survey_id}, title '{survey_title}' for group '{group_name}' (ID: {group_id})")
    
    success_message = (
        f"✅ Опрос '{survey_title}' для группы '{group_name}' успешно создан!\n\n"
        f"• Добавьте вопросы к опросу с помощью команды /set_questions\n"
        f"• После добавления вопросов, используйте /send_now для отправки опроса студентам"
    )
    
    await msg.answer(success_message)
    await state.clear()

# ----- Send Survey Command -----
@router.message(Command("send_now"))
@curator_guard
async def send_now_start(msg: Message, state: FSMContext):
    """Starts the flow to send an existing survey."""
    # Cancel previous operation if any
    current_state = await state.get_state()
    if current_state is not None:
        logger.info(f"User {msg.from_user.id} initiated /send_now, cancelling previous state: {current_state}")
        await state.clear()
        await msg.answer("(Предыдущая операция отменена)")
        
    if msg.text.strip() != "/send_now":
        await msg.answer("Пожалуйста, используйте команду /send_now без аргументов.")
        return

    builder = await get_course_selection_keyboard(callback_prefix="ss_select_course")
    if builder is None:
        await msg.answer(NO_COURSES_FOUND)
        return

    await msg.answer("1/3: Выберите курс для отправки опроса:", reply_markup=builder.as_markup())
    await state.set_state(SendSurveyStates.selecting_course)

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
        await callback.message.edit_text("В этом курсе нет групп. Сначала создайте группу (/set_group).")
        await callback.answer()
        await state.clear()
        return

    await state.update_data(course_id=course_id)
    await callback.message.edit_text("2/3: Выберите группу для отправки опроса:", reply_markup=group_builder.as_markup())
    await state.set_state(SendSurveyStates.selecting_group)
    await callback.answer()

@router.callback_query(SendSurveyStates.selecting_group, F.data.startswith("ss_select_group:"))
async def send_now_group_selected(callback: CallbackQuery, state: FSMContext):
    """Handles group selection and shows available surveys for the group."""
    try:
        group_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка выбора группы.", show_alert=True)
        await state.clear()
        return

    async with async_session() as session:
        # Проверяем существование группы
        group = await session.get(Group, group_id)
        if not group:
            await callback.message.edit_text("Ошибка: Выбранная группа не найдена.")
            await callback.answer()
            await state.clear()
            return
        
        # Получаем список опросов для данной группы
        surveys_stmt = select(Survey).where(Survey.group_id == group_id).order_by(Survey.started_at.desc())
        surveys_result = await session.execute(surveys_stmt)
        surveys = surveys_result.scalars().all()
        
        if not surveys:
            await callback.message.edit_text(
                f"⚠️ Для группы '{group.name}' не создано ни одного опроса.\n"
                "Сначала создайте опрос с помощью команды /create_survey"
            )
            await callback.answer()
            await state.clear()
            return
        
        # Проверяем наличие вопросов в опросах группы
        has_questions = False
        for survey in surveys:
            questions_stmt = select(Question).where(Question.survey_id == survey.id).limit(1)
            questions_result = await session.execute(questions_stmt)
            if questions_result.scalars().first() is not None:
                has_questions = True
                break
        
        if not has_questions:
            logger.warning(f"Survey selection cancelled for group '{group.name}': No questions found in any survey.")
            await callback.message.edit_text(
                f"⚠️ Для группы '{group.name}' не заданы вопросы ни в одном опросе.\n\n"
                f"Сначала добавьте вопросы с помощью команды /set_questions."
            )
            await callback.answer()
            await state.clear()
            return
        
        # Проверяем наличие студентов в группе
        students_stmt = (
            select(Student)
            .join(GroupStudent, Student.id == GroupStudent.student_id)
            .where(GroupStudent.group_id == group_id)
        )
        students_result = await session.execute(students_stmt)
        students = students_result.scalars().all()
        
        if not students:
            await callback.message.edit_text(
                f"⚠️ В группе '{group.name}' нет студентов. Добавьте студентов с помощью /set_recipients или /add_recipient"
            )
            await callback.answer()
            await state.clear()
            return
    
    # Создаем клавиатуру с опросами
    builder = InlineKeyboardBuilder()
    for survey in surveys:
        # Ограничиваем длину названия для кнопки, если оно слишком длинное
        button_text = survey.title if len(survey.title) <= 30 else f"{survey.title[:27]}..."
        builder.add(InlineKeyboardButton(
            text=button_text,
            callback_data=f"ss_select_survey:{survey.id}"
        ))
    builder.adjust(1)  # По одной кнопке в ряд для лучшей читаемости
    
    await state.update_data(group_id=group_id, group_name=group.name)
    await callback.message.edit_text(
        f"3/3: Выберите опрос для отправки группе '{group.name}':",
        reply_markup=builder.as_markup()
    )
    await state.set_state(SendSurveyStates.selecting_survey)
    await callback.answer()

@router.callback_query(SendSurveyStates.selecting_survey, F.data.startswith("ss_select_survey:"))
async def send_now_survey_selected(callback: CallbackQuery, state: FSMContext, bot: Bot, dp_instance: Dispatcher):
    """Handles survey selection and starts sending to students."""
    try:
        survey_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка выбора опроса.", show_alert=True)
        await state.clear()
        return
    
    data = await state.get_data()
    group_id = data.get("group_id")
    group_name = data.get("group_name", "Неизвестная группа")
    
    if not group_id:
        await callback.message.edit_text("Ошибка: Потерян контекст группы. Начните сначала с /send_now.")
        await callback.answer()
        await state.clear()
        return
    
    # Получаем данные опроса и студентов
    async with async_session() as session:
        # Получаем опрос
        survey = await session.get(Survey, survey_id)
        if not survey or survey.group_id != group_id:
            await callback.message.edit_text("Ошибка: Выбранный опрос не найден или не принадлежит выбранной группе.")
            await callback.answer()
            await state.clear()
            return
        
        survey_title = survey.title
        
        # Получаем вопросы для опроса
        questions_stmt = select(Question).where(Question.survey_id == survey_id).order_by(Question.order)
        questions_result = await session.execute(questions_stmt)
        questions = questions_result.scalars().all()
        
        if not questions:
            logger.warning(f"Survey send cancelled for group '{group_name}': No questions found.")
            await callback.message.edit_text(
                f"⚠️ Невозможно отправить опрос '{survey_title}' для группы '{group_name}':\n\n"
                f"В опросе отсутствуют вопросы. Добавьте вопросы с помощью команды /set_questions."
            )
            await callback.answer()
            await state.clear()
            return
            
        # Дополнительная проверка на количество вопросов
        if len(questions) == 0:
            logger.warning(f"Survey send cancelled for group '{group_name}': Question list is empty.")
            await callback.message.edit_text(
                f"⚠️ Невозможно отправить опрос '{survey_title}' для группы '{group_name}':\n\n"
                f"Список вопросов пуст. Добавьте вопросы с помощью команды /set_questions."
            )
            await callback.answer()
            await state.clear()
            return
        
        first_question = questions[0]
        
        # Получаем студентов
        students_stmt = (
            select(Student)
            .join(GroupStudent, Student.id == GroupStudent.student_id)
            .where(GroupStudent.group_id == group_id)
        )
        students_result = await session.execute(students_stmt)
        students = students_result.scalars().all()
        reachable_students = [s for s in students if s.tg_user_id is not None]
    
    # Сообщаем о начале отправки
    await callback.message.edit_text(f"Отправка опроса '{survey_title}' студентам группы '{group_name}'...")
    
    # Инициируем отправку последовательно с задержками
    successful_sends = 0
    
    for i, student in enumerate(reachable_students):
        # Small delay between sends to prevent rate limiting
        if i > 0:
            await asyncio.sleep(0.1)  # 100ms delay between sends
            
        result = await initiate_survey_for_student(bot, dp_instance, student, first_question, survey_id)
        if result:
            successful_sends += 1
    
    # Report results
    student_count = len(reachable_students)
    unreachable_count = len(students) - len(reachable_students)
    
    status_message = (
        f"✅ Опрос '{survey_title}' отправлен {successful_sends} из {student_count} студентов группы '{group_name}'."
    )
    
    if successful_sends < student_count:
        status_message += f"\n⚠️ Не удалось отправить {student_count - successful_sends} студентам (возможно, бот заблокирован)."
    
    if unreachable_count > 0:
        status_message += f"\nℹ️ {unreachable_count} студентов не запускали бота и не получили опрос."
        
    await callback.message.edit_text(status_message)
    await callback.answer()
    await state.clear()

# Helper function to send the first question and set student state
async def initiate_survey_for_student(bot: Bot, dp: Dispatcher, student: Student, first_question: Question, survey_id: int):
    if not student.tg_user_id:
        logger.warning(f"Cannot initiate survey for student '{student.tg_username}' (ID: {student.id}) - missing tg_user_id")
        return False # Indicate failure

    # Проверяем, что вопрос существует
    if not first_question or not first_question.text:
        logger.warning(f"Cannot initiate survey for student '{student.tg_username}' (ID: {student.id}) - invalid first question")
        return False # Indicate failure
    
    try:
        # Получаем информацию о группе и курсе
        async with async_session() as session:
            survey = await session.get(Survey, survey_id)
            if not survey:
                logger.error(f"Cannot initiate survey: Survey ID {survey_id} not found")
                return False
                
            group = await session.get(Group, survey.group_id)
            if not group:
                logger.error(f"Cannot initiate survey: Group for Survey ID {survey_id} not found")
                return False
            
            # Формируем сообщение для студента
            course_stmt = select(Course).where(Course.id == group.course_id)
            course_result = await session.execute(course_stmt)
            course = course_result.scalars().first()
            course_name = course.name if course else "Неизвестный курс"
            
            # Создаем сообщение с информацией об опросе
            survey_info = f"📊 <b>Опрос '{survey.title}' по курсу '{course_name}'</b>\n\n"
            
            # Создаем клавиатуру в зависимости от типа вопроса
            if first_question.q_type == QuestionType.scale:
                keyboard = get_scale_keyboard()
                survey_info += f"<b>Вопрос 1:</b> {first_question.text}\n\nОцените по шкале от 1 до 10:"
            else:
                keyboard = get_skip_keyboard()
                survey_info += f"<b>Вопрос 1:</b> {first_question.text}\n\nВведите ваш ответ или нажмите «Пропустить»:"
        
        # Отправляем первый вопрос
        await bot.send_message(
            chat_id=student.tg_user_id,
            text=survey_info,
            reply_markup=keyboard.as_markup()
        )
        
        # Устанавливаем состояние студента для дальнейших ответов
        state = dp.fsm.get_context(bot, student.tg_user_id, student.tg_user_id)
        await state.set_state(SurveyResponseStates.answering)
        await state.update_data(
            current_question_id=first_question.id,
            survey_id=survey_id,
            course_name=course_name,
            group_name=group.name,
            survey_title=survey.title,
            question_type=first_question.q_type,
            question_order=1
        )
        
        logger.info(f"Survey started for student '{student.tg_username}' (Telegram ID: {student.tg_user_id})")
        return True # Indicate success
        
    except TelegramForbiddenError:
        logger.warning(f"Student '{student.tg_username}' (Telegram ID: {student.tg_user_id}) has blocked the bot")
        return False
    except TelegramBadRequest as e:
        logger.warning(f"Failed to send survey to student '{student.tg_username}' ({student.tg_user_id}): {e}")
        return False
    except Exception as e:
        logger.exception(f"Unexpected error sending survey to student '{student.tg_username}' ({student.tg_user_id}): {e}")
        return False
