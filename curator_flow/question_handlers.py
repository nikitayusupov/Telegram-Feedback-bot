import logging
from typing import List, Dict, Any, Union

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import delete # For deleting old questions
from sqlmodel import select

# Assuming curator_guard is correctly defined and imported
from curator_flow.group_handlers import curator_guard 
from db import async_session
from models import Group, Question, QuestionType, Survey
from utils.keyboards import (
    get_course_selection_keyboard, 
    get_group_selection_keyboard,
    get_question_type_keyboard
)
from utils.constants import NO_COURSES_FOUND, MAX_QUESTIONS

logger = logging.getLogger(__name__)
router = Router()

# ----- FSM States -----
class SetQuestionsStates(StatesGroup):
    selecting_course = State()
    selecting_group = State()
    selecting_survey = State()
    confirming_overwrite = State() # New state for confirmation
    selecting_question_type = State()
    entering_question_text = State()

# ----- Helper to Ask Next Question or Finish -----
async def ask_next_question_or_finish(message_or_callback: Union[Message, CallbackQuery], state: FSMContext):
    # Determine if we need to edit or send a new message
    if isinstance(message_or_callback, CallbackQuery):
        msg_to_edit = message_or_callback.message
        answer_func = msg_to_edit.edit_text
    else:
        msg_to_edit = message_or_callback # It's already a Message
        answer_func = msg_to_edit.answer
        
    data = await state.get_data()
    questions_list: List[Dict[str, Any]] = data.get("questions", [])
    question_number = len(questions_list) + 1

    if question_number > MAX_QUESTIONS:
        # Limit reached, automatically save and finish
        await save_questions_and_finish(message_or_callback, state)
        return

    # Call the imported keyboard function, passing MAX_QUESTIONS
    keyboard = get_question_type_keyboard(len(questions_list), MAX_QUESTIONS) 
    prompt_text = (
        f"Вопрос {question_number}/{MAX_QUESTIONS}. Выберите тип вопроса или завершите:" if question_number > 1 
        else f"Вопрос {question_number}/{MAX_QUESTIONS}. Выберите тип первого вопроса:"
    )
    # Use answer_func to either edit the existing message or send a new one
    await answer_func(prompt_text, reply_markup=keyboard.as_markup())
    await state.set_state(SetQuestionsStates.selecting_question_type)
    # Acknowledge callback if it was one
    if isinstance(message_or_callback, CallbackQuery):
        await message_or_callback.answer()

# ----- Helper to Save Questions -----
async def save_questions_and_finish(message_or_callback: Union[Message, CallbackQuery], state: FSMContext):
    msg = message_or_callback if isinstance(message_or_callback, Message) else message_or_callback.message
    
    data = await state.get_data()
    questions_list: List[Dict[str, Any]] = data.get("questions", [])
    survey_id = data.get("survey_id")
    survey_title = data.get("survey_title", "Unknown Survey")

    if not survey_id:
        logger.error("Survey ID missing in state during save_questions_and_finish")
        await msg.answer("Ошибка: Потерян ID опроса. Начните заново.")
        await state.clear()
        return

    if not questions_list:
        await msg.answer("Вы не добавили ни одного вопроса. Процесс отменен.")
        await state.clear()
        return

    async with async_session() as session:
        try:
            # 1. Delete existing questions for this survey
            delete_stmt = delete(Question).where(Question.survey_id == survey_id)
            await session.execute(delete_stmt)
            logger.info(f"Deleted existing questions for survey ID {survey_id}")

            # 2. Add new questions
            new_questions = []
            for i, q_data in enumerate(questions_list):
                new_q = Question(
                    survey_id=survey_id,
                    text=q_data['text'],
                    q_type=q_data['type'],
                    order=i + 1 # Order is 1-based
                )
                session.add(new_q)
                new_questions.append(new_q)
            
            await session.commit()
            logger.info(f"Saved {len(new_questions)} questions for survey '{survey_title}' (ID: {survey_id})")
            await msg.answer(f"✅ Сохранено {len(new_questions)} вопросов для опроса '{survey_title}'.")

        except Exception as e:
            logger.exception(f"Error saving questions for survey {survey_id}: {e}")
            await session.rollback()
            await msg.answer("Произошла ошибка при сохранении вопросов. Попробуйте позже.")

    await state.clear()

# ----- Command and State Handlers -----

@router.message(Command("set_questions"))
@curator_guard
async def set_questions_start(msg: Message, state: FSMContext):
    """Starts the flow to set questions for a survey."""
    # Cancel previous operation if any
    current_state = await state.get_state()
    if current_state is not None:
        logger.info(f"User {msg.from_user.id} initiated /set_questions, cancelling previous state: {current_state}")
        await state.clear()
        await msg.answer("(Предыдущая операция отменена)")
        
    if msg.text.strip() != "/set_questions":
        await msg.answer("Пожалуйста, используйте команду /set_questions без аргументов.")
        return

    builder = await get_course_selection_keyboard(callback_prefix="sq_select_course")
    if builder is None:
        await msg.answer(NO_COURSES_FOUND)
        return

    await msg.answer("1/4: Выберите курс:", reply_markup=builder.as_markup())
    await state.set_state(SetQuestionsStates.selecting_course)

@router.callback_query(SetQuestionsStates.selecting_course, F.data.startswith("sq_select_course:"))
async def set_questions_course_selected(callback: CallbackQuery, state: FSMContext):
    """Handles course selection and shows group keyboard."""
    try:
        course_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка выбора курса.", show_alert=True)
        await state.clear()
        return

    # Show group keyboard for the selected course
    group_builder = await get_group_selection_keyboard(course_id, callback_prefix="sq_select_group")
    if group_builder is None:
        await callback.message.edit_text("В этом курсе нет групп. Сначала создайте группу командой /set_group.")
        await callback.answer()
        await state.clear()
        return

    await state.update_data(course_id=course_id)
    await callback.message.edit_text("2/4: Выберите группу:", reply_markup=group_builder.as_markup())
    await state.set_state(SetQuestionsStates.selecting_group)
    await callback.answer()

@router.callback_query(SetQuestionsStates.selecting_group, F.data.startswith("sq_select_group:"))
async def set_questions_group_selected(callback: CallbackQuery, state: FSMContext):
    """Handles group selection and shows available surveys."""
    try:
        group_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка выбора группы.", show_alert=True)
        await state.clear()
        return
        
    async with async_session() as session:
        # Verify group exists and get name
        group = await session.get(Group, group_id)
        if not group:
            await callback.message.edit_text("Выбранная группа не найдена.")
            await callback.answer()
            await state.clear()
            return
        
        # Get available surveys for this group
        surveys_stmt = select(Survey).where(Survey.group_id == group_id).order_by(Survey.id)
        surveys_result = await session.execute(surveys_stmt)
        surveys = surveys_result.scalars().all()
        
        if not surveys:
            # No surveys exist, prompt to create one
            await callback.message.edit_text(
                f"Для группы '{group.name}' нет опросов. "
                "Сначала создайте опрос с помощью команды /create_survey."
            )
            await callback.answer()
            await state.clear()
            return

    # Build keyboard with available surveys
    survey_builder = InlineKeyboardBuilder()
    for survey in surveys:
        survey_builder.row(InlineKeyboardButton(
            text=f"{survey.title}",
            callback_data=f"sq_select_survey:{survey.id}"
        ))
    
    await state.update_data(group_id=group_id, group_name=group.name)
    await callback.message.edit_text("3/4: Выберите опрос для редактирования вопросов:", reply_markup=survey_builder.as_markup())
    await state.set_state(SetQuestionsStates.selecting_survey)
    await callback.answer()

@router.callback_query(SetQuestionsStates.selecting_survey, F.data.startswith("sq_select_survey:"))
async def set_questions_survey_selected(callback: CallbackQuery, state: FSMContext):
    """Handles survey selection and checks for existing questions."""
    try:
        survey_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка выбора опроса.", show_alert=True)
        await state.clear()
        return
        
    async with async_session() as session:
        # Verify survey exists and get title
        survey = await session.get(Survey, survey_id)
        if not survey:
            await callback.message.edit_text("Выбранный опрос не найден.")
            await callback.answer()
            await state.clear()
            return
            
        # Check for existing questions
        existing_questions_stmt = select(Question.id).where(Question.survey_id == survey_id).limit(1)
        existing_questions_result = await session.execute(existing_questions_stmt)
        has_existing_questions = existing_questions_result.scalars().first() is not None

    await state.update_data(survey_id=survey_id, survey_title=survey.title)

    if has_existing_questions:
        logger.info(f"Survey '{survey.title}' (ID: {survey_id}) already has questions. Asking for overwrite confirmation.")
        # Ask for confirmation
        confirm_builder = InlineKeyboardBuilder()
        confirm_builder.row(
            InlineKeyboardButton(text="✅ Да, удалить старые вопросы", callback_data="sq_confirm_overwrite:yes"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="sq_confirm_overwrite:no")
        )
        await callback.message.edit_text(
            f"Для опроса '{survey.title}' уже заданы вопросы. Хотите удалить их и создать новые?",
            reply_markup=confirm_builder.as_markup()
        )
        await state.set_state(SetQuestionsStates.confirming_overwrite)
        await callback.answer()
    else:
        # No existing questions, proceed directly
        logger.info(f"Survey '{survey.title}' (ID: {survey_id}) has no questions. Proceeding to add new ones.")
        await state.update_data(questions=[]) # Initialize empty list
        # Edit message before starting the flow
        await callback.message.edit_text(f"4/4: Выбран опрос '{survey.title}'. Начинаем добавлять вопросы.")
        # Start asking for the first question (pass callback to edit message)
        await ask_next_question_or_finish(callback, state)
        # No need for callback.answer() here as ask_next_question_or_finish handles it

# New handler for overwrite confirmation
@router.callback_query(SetQuestionsStates.confirming_overwrite, F.data.startswith("sq_confirm_overwrite:"))
async def set_questions_overwrite_confirmed(callback: CallbackQuery, state: FSMContext):
    """Handles the confirmation for overwriting existing questions."""
    action = callback.data.split(":")[1]

    if action == "yes":
        data = await state.get_data()
        survey_id = data.get("survey_id")
        survey_title = data.get("survey_title", "Unknown Survey")
        logger.info(f"User confirmed overwriting questions for survey '{survey_title}' (ID: {survey_id})")
        await state.update_data(questions=[]) # Initialize empty list for new questions
        # Start asking for the first question, editing the confirmation message
        await ask_next_question_or_finish(callback, state)
    elif action == "no":
        data = await state.get_data()
        survey_id = data.get("survey_id")
        survey_title = data.get("survey_title", "Unknown Survey")
        logger.info(f"User cancelled overwriting questions for survey '{survey_title}' (ID: {survey_id})")
        await callback.message.edit_text("Редактирование вопросов отменено.")
        await state.clear()
        await callback.answer()
    else:
        logger.warning(f"Invalid confirmation action received: {action}")
        await callback.answer("Некорректное действие.", show_alert=True)

@router.callback_query(SetQuestionsStates.selecting_question_type, F.data.startswith("sq_qtype:"))
async def set_questions_type_selected(callback: CallbackQuery, state: FSMContext):
    """Handles question type selection or finishing."""
    action = callback.data.split(":")[1]

    if action == "finish":
        await save_questions_and_finish(callback, state)
        return
    
    try:
        # Validate and store the question type enum
        question_type = QuestionType(action)
        await state.update_data(current_question_type=question_type)
        await state.set_state(SetQuestionsStates.entering_question_text)
        
        prompt = "Введите текст вопроса:"
        if question_type == QuestionType.scale:
            prompt += " (для шкалы от 1 до 5)"
        
        await callback.message.edit_text(prompt)
        await callback.answer()
    except ValueError:
        logger.error(f"Invalid question type received: {action}")
        await callback.answer("Некорректный тип вопроса.", show_alert=True)

@router.message(SetQuestionsStates.entering_question_text, F.text)
async def set_questions_text_entered(msg: Message, state: FSMContext):
    """Handles question text input and adds the question to the state."""
    data = await state.get_data()
    current_type = data.get("current_question_type")
    questions_list = data.get("questions", [])
    
    if not current_type:
        await msg.answer("Ошибка: Тип вопроса не был выбран. Пожалуйста, начните заново.")
        await state.clear()
        return
    
    # Add the question to our list
    question_text = msg.text.strip()
    questions_list.append({
        "type": current_type,
        "text": question_text
    })
    
    # Update state with the new list
    await state.update_data(questions=questions_list)
    
    # Ask for next question or finish
    await ask_next_question_or_finish(msg, state)
