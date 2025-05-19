import logging
from sqlalchemy import text
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from sqlmodel import select

from config import settings
from curator_flow.group_handlers import curator_guard
from db import async_session
from models import Course, Group, Question, Survey, Response
from utils.keyboards import get_course_selection_keyboard, get_group_selection_keyboard, get_confirmation_keyboard
from utils.constants import NO_COURSES_FOUND

logger = logging.getLogger(__name__)
router = Router()

# ----- FSM States -----
class ListSurveysStates(StatesGroup):
    selecting_course = State()
    selecting_group = State()

class CleanupSurveysStates(StatesGroup):
    selecting_course = State()
    selecting_group = State()
    confirming = State()

# ----- List Surveys Command -----
@router.message(Command("list_surveys"))
@curator_guard
async def list_surveys_start(msg: Message, state: FSMContext):
    """Starts the flow to list surveys for a group."""
    # Cancel previous operation if any
    current_state = await state.get_state()
    if current_state is not None:
        logger.info(f"User {msg.from_user.id} initiated /list_surveys, cancelling previous state: {current_state}")
        await state.clear()
        await msg.answer("(Предыдущая операция отменена)")
        
    builder = await get_course_selection_keyboard(callback_prefix="ls_select_course")
    if builder is None:
        await msg.answer(NO_COURSES_FOUND)
        return

    await msg.answer("1/2: Выберите курс для просмотра опросов:", reply_markup=builder.as_markup())
    await state.set_state(ListSurveysStates.selecting_course)

@router.callback_query(ListSurveysStates.selecting_course, F.data.startswith("ls_select_course:"))
async def list_surveys_course_selected(callback: CallbackQuery, state: FSMContext):
    """Handles course selection and shows group keyboard for listing surveys."""
    try:
        course_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка выбора курса.", show_alert=True)
        await state.clear()
        return

    # Get course name for display
    async with async_session() as session:
        course = await session.get(Course, course_id)
        course_name = course.name if course else "Неизвестный курс"

    group_builder = await get_group_selection_keyboard(course_id, callback_prefix="ls_select_group")
    if group_builder is None:
        await callback.message.edit_text(f"В курсе '{course_name}' нет групп. Сначала создайте группу (/set_group).")
        await callback.answer()
        await state.clear()
        return

    await state.update_data(course_id=course_id, course_name=course_name)
    await callback.message.edit_text(f"2/2: Выберите группу для просмотра опросов:", reply_markup=group_builder.as_markup())
    await state.set_state(ListSurveysStates.selecting_group)
    await callback.answer()

@router.callback_query(ListSurveysStates.selecting_group, F.data.startswith("ls_select_group:"))
async def list_surveys_group_selected(callback: CallbackQuery, state: FSMContext):
    """Handles group selection and displays all surveys for the selected group."""
    try:
        group_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка выбора группы.", show_alert=True)
        await state.clear()
        return

    data = await state.get_data()
    course_name = data.get("course_name", "Неизвестный курс")

    async with async_session() as session:
        # Verify group exists
        group = await session.get(Group, group_id)
        if not group:
            await callback.message.edit_text("Ошибка: Выбранная группа не найдена.")
            await callback.answer()
            await state.clear()
            return
        
        # Get surveys for this group
        surveys_stmt = select(Survey).where(Survey.group_id == group_id).order_by(Survey.started_at.desc())
        surveys_result = await session.execute(surveys_stmt)
        surveys = surveys_result.scalars().all()

        # Get surveys with titles (non-empty title)
        surveys_with_title = [s for s in surveys if s.title.strip()]
        
        # If we only want to show titled surveys, use this instead:
        # surveys = surveys_with_title

    if not surveys:
        await callback.message.edit_text(
            f"📋 <b>Список опросов группы '{group.name}' (курс '{course_name}')</b>\n\n"
            f"В этой группе нет созданных опросов.\n\n"
            f"Используйте /create_survey, чтобы создать новый опрос."
        )
        await callback.answer()
        await state.clear()
        return

    # Format list of surveys with details
    message_parts = [f"📋 <b>Список опросов группы '{group.name}' (курс '{course_name}')</b>\n"]
    message_parts.append(f"Всего опросов: {len(surveys_with_title)} (показано {len(surveys)})\n")

    for i, survey in enumerate(surveys, 1):
        # Get question count for this survey
        async with async_session() as session:
            questions_count_stmt = select(Question).where(Question.survey_id == survey.id)
            questions_result = await session.execute(questions_count_stmt)
            questions = questions_result.scalars().all()
            question_count = len(questions)
            
            # Get response count for this survey
            response_count_stmt = text(f"SELECT COUNT(DISTINCT student_tg_id) FROM response WHERE survey_id = {survey.id}")
            response_count_result = await session.execute(response_count_stmt)
            response_count = response_count_result.scalar() or 0
        
        # Get formatted date
        survey_date = survey.started_at.strftime("%d.%m.%Y %H:%M")
        
        # Use placeholder title for empty titles
        display_title = survey.title.strip() if survey.title.strip() else f"Опрос от {survey_date}"
        
        # Add survey details
        message_parts.append(
            f"<b>{i}. {display_title}</b>\n"
            f"📝 Вопросов: {question_count}\n"
            f"👤 Ответили: {response_count} студентов\n"
            f"🕓 Создан: {survey_date}\n"
        )

    # Add help commands at the bottom
    message_parts.append(
        "\n<i>Команды для работы с опросами:</i>\n"
        "• /create_survey — создать новый опрос\n"
        "• /set_questions — задать вопросы для опроса\n"
        "• /send_now — отправить опрос студентам"
    )

    # If there are unnamed surveys, add cleanup tip for admins
    if len(surveys) > len(surveys_with_title) and callback.from_user.id in settings.admin_id_set:
        message_parts.append("\n<i>Администраторам:</i> Используйте /cleanup_surveys для удаления устаревших опросов без названия")

    # Combine parts into message (this ensures we don't exceed message limits)
    message_text = "\n".join(message_parts)
    
    await callback.message.edit_text(message_text)
    await callback.answer()
    await state.clear()

# ----- Cleanup Surveys Command (Admin Only) -----
@router.message(Command("cleanup_surveys"))
async def cleanup_surveys_start(msg: Message, state: FSMContext):
    """Starts the flow to clean up unnamed surveys with no questions/responses."""
    # Admin authorization check - check both ID and username
    user_id = msg.from_user.id
    username = msg.from_user.username
    is_admin = False
    
    if user_id in settings.admin_id_set:
        is_admin = True
    elif username and f"@{username.lower()}" in settings.admin_id_set:
        is_admin = True
    
    if not is_admin:
        logger.warning(f"Non-admin user {user_id} (@{username}) attempted to access /cleanup_surveys")
        await msg.answer("⛔ Эта команда доступна только администраторам.")
        return
        
    # Cancel previous operation if any
    current_state = await state.get_state()
    if current_state is not None:
        logger.info(f"Admin {msg.from_user.id} initiated /cleanup_surveys, cancelling previous state: {current_state}")
        await state.clear()
        await msg.answer("(Предыдущая операция отменена)")
        
    builder = await get_course_selection_keyboard(callback_prefix="cs_cleanup_course")
    if builder is None:
        await msg.answer(NO_COURSES_FOUND)
        return

    await msg.answer("1/3: Выберите курс для очистки опросов:", reply_markup=builder.as_markup())
    await state.set_state(CleanupSurveysStates.selecting_course)

@router.callback_query(CleanupSurveysStates.selecting_course, F.data.startswith("cs_cleanup_course:"))
async def cleanup_surveys_course_selected(callback: CallbackQuery, state: FSMContext):
    """Handles course selection for survey cleanup."""
    try:
        course_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка выбора курса.", show_alert=True)
        await state.clear()
        return

    # Get course name for display
    async with async_session() as session:
        course = await session.get(Course, course_id)
        course_name = course.name if course else "Неизвестный курс"

    group_builder = await get_group_selection_keyboard(course_id, callback_prefix="cs_cleanup_group")
    if group_builder is None:
        await callback.message.edit_text(f"В курсе '{course_name}' нет групп.")
        await callback.answer()
        await state.clear()
        return

    await state.update_data(course_id=course_id, course_name=course_name)
    await callback.message.edit_text(f"2/3: Выберите группу для очистки опросов:", reply_markup=group_builder.as_markup())
    await state.set_state(CleanupSurveysStates.selecting_group)
    await callback.answer()

@router.callback_query(CleanupSurveysStates.selecting_group, F.data.startswith("cs_cleanup_group:"))
async def cleanup_surveys_group_selected(callback: CallbackQuery, state: FSMContext):
    """Handles group selection and shows surveys that will be cleaned up."""
    try:
        group_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка выбора группы.", show_alert=True)
        await state.clear()
        return

    data = await state.get_data()
    course_name = data.get("course_name", "Неизвестный курс")

    async with async_session() as session:
        # Verify group exists
        group = await session.get(Group, group_id)
        if not group:
            await callback.message.edit_text("Ошибка: Выбранная группа не найдена.")
            await callback.answer()
            await state.clear()
            return
        
        # Find surveys to clean up (empty titles with no questions or responses)
        surveys_to_clean = []
        
        # Get all surveys for this group
        surveys_stmt = select(Survey).where(Survey.group_id == group_id)
        surveys_result = await session.execute(surveys_stmt)
        surveys = surveys_result.scalars().all()
        
        for survey in surveys:
            # Skip if has a title
            if survey.title.strip():
                continue
                
            # Check if has questions
            questions_stmt = select(Question).where(Question.survey_id == survey.id)
            questions_result = await session.execute(questions_stmt)
            has_questions = questions_result.scalars().first() is not None
            
            # Check if has responses
            responses_stmt = select(Response).where(Response.survey_id == survey.id)
            responses_result = await session.execute(responses_stmt)
            has_responses = responses_result.scalars().first() is not None
            
            # Add to cleanup list if no questions AND no responses
            if not has_questions and not has_responses:
                surveys_to_clean.append(survey)
        
    if not surveys_to_clean:
        await callback.message.edit_text(
            f"✅ Группа '{group.name}' (курс '{course_name}') не нуждается в очистке.\n\n"
            f"Не найдено пустых опросов без названия."
        )
        await callback.answer()
        await state.clear()
        return
    
    # Save data for confirmation step
    await state.update_data(group_id=group_id, group_name=group.name, surveys_to_clean_count=len(surveys_to_clean))
    
    # Show confirmation dialog
    confirmation_builder = await get_confirmation_keyboard(
        yes_callback="cs_cleanup_confirm:yes", 
        no_callback="cs_cleanup_confirm:no"
    )
    
    await callback.message.edit_text(
        f"3/3: Подтверждение очистки опросов группы '{group.name}' (курс '{course_name}')\n\n"
        f"Будет удалено {len(surveys_to_clean)} опросов без названия, вопросов и ответов.\n\n"
        f"Продолжить?",
        reply_markup=confirmation_builder.as_markup()
    )
    await state.set_state(CleanupSurveysStates.confirming)
    await callback.answer()

@router.callback_query(CleanupSurveysStates.confirming, F.data.startswith("cs_cleanup_confirm:"))
async def cleanup_surveys_confirm(callback: CallbackQuery, state: FSMContext):
    """Handles confirmation and performs the cleanup if confirmed."""
    choice = callback.data.split(":")[1]
    
    if choice != "yes":
        await callback.message.edit_text("Очистка опросов отменена.")
        await callback.answer()
        await state.clear()
        return
    
    data = await state.get_data()
    group_id = data.get("group_id")
    group_name = data.get("group_name", "Неизвестная группа")
    course_name = data.get("course_name", "Неизвестный курс")
    
    if not group_id:
        await callback.message.edit_text("Ошибка: Не найден идентификатор группы.")
        await callback.answer()
        await state.clear()
        return
    
    # Perform the cleanup
    clean_count = 0
    async with async_session() as session:
        # Find surveys to clean up (empty titles with no questions or responses)
        surveys_stmt = select(Survey).where(Survey.group_id == group_id)
        surveys_result = await session.execute(surveys_stmt)
        surveys = surveys_result.scalars().all()
        
        for survey in surveys:
            # Skip if has a title
            if survey.title.strip():
                continue
                
            # Check if has questions
            questions_stmt = select(Question).where(Question.survey_id == survey.id)
            questions_result = await session.execute(questions_stmt)
            has_questions = questions_result.scalars().first() is not None
            
            # Check if has responses
            responses_stmt = select(Response).where(Response.survey_id == survey.id)
            responses_result = await session.execute(responses_stmt)
            has_responses = responses_result.scalars().first() is not None
            
            # Delete if no questions AND no responses
            if not has_questions and not has_responses:
                await session.delete(survey)
                clean_count += 1
        
        # Commit the changes
        await session.commit()
    
    # Report results
    await callback.message.edit_text(
        f"✅ Очистка опросов для группы '{group_name}' (курс '{course_name}') завершена.\n\n"
        f"Удалено {clean_count} пустых опросов без названия."
    )
    await callback.answer()
    await state.clear() 