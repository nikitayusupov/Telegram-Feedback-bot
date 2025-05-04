import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlmodel import select

# Import the guard from the central location
from utils.auth_checks import admin_guard

from db import async_session
from models import Course, Curator, CuratorCourse
from utils.keyboards import get_course_selection_keyboard
# Import the constant
from utils.constants import NO_COURSES_FOUND

logger = logging.getLogger(__name__)
router = Router()

# ----- FSM States -----
class AddCuratorStates(StatesGroup):
    selecting_course = State()
    entering_username = State()

# ----- Command Handler -----
@router.message(Command("add_curator"))
@admin_guard
async def add_curator_start(msg: Message, state: FSMContext):
    """Starts the flow to add a curator to a course."""
    # Cancel previous operation if any
    current_state = await state.get_state()
    if current_state is not None:
        logger.info(f"User {msg.from_user.id} initiated /add_curator, cancelling previous state: {current_state}")
        await state.clear()
        await msg.answer("(Предыдущая операция отменена)")
        
    if msg.text.strip() != "/add_curator":
        await msg.answer("Пожалуйста, используйте команду /add_curator без аргументов.")
        return

    builder = await get_course_selection_keyboard(callback_prefix="ac_select_course")
    if builder is None:
        await msg.answer(NO_COURSES_FOUND) # Use constant
        return

    await msg.answer("Выберите курс, к которому хотите добавить куратора:", reply_markup=builder.as_markup())
    await state.set_state(AddCuratorStates.selecting_course)

# ----- Callback Handler for Course Selection -----
@router.callback_query(AddCuratorStates.selecting_course, F.data.startswith("ac_select_course:"))
async def add_curator_course_selected(callback: CallbackQuery, state: FSMContext):
    """Handles course selection and prompts for curator username."""
    try:
        course_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка выбора курса.", show_alert=True)
        await state.clear()
        return

    # Check if course still exists
    async with async_session() as session:
        course = await session.get(Course, course_id)
        if not course:
             await callback.answer("Выбранный курс не найден.", show_alert=True)
             await state.clear()
             return
             
    await state.update_data(course_id=course_id, course_name=course.name)
    await state.set_state(AddCuratorStates.entering_username)
    await callback.message.edit_text(f"Выбран курс: {course.name}.\nТеперь введите Telegram username куратора (например, @username):")
    await callback.answer()

# ----- Message Handler for Username Input -----
@router.message(AddCuratorStates.entering_username, F.text)
async def add_curator_username_entered(msg: Message, state: FSMContext):
    """Handles username input, validates, finds/creates curator and links to course."""
    username = msg.text.strip()
    
    # Basic username validation
    if not username.startswith('@') or len(username) < 2:
        await msg.answer("Неверный формат username. Он должен начинаться с '@' и содержать хотя бы один символ после. Попробуйте снова.")
        return # Keep state

    data = await state.get_data()
    course_id = data.get("course_id")
    course_name = data.get("course_name", "Unknown Course")

    if not course_id:
         await msg.answer("Ошибка: Потерян контекст курса. Пожалуйста, начните сначала с /add_curator.")
         await state.clear()
         return
         
    # Store and compare without leading '@'
    username_lower = username.lower().lstrip('@') 

    async with async_session() as session:
        try:
            # 1. Find or Create Curator
            curator_result = await session.execute(
                select(Curator).where(Curator.tg_username == username_lower)
            )
            curator = curator_result.scalars().first()
            
            created_curator = False
            if not curator:
                curator = Curator(tg_username=username_lower)
                session.add(curator)
                await session.flush() # Flush to get curator ID before linking
                await session.refresh(curator)
                created_curator = True
                logger.info(f"Created new curator '{username_lower}' with ID {curator.id}")

            # 2. Check if link already exists
            link_result = await session.execute(
                select(CuratorCourse).where(
                    CuratorCourse.curator_id == curator.id, 
                    CuratorCourse.course_id == course_id
                )
            )
            existing_link = link_result.scalars().first()

            if existing_link:
                await msg.answer(f"Куратор {username} уже добавлен к курсу '{course_name}'.")
            else:
                # 3. Create CuratorCourse link
                new_link = CuratorCourse(curator_id=curator.id, course_id=course_id)
                session.add(new_link)
                await session.commit()
                logger.info(f"Linked curator {username_lower} (ID: {curator.id}) to course '{course_name}' (ID: {course_id})")
                
                if created_curator:
                     await msg.answer(f"✅ Новый куратор {username} создан и добавлен к курсу '{course_name}'.")
                else:
                     await msg.answer(f"✅ Куратор {username} добавлен к курсу '{course_name}'.")

        except Exception as e:
            logger.exception(f"Error adding curator {username} to course {course_id}: {e}")
            await session.rollback()
            await msg.answer("Произошла ошибка при добавлении куратора. Попробуйте позже.")

    await state.clear()
