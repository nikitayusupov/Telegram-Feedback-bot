# admin_flow/course_handlers.py
import logging

from aiogram import Router
from aiogram import F # Import F for filters
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from sqlmodel import select
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

from db import async_session
from models import Course, Group, CuratorCourse, CuratorGroup, GroupStudent
from utils.keyboards import get_course_selection_keyboard, get_confirmation_keyboard
from utils.auth_checks import admin_guard
from sqlalchemy import delete
from utils.constants import NO_COURSES_FOUND

logger = logging.getLogger(__name__)
router = Router()

# ----- FSM States for Admin Flow -----
class CreateCourseStates(StatesGroup):
    entering_name = State()

class DeleteCourseStates(StatesGroup):
    selecting_course = State()
    confirming_deletion = State()

# ----- Admin Course Commands ------------------------------

@router.message(Command("list_courses"))
@admin_guard
async def list_courses(msg: Message):
    """Lists all courses in the database."""
    async with async_session() as session:
        result = await session.execute(select(Course).order_by(Course.name))
        courses = result.scalars().all()
        
    if not courses:
        await msg.answer(NO_COURSES_FOUND)
        return

    response_lines = ["Available Courses:"]
    response_lines.extend([f"- {course.name} (ID: {course.id})" for course in courses])
    await msg.answer("\n".join(response_lines))

@router.message(Command("create_course"))
@admin_guard
async def create_course_start(msg: Message, state: FSMContext):
    """Starts the flow to create a new course."""
    # Cancel previous operation if any
    current_state = await state.get_state()
    if current_state is not None:
        logger.info(f"User {msg.from_user.id} initiated /create_course, cancelling previous state: {current_state}")
        await state.clear()
        await msg.answer("(Предыдущая операция отменена)")
        
    # Check if the user sent arguments with the command
    if msg.text.strip() != "/create_course":
        await msg.answer("Пожалуйста, используйте команду /create_course без аргументов.")
        return
        
    await state.set_state(CreateCourseStates.entering_name)
    await msg.answer("Хорошо, напишите название нового курса")

@router.message(CreateCourseStates.entering_name, F.text)
async def create_course_name_entered(msg: Message, state: FSMContext):
    """Handles entering the course name and creates the course."""
    course_name = msg.text.strip()
    if not course_name:
        await msg.answer("Название курса не может быть пустым. Пожалуйста, введите название.")
        return # Keep state for user to retry
        
    # Check length
    if len(course_name) > 100:
        await msg.answer("Название курса не может быть длиннее 100 символов. Пожалуйста, введите более короткое название.")
        return # Keep state

    # Prevent using commands as course names
    if course_name.startswith('/'):
        await msg.answer("Название курса не может начинаться с '/'. Пожалуйста, введите другое название.")
        return # Keep state

    async with async_session() as session:
        # Check if course already exists
        result = await session.execute(select(Course).where(Course.name == course_name))
        existing_course = result.scalars().first()
        
        if existing_course:
            await msg.answer(f"Курс '{course_name}' уже существует (ID: {existing_course.id}). Введите другое название или отмените (/cancel).")
            return # Keep state
            
        # Create and save the new course
        new_course = Course(name=course_name)
        session.add(new_course)
        await session.commit()
        await session.refresh(new_course) # To get the generated ID
        
        logger.info(f"Course '{new_course.name}' created with ID {new_course.id} by admin {msg.from_user.id}")
        await msg.answer(f"✅ Курс '{new_course.name}' успешно создан (ID: {new_course.id}).")
        await state.clear()

@router.message(Command("delete_course"))
@admin_guard
async def delete_course_start(msg: Message, state: FSMContext):
    """Starts the interactive course deletion flow."""
    # Cancel previous operation if any
    current_state = await state.get_state()
    if current_state is not None:
        logger.info(f"User {msg.from_user.id} initiated /delete_course, cancelling previous state: {current_state}")
        await state.clear()
        await msg.answer("(Предыдущая операция отменена)")
        
    # 1. Check for arguments
    if msg.text.strip() != "/delete_course":
        await msg.answer("Пожалуйста, используйте команду /delete_course без аргументов.")
        return
    
    # 2. Get course selection keyboard
    builder = await get_course_selection_keyboard(callback_prefix="del_select_course")
    
    # 3. Handle no courses
    if builder is None:
        await msg.answer(NO_COURSES_FOUND)
        return
        
    # 4. Show keyboard and set state
    await msg.answer("Выберите курс для удаления:", reply_markup=builder.as_markup())
    await state.set_state(DeleteCourseStates.selecting_course)

@router.callback_query(DeleteCourseStates.selecting_course, F.data.startswith("del_select_course:"))
async def delete_course_selected(callback: CallbackQuery, state: FSMContext):
    """Handles course selection and asks for confirmation before deletion."""
    try:
        course_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка выбора курса.", show_alert=True)
        await state.clear()
        return

    async with async_session() as session:
        # Find the course by ID to get its name for the confirmation
        course = await session.get(Course, course_id)
        if not course:
            await callback.message.edit_text(f"Ошибка: Курс с ID {course_id} не найден (возможно, уже удален).")
            await callback.answer()
            await state.clear()
            return

    # Store course_id and name in state for the confirmation step
    await state.update_data(course_id_to_delete=course_id, course_name_to_delete=course.name)

    # Generate confirmation keyboard
    confirm_yes_callback = f"del_confirm_yes:{course_id}"
    confirm_no_callback = "del_confirm_no"
    confirm_builder = await get_confirmation_keyboard(
        yes_callback=confirm_yes_callback,
        no_callback=confirm_no_callback
    )

    # Show confirmation message
    confirmation_text = f"Вы уверены, что хотите удалить курс '{course.name}' (ID: {course_id})?\nПри удалении курса будут удалены и все связанные с ним группы."
    
    await callback.message.edit_text(
        confirmation_text,
        reply_markup=confirm_builder.as_markup()
    )
    await state.set_state(DeleteCourseStates.confirming_deletion)
    await callback.answer() # Acknowledge button press

# Handler for "Yes" confirmation button
@router.callback_query(DeleteCourseStates.confirming_deletion, F.data.startswith("del_confirm_yes"))
async def delete_course_confirm_yes(callback: CallbackQuery, state: FSMContext):
    """Handles the 'Yes' confirmation and performs the deletion."""
    logger.info(f"Entered delete_course_confirm_yes for user {callback.from_user.id}") # Log entry
    data = await state.get_data()
    course_id = data.get("course_id_to_delete")
    course_name = data.get("course_name_to_delete", "Unknown") # Get name for logging/message

    if course_id is None:
        logger.warning(f"course_id_to_delete not found in state for user {callback.from_user.id}")
        await callback.message.edit_text("Ошибка: ID курса не найден в состоянии. Попробуйте снова.")
        await callback.answer()
        await state.clear()
        return

    logger.info(f"Attempting to delete course ID: {course_id}, Name: '{course_name}'")
    async with async_session() as session:
        # Find the course again just to be safe before delete
        course_to_delete = await session.get(Course, course_id)
        if course_to_delete is None:
            logger.warning(f"Course ID {course_id} not found during confirmation for user {callback.from_user.id}")
            await callback.message.edit_text(f"Ошибка: Курс '{course_name}' (ID: {course_id}) не найден (возможно, уже удален).")
            await callback.answer()
            await state.clear()
            return

        # Delete associated groups first, then the course
        try:
            logger.debug(f"Starting deletion transaction for course ID {course_id}")
            # Find and delete groups belonging to this course
            groups_stmt = select(Group).where(Group.course_id == course_id)
            
            # Find and delete CuratorCourse links for this course
            curator_links_stmt = select(CuratorCourse).where(CuratorCourse.course_id == course_id)
            curator_links_results = await session.execute(curator_links_stmt)
            curator_links_to_delete = curator_links_results.scalars().all()

            deleted_curator_link_count = 0
            if curator_links_to_delete:
                logger.info(f"Found {len(curator_links_to_delete)} curator links for course '{course_name}' (ID: {course_id}) to delete.")
                for link in curator_links_to_delete:
                    await session.delete(link)
                    deleted_curator_link_count += 1

            groups_results = await session.execute(groups_stmt)
            groups_to_delete = groups_results.scalars().all()

            deleted_group_count = 0
            deleted_student_link_count = 0 # Initialize here
            if groups_to_delete:
                logger.info(f"Found {len(groups_to_delete)} groups for course '{course_name}' (ID: {course_id}) to delete.")
                
                # --- Delete associated GroupStudent links for each group ---
                group_ids = [g.id for g in groups_to_delete if g.id is not None]
                if group_ids:
                    # Delete student links first
                    student_links_stmt = delete(GroupStudent).where(GroupStudent.group_id.in_(group_ids))
                    result = await session.execute(student_links_stmt)
                    deleted_student_link_count = result.rowcount # Get count of deleted rows
                    if deleted_student_link_count > 0:
                         logger.info(f"Deleted {deleted_student_link_count} student-group links for groups being deleted.")

                    # Now delete curator-group links (existing logic)
                    curator_group_links_stmt = select(CuratorGroup).where(CuratorGroup.group_id.in_(group_ids))
                    curator_group_links_results = await session.execute(curator_group_links_stmt)
                    curator_group_links_to_delete = curator_group_links_results.scalars().all()
                    if curator_group_links_to_delete:
                         logger.info(f"Found {len(curator_group_links_to_delete)} curator-group links for groups being deleted.")
                         for cg_link in curator_group_links_to_delete:
                              await session.delete(cg_link)
                # --- End deleting CuratorGroup links ---
                # --- End deleting associated links ---
                        
                for group in groups_to_delete:
                    await session.delete(group)
                    deleted_group_count += 1
                # Flush the session to execute group deletions before course deletion
                logger.debug(f"Flushing session to delete {deleted_curator_link_count} curator-course links, {deleted_student_link_count} student-group links, and {deleted_group_count} groups for course ID {course_id}")
                await session.flush()

            # Now delete the course itself
            await session.delete(course_to_delete)
            logger.info(f"Deleted course '{course_name}' (ID: {course_id}).")
            await session.commit() # Commit all changes
            logger.debug(f"Committed transaction for course ID {course_id}")

            confirmation_message = f"✅ Курс '{course_name}' (ID: {course_id}) успешно удален."
            if deleted_group_count > 0:
                 confirmation_message += f"\n🗑️ Удалено {deleted_group_count} связанных групп."
            if deleted_student_link_count > 0: # Add count to message
                 confirmation_message += f"\n🔗 Удалено {deleted_student_link_count} связей студентов с группами."
            if deleted_curator_link_count > 0:
                 confirmation_message += f"\n🔗 Удалено {deleted_curator_link_count} связей кураторов с курсом."
            await callback.message.edit_text(confirmation_message)

        except Exception as e:
            # Catch potential DB errors during delete/commit
            logger.exception(f"DB Error during deletion for course ID {course_id}: {e}")
            await session.rollback()
            await callback.message.edit_text(f"Ошибка: Не удалось удалить курс ID {course_id}. Проверьте логи.")

    await callback.answer()
    logger.info(f"Clearing state after delete confirmation for user {callback.from_user.id}")
    await state.clear()

# Handler for "Cancel" confirmation button
@router.callback_query(DeleteCourseStates.confirming_deletion, F.data.startswith("del_confirm_no"))
async def delete_course_confirm_no(callback: CallbackQuery, state: FSMContext):
    """Handles the 'Cancel' confirmation."""
    await callback.message.edit_text("Удаление отменено.")
    await callback.answer()
    await state.clear() 