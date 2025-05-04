import logging
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import delete
from sqlmodel import select # Use SQLModel select for querying models

from db import async_session
from models import Group, Student, GroupStudent
from utils.keyboards import get_course_selection_keyboard, get_group_selection_keyboard
# Assuming curator_guard is correctly defined and imported
from curator_flow.group_handlers import curator_guard
# Import the constant
from utils.constants import NO_COURSES_FOUND

logger = logging.getLogger(__name__)
router = Router()

# ----- FSM States -----
class SetRecipientsStates(StatesGroup):
    selecting_course = State()
    selecting_group = State()
    entering_usernames = State()

# ----- Command Handler -----
@router.message(Command("set_recipients"))
@curator_guard
async def set_recipients_start(msg: Message, state: FSMContext):
    """Starts the flow to set recipients for a group."""
    # Cancel previous operation if any
    current_state = await state.get_state()
    if current_state is not None:
        logger.info(f"User {msg.from_user.id} initiated /set_recipients, cancelling previous state: {current_state}")
        await state.clear()
        await msg.answer("(Предыдущая операция отменена)")
        
    if msg.text.strip() != "/set_recipients":
        await msg.answer("Пожалуйста, используйте команду /set_recipients без аргументов.")
        return

    builder = await get_course_selection_keyboard(callback_prefix="sr_select_course")
    if builder is None:
        await msg.answer(NO_COURSES_FOUND) # Use constant
        return

    await msg.answer("1/3: Выберите курс:", reply_markup=builder.as_markup())
    await state.set_state(SetRecipientsStates.selecting_course)

# ----- Callback Handler for Course Selection -----
@router.callback_query(SetRecipientsStates.selecting_course, F.data.startswith("sr_select_course:"))
async def set_recipients_course_selected(callback: CallbackQuery, state: FSMContext):
    """Handles course selection and shows group keyboard."""
    try:
        course_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка выбора курса.", show_alert=True)
        await state.clear()
        return

    # Show group keyboard for the selected course
    group_builder = await get_group_selection_keyboard(course_id, callback_prefix="sr_select_group")
    if group_builder is None:
        await callback.message.edit_text("В этом курсе нет групп. Сначала создайте группу командой /set_group.")
        await callback.answer()
        await state.clear()
        return

    await state.update_data(course_id=course_id)
    await callback.message.edit_text("2/3: Выберите группу:", reply_markup=group_builder.as_markup())
    await state.set_state(SetRecipientsStates.selecting_group)
    await callback.answer()

# ----- Callback Handler for Group Selection -----
@router.callback_query(SetRecipientsStates.selecting_group, F.data.startswith("sr_select_group:"))
async def set_recipients_group_selected(callback: CallbackQuery, state: FSMContext):
    """Handles group selection and prompts for usernames."""
    try:
        group_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка выбора группы.", show_alert=True)
        await state.clear()
        return
        
    # Verify group exists (optional, but good practice)
    async with async_session() as session:
        group = await session.get(Group, group_id)
        if not group:
            await callback.answer("Выбранная группа не найдена.", show_alert=True)
            await state.clear()
            return
            
    await state.update_data(group_id=group_id, group_name=group.name)
    await callback.message.edit_text(
        f"3/3: Выбрана группа '{group.name}'. "
        "Теперь отправьте список Telegram username студентов через запятую (например, @student1, @another_student):"
    )
    await state.set_state(SetRecipientsStates.entering_usernames)
    await callback.answer()

# ----- Message Handler for Username Input -----
@router.message(SetRecipientsStates.entering_usernames, F.text)
async def set_recipients_usernames_entered(msg: Message, state: FSMContext):
    """Handles username list, updates group membership, and reports changes."""
    data = await state.get_data()
    group_id = data.get("group_id")
    group_name = data.get("group_name", "Unknown Group")

    if not group_id:
        await msg.answer("Ошибка: Потерян контекст группы. Начните сначала с /set_recipients.")
        await state.clear()
        return

    # Parse and validate usernames
    raw_usernames = [u.strip() for u in msg.text.split(',') if u.strip()]
    valid_usernames_input = set()
    invalid_inputs = []
    for u in raw_usernames:
        if u.startswith('@') and len(u) > 1:
            valid_usernames_input.add(u.lower().lstrip('@'))
        else:
            invalid_inputs.append(u)

    if invalid_inputs:
        await msg.answer(
            f"Следующие записи не являются корректными username (должны начинаться с @):\n"
            f"{', '.join(invalid_inputs)}\nПожалуйста, исправьте и отправьте список заново."
        )
        return # Keep state

    if not valid_usernames_input:
        await msg.answer("Вы не отправили ни одного корректного username. Состав группы будет очищен. Вы уверены?")
        # TODO: Add confirmation step for clearing the group?
        # For now, proceed with clearing.
        pass

    added_students = []
    # Renamed list for clarity - students ignored because they are in another group *within the same course*
    ignored_students_same_course = [] 
    removed_students = []
    created_students = []
    kept_students = [] # Students who were already there and remain

    async with async_session() as session:
        try:
            target_course_id = data.get("course_id") # Get the target course ID
            if not target_course_id:
                logger.error(f"Target course ID missing from state for group {group_id}")
                await msg.answer("Ошибка: Потерян контекст курса. Начните сначала.")
                await state.clear()
                return
            
            # --- Pre-check: Identify students already in *other* groups within THIS course --- 
            students_in_conflict = set() # Store usernames (without @) that conflict
            if valid_usernames_input:
                # Get Student IDs for input usernames
                potential_students_stmt = select(Student.id, Student.tg_username).where(
                    Student.tg_username.in_(valid_usernames_input)
                )
                potential_students_result = await session.execute(potential_students_stmt)
                # Map username -> student_id for easier lookup later
                username_to_id_map = {uname: s_id for s_id, uname in potential_students_result.all()}
                potential_student_ids = list(username_to_id_map.values())
                
                if potential_student_ids:
                    # Find links for these students to groups *in the same course* but *not the target group*
                    conflict_check_stmt = (
                        select(GroupStudent.student_id)
                        .join(Group, GroupStudent.group_id == Group.id) # Join GroupStudent with Group
                        .where(
                            GroupStudent.student_id.in_(potential_student_ids),
                            Group.course_id == target_course_id, # Filter by target course
                            GroupStudent.group_id != group_id    # Exclude the target group itself
                        )
                        .distinct() # Only need unique student IDs
                    )
                    conflict_result = await session.execute(conflict_check_stmt)
                    conflicting_student_ids = set(conflict_result.scalars().all())
                    
                    # Map conflicting IDs back to usernames for reporting and filtering
                    id_to_username_map = {v: k for k, v in username_to_id_map.items()} # Reverse map
                    for s_id in conflicting_student_ids:
                        if s_id in id_to_username_map:
                            students_in_conflict.add(id_to_username_map[s_id])
                        else: 
                            logger.warning(f"Could not map conflicting student ID {s_id} back to username.")
            
            if students_in_conflict:
                logger.info(f"Students ignored (already in another group in course {target_course_id}): {students_in_conflict}")
                ignored_students_same_course = [f"@{uname}" for uname in students_in_conflict]
            
            # Filter the input list - process only those not in conflict within this course
            usernames_to_process = valid_usernames_input - students_in_conflict
            logger.debug(f"Usernames to process after filtering: {usernames_to_process}")
            # --- End Pre-check --- 

            # 1. Get current student IDs linked to the target group
            current_links_stmt = select(GroupStudent.student_id).where(GroupStudent.group_id == group_id)
            current_links_result = await session.execute(current_links_stmt)
            current_student_ids = set(current_links_result.scalars().all())
            logger.debug(f"Group {group_id}: Current student IDs: {current_student_ids}")

            # 2. Find/Create Student records for the *filtered* usernames_to_process
            new_student_ids = set()
            student_id_map = {}
            if usernames_to_process: # Use filtered set
                # Find existing students
                existing_students_stmt = select(Student).where(Student.tg_username.in_(usernames_to_process))
                existing_students_result = await session.execute(existing_students_stmt)
                existing_students = existing_students_result.scalars().all()
                
                found_usernames = set()
                for student in existing_students:
                    new_student_ids.add(student.id)
                    student_id_map[student.id] = student.tg_username
                    found_usernames.add(student.tg_username)
                
                # Identify and create new students
                usernames_to_create = usernames_to_process - found_usernames # Use filtered set
                for username in usernames_to_create:
                    new_student = Student(tg_username=username)
                    session.add(new_student)
                    await session.flush() # Get ID
                    await session.refresh(new_student)
                    new_student_ids.add(new_student.id)
                    student_id_map[new_student.id] = new_student.tg_username
                    created_students.append(f"@{username}") # Add with @ for report
                    logger.info(f"Created new student '{username}' with ID {new_student.id}")

            logger.debug(f"Group {group_id}: New student IDs from input: {new_student_ids}")

            # 3. Calculate differences
            ids_to_add = new_student_ids - current_student_ids
            ids_to_remove = current_student_ids - new_student_ids
            ids_to_keep = current_student_ids.intersection(new_student_ids)

            logger.debug(f"Group {group_id}: IDs to add: {ids_to_add}")
            logger.debug(f"Group {group_id}: IDs to remove: {ids_to_remove}")
            logger.debug(f"Group {group_id}: IDs to keep: {ids_to_keep}")

            # 4. Fetch usernames for reporting removed/added students (who weren't just created)
            ids_for_report = ids_to_add.union(ids_to_remove).union(ids_to_keep)
            if ids_for_report:
                report_students_stmt = select(Student.id, Student.tg_username).where(Student.id.in_(ids_for_report))
                report_students_result = await session.execute(report_students_stmt)
                report_id_map = {s_id: s_uname for s_id, s_uname in report_students_result.all()}
                student_id_map.update(report_id_map) # Ensure map is complete

            # 5. Perform DB operations
            # Delete links for removed students
            if ids_to_remove:
                delete_stmt = delete(GroupStudent).where(
                    GroupStudent.group_id == group_id,
                    GroupStudent.student_id.in_(ids_to_remove)
                )
                await session.execute(delete_stmt)
                removed_students = [f"@{student_id_map.get(s_id, '?')}" for s_id in ids_to_remove]
                logger.info(f"Removed {len(ids_to_remove)} students from group {group_id}")

            # Add links for new students
            if ids_to_add:
                for s_id in ids_to_add:
                    session.add(GroupStudent(group_id=group_id, student_id=s_id))
                # Exclude newly created students from the 'added' report list
                added_students = [f"@{student_id_map.get(s_id, '?')}" for s_id in ids_to_add if f"@{student_id_map.get(s_id, '?')}" not in created_students]
                logger.info(f"Added {len(ids_to_add)} students to group {group_id}")
                
            kept_students = [f"@{student_id_map.get(s_id, '?')}" for s_id in ids_to_keep]

            await session.commit()

        except Exception as e:
            logger.exception(f"Error setting recipients for group {group_id}: {e}")
            await session.rollback()
            await msg.answer("Произошла ошибка при обновлении состава группы. Попробуйте позже.")
            await state.clear()
            return

    # 6. Send summary message
    summary_lines = [f"Состав группы '{group_name}' обновлен:"]
    if created_students:
        summary_lines.append(f"\n🆕 Созданы и добавлены: {', '.join(created_students)}")
    if added_students:
        summary_lines.append(f"\n➕ Добавлены существующие: {', '.join(added_students)}")
    if ignored_students_same_course:
        summary_lines.append(f"\n🚫 Проигнорированы (уже в др. группе ЭТОГО курса): {', '.join(ignored_students_same_course)}") 
    if kept_students:
         summary_lines.append(f"\n✅ Остались в группе: {', '.join(kept_students)}")
    if removed_students:
        summary_lines.append(f"\n➖ Исключены из группы: {', '.join(removed_students)}")
    if not valid_usernames_input and not current_student_ids:
         summary_lines.append("\nℹ️ Группа теперь пуста.")

    await msg.answer("\n".join(summary_lines))
    await state.clear()
