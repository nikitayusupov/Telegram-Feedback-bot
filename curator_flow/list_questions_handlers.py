import logging
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from sqlmodel import select
from aiogram.utils.keyboard import InlineKeyboardBuilder

from curator_flow.group_handlers import curator_guard
from db import async_session
from models import Course, Group, Question, Survey
from utils.keyboards import get_course_selection_keyboard, get_group_selection_keyboard
from utils.constants import NO_COURSES_FOUND

logger = logging.getLogger(__name__)
router = Router()

# ----- FSM States -----
class ListQuestionsStates(StatesGroup):
    selecting_course = State()
    selecting_group = State()
    selecting_survey = State()

# ----- List Questions Command -----
@router.message(Command("list_questions"))
@curator_guard
async def list_questions_start(msg: Message, state: FSMContext):
    """Starts the flow to list questions for a survey."""
    # Cancel previous operation if any
    current_state = await state.get_state()
    if current_state is not None:
        logger.info(f"User {msg.from_user.id} initiated /list_questions, cancelling previous state: {current_state}")
        await state.clear()
        await msg.answer("(Предыдущая операция отменена)")
        
    builder = await get_course_selection_keyboard(callback_prefix="lq_select_course")
    if builder is None:
        await msg.answer(NO_COURSES_FOUND)
        return

    await msg.answer("1/3: Выберите курс для просмотра вопросов:", reply_markup=builder.as_markup())
    await state.set_state(ListQuestionsStates.selecting_course)

@router.callback_query(ListQuestionsStates.selecting_course, F.data.startswith("lq_select_course:"))
async def list_questions_course_selected(callback: CallbackQuery, state: FSMContext):
    """Handles course selection and shows group keyboard for listing questions."""
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

    group_builder = await get_group_selection_keyboard(course_id, callback_prefix="lq_select_group")
    if group_builder is None:
        await callback.message.edit_text(f"В курсе '{course_name}' нет групп. Сначала создайте группу (/set_group).")
        await callback.answer()
        await state.clear()
        return

    await state.update_data(course_id=course_id, course_name=course_name)
    await callback.message.edit_text(f"2/3: Выберите группу для просмотра вопросов:", reply_markup=group_builder.as_markup())
    await state.set_state(ListQuestionsStates.selecting_group)
    await callback.answer()

@router.callback_query(ListQuestionsStates.selecting_group, F.data.startswith("lq_select_group:"))
async def list_questions_group_selected(callback: CallbackQuery, state: FSMContext):
    """Handles group selection and shows available surveys."""
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
        
        # Get available surveys for this group
        surveys_stmt = select(Survey).where(Survey.group_id == group_id).order_by(Survey.started_at.desc())
        surveys_result = await session.execute(surveys_stmt)
        surveys = surveys_result.scalars().all()
        
        if not surveys:
            await callback.message.edit_text(
                f"Для группы '{group.name}' нет опросов. "
                "Сначала создайте опрос с помощью команды /create_survey."
            )
            await callback.answer()
            await state.clear()
            return

    # Build keyboard with available surveys
    builder = InlineKeyboardBuilder()
    for survey in surveys:
        # Use survey title or fallback to date
        display_title = survey.title.strip() if survey.title.strip() else f"Опрос от {survey.started_at.strftime('%d.%m.%Y %H:%M')}"
        builder.row(InlineKeyboardButton(
            text=display_title,
            callback_data=f"lq_select_survey:{survey.id}"
        ))

    await state.update_data(group_id=group_id, group_name=group.name)
    await callback.message.edit_text(
        f"3/3: Выберите опрос для просмотра вопросов (группа '{group.name}'):",
        reply_markup=builder.as_markup()
    )
    await state.set_state(ListQuestionsStates.selecting_survey)
    await callback.answer()

@router.callback_query(ListQuestionsStates.selecting_survey, F.data.startswith("lq_select_survey:"))
async def list_questions_survey_selected(callback: CallbackQuery, state: FSMContext):
    """Handles survey selection and displays all questions for the selected survey."""
    try:
        survey_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка выбора опроса.", show_alert=True)
        await state.clear()
        return

    data = await state.get_data()
    course_name = data.get("course_name", "Неизвестный курс")
    group_name = data.get("group_name", "Неизвестная группа")

    async with async_session() as session:
        # Verify survey exists
        survey = await session.get(Survey, survey_id)
        if not survey:
            await callback.message.edit_text("Ошибка: Выбранный опрос не найден.")
            await callback.answer()
            await state.clear()
            return
        
        # Get questions for this survey
        questions_stmt = select(Question).where(Question.survey_id == survey_id).order_by(Question.order)
        questions_result = await session.execute(questions_stmt)
        questions = questions_result.scalars().all()

    if not questions:
        survey_title = survey.title.strip() if survey.title.strip() else f"Опрос от {survey.started_at.strftime('%d.%m.%Y %H:%M')}"
        await callback.message.edit_text(
            f"❓ <b>Вопросы опроса '{survey_title}'</b>\n"
            f"Группа: {group_name} (курс '{course_name}')\n\n"
            f"В этом опросе нет вопросов.\n\n"
            f"Используйте /set_questions, чтобы добавить вопросы."
        )
        await callback.answer()
        await state.clear()
        return

    # Format list of questions
    survey_title = survey.title.strip() if survey.title.strip() else f"Опрос от {survey.started_at.strftime('%d.%m.%Y %H:%M')}"
    message_parts = [
        f"❓ <b>Вопросы опроса '{survey_title}'</b>",
        f"Группа: {group_name} (курс '{course_name}')",
        f"Всего вопросов: {len(questions)}\n"
    ]

    for i, question in enumerate(questions, 1):
        # Format question type for display
        if question.q_type.value == "scale":
            type_display = "Шкала 1-10"
        elif question.q_type.value == "text":
            type_display = "Текстовый ответ"
        else:
            type_display = question.q_type.value
        
        message_parts.append(
            f"<b>{i}. {question.text}</b>\n"
            f"   Тип: {type_display}\n"
        )

    # Add help commands at the bottom
    message_parts.append(
        "\n<i>Команды для работы с вопросами:</i>\n"
        "• /set_questions — изменить вопросы опроса\n"
        "• /send_now — отправить опрос студентам"
    )

    # Combine parts into message
    message_text = "\n".join(message_parts)
    
    await callback.message.edit_text(message_text)
    await callback.answer()
    await state.clear() 