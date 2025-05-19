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

# FSM States for list_recipients command
class ListRecipientsStates(StatesGroup):
    selecting_course = State()
    selecting_group = State()

# FSM States for delete_recipient command
class DeleteRecipientStates(StatesGroup):
    selecting_course = State()
    selecting_group = State()
    selecting_student = State()

# FSM States for add_recipient command
class AddRecipientStates(StatesGroup):
    selecting_course = State()
    selecting_group = State()
    entering_username = State()

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

# ----- List Recipients Command -----
@router.message(Command("list_recipients"))
@curator_guard
async def list_recipients_start(msg: Message, state: FSMContext):
    """Starts the flow to list recipients of a group."""
    # Cancel previous operation if any
    current_state = await state.get_state()
    if current_state is not None:
        logger.info(f"User {msg.from_user.id} initiated /list_recipients, cancelling previous state: {current_state}")
        await state.clear()
        await msg.answer("(Предыдущая операция отменена)")
        
    builder = await get_course_selection_keyboard(callback_prefix="lr_select_course")
    if builder is None:
        await msg.answer(NO_COURSES_FOUND)
        return

    await msg.answer("1/2: Выберите курс:", reply_markup=builder.as_markup())
    await state.set_state(ListRecipientsStates.selecting_course)

@router.callback_query(ListRecipientsStates.selecting_course, F.data.startswith("lr_select_course:"))
async def list_recipients_course_selected(callback: CallbackQuery, state: FSMContext):
    """Handles course selection and shows group keyboard."""
    try:
        course_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка выбора курса.", show_alert=True)
        await state.clear()
        return

    # Show group keyboard for the selected course
    group_builder = await get_group_selection_keyboard(course_id, callback_prefix="lr_select_group")
    if group_builder is None:
        await callback.message.edit_text("В этом курсе нет групп. Сначала создайте группу командой /set_group.")
        await callback.answer()
        await state.clear()
        return

    await state.update_data(course_id=course_id)
    await callback.message.edit_text("2/2: Выберите группу:", reply_markup=group_builder.as_markup())
    await state.set_state(ListRecipientsStates.selecting_group)
    await callback.answer()

@router.callback_query(ListRecipientsStates.selecting_group, F.data.startswith("lr_select_group:"))
async def list_recipients_group_selected(callback: CallbackQuery, state: FSMContext):
    """Handles group selection and displays the list of students in the group."""
    try:
        group_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка выбора группы.", show_alert=True)
        await state.clear()
        return
        
    async with async_session() as session:
        # Get group name
        group = await session.get(Group, group_id)
        if not group:
            await callback.answer("Выбранная группа не найдена.", show_alert=True)
            await state.clear()
            return
            
        # Get students in the group
        students_query = (
            select(Student)
            .join(GroupStudent, Student.id == GroupStudent.student_id)
            .where(GroupStudent.group_id == group_id)
            .order_by(Student.tg_username)
        )
        result = await session.execute(students_query)
        students = result.scalars().all()
    
    # Build response message
    if students:
        student_list = "\n".join([f"• @{student.tg_username}" for student in students])
        response = f"📋 Список студентов группы '{group.name}' ({len(students)}):\n\n{student_list}"
    else:
        response = f"Группа '{group.name}' пуста. Добавьте студентов, используя команду /set_recipients."
    
    await callback.message.edit_text(response)
    await callback.answer()
    await state.clear()

# ----- Delete Recipient Command -----
@router.message(Command("delete_recipient"))
@curator_guard
async def delete_recipient_start(msg: Message, state: FSMContext):
    """Starts the flow to delete a student from a group."""
    # Cancel previous operation if any
    current_state = await state.get_state()
    if current_state is not None:
        logger.info(f"User {msg.from_user.id} initiated /delete_recipient, cancelling previous state: {current_state}")
        await state.clear()
        await msg.answer("(Предыдущая операция отменена)")
        
    builder = await get_course_selection_keyboard(callback_prefix="dr_select_course")
    if builder is None:
        await msg.answer(NO_COURSES_FOUND)
        return

    await msg.answer("1/3: Выберите курс:", reply_markup=builder.as_markup())
    await state.set_state(DeleteRecipientStates.selecting_course)

@router.callback_query(DeleteRecipientStates.selecting_course, F.data.startswith("dr_select_course:"))
async def delete_recipient_course_selected(callback: CallbackQuery, state: FSMContext):
    """Handles course selection and shows group keyboard."""
    try:
        course_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка выбора курса.", show_alert=True)
        await state.clear()
        return

    # Show group keyboard for the selected course
    group_builder = await get_group_selection_keyboard(course_id, callback_prefix="dr_select_group")
    if group_builder is None:
        await callback.message.edit_text("В этом курсе нет групп. Сначала создайте группу командой /set_group.")
        await callback.answer()
        await state.clear()
        return

    await state.update_data(course_id=course_id)
    await callback.message.edit_text("2/3: Выберите группу:", reply_markup=group_builder.as_markup())
    await state.set_state(DeleteRecipientStates.selecting_group)
    await callback.answer()

@router.callback_query(DeleteRecipientStates.selecting_group, F.data.startswith("dr_select_group:"))
async def delete_recipient_group_selected(callback: CallbackQuery, state: FSMContext):
    """Handles group selection and shows student keyboard."""
    try:
        group_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка выбора группы.", show_alert=True)
        await state.clear()
        return
        
    async with async_session() as session:
        # Get group name
        group = await session.get(Group, group_id)
        if not group:
            await callback.answer("Выбранная группа не найдена.", show_alert=True)
            await state.clear()
            return
            
        # Get students in the group
        students_query = (
            select(Student)
            .join(GroupStudent, Student.id == GroupStudent.student_id)
            .where(GroupStudent.group_id == group_id)
            .order_by(Student.tg_username)
        )
        result = await session.execute(students_query)
        students = result.scalars().all()
    
    if not students:
        await callback.message.edit_text(f"Группа '{group.name}' пуста. Сначала добавьте студентов, используя команду /set_recipients.")
        await callback.answer()
        await state.clear()
        return
    
    # Build student selection keyboard
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton
    
    builder = InlineKeyboardBuilder()
    for student in students:
        builder.add(InlineKeyboardButton(
            text=f"@{student.tg_username}",
            callback_data=f"dr_select_student:{group_id}:{student.id}"
        ))
    builder.adjust(1)  # One button per row for better readability
    
    await state.update_data(group_id=group_id, group_name=group.name)
    await callback.message.edit_text(
        f"3/3: Выберите студента для удаления из группы '{group.name}':",
        reply_markup=builder.as_markup()
    )
    await state.set_state(DeleteRecipientStates.selecting_student)
    await callback.answer()

@router.callback_query(DeleteRecipientStates.selecting_student, F.data.startswith("dr_select_student:"))
async def delete_recipient_student_selected(callback: CallbackQuery, state: FSMContext):
    """Handles student selection and removes the student from the group."""
    try:
        parts = callback.data.split(":")
        group_id = int(parts[1])
        student_id = int(parts[2])
    except (IndexError, ValueError):
        await callback.answer("Ошибка выбора студента.", show_alert=True)
        await state.clear()
        return
    
    data = await state.get_data()
    group_name = data.get("group_name", "Unknown Group")
    
    student_username = ""
    
    async with async_session() as session:
        try:
            # Get student username for the response message
            student = await session.get(Student, student_id)
            if student:
                student_username = student.tg_username
            
            # Delete the link between student and group
            delete_stmt = delete(GroupStudent).where(
                GroupStudent.group_id == group_id,
                GroupStudent.student_id == student_id
            )
            result = await session.execute(delete_stmt)
            await session.commit()
            
            if result.rowcount > 0:
                logger.info(f"Removed student {student_id} (@{student_username}) from group {group_id}")
                await callback.message.edit_text(
                    f"✅ Студент @{student_username} удален из группы '{group_name}'."
                )
            else:
                logger.warning(f"No GroupStudent record found for student {student_id} in group {group_id}")
                await callback.message.edit_text(
                    f"⚠️ Студент не найден в группе '{group_name}'."
                )
        except Exception as e:
            logger.exception(f"Error deleting student {student_id} from group {group_id}: {e}")
            await session.rollback()
            await callback.message.edit_text(
                f"❌ Ошибка при удалении студента из группы '{group_name}'. Попробуйте позже."
            )
    
    await callback.answer()
    await state.clear()

# ----- Add Recipient Command -----
@router.message(Command("add_recipient"))
@curator_guard
async def add_recipient_start(msg: Message, state: FSMContext):
    """Starts the flow to add a student to a group."""
    # Cancel previous operation if any
    current_state = await state.get_state()
    if current_state is not None:
        logger.info(f"User {msg.from_user.id} initiated /add_recipient, cancelling previous state: {current_state}")
        await state.clear()
        await msg.answer("(Предыдущая операция отменена)")
        
    builder = await get_course_selection_keyboard(callback_prefix="ar_select_course")
    if builder is None:
        await msg.answer(NO_COURSES_FOUND)
        return

    await msg.answer("1/3: Выберите курс:", reply_markup=builder.as_markup())
    await state.set_state(AddRecipientStates.selecting_course)

@router.callback_query(AddRecipientStates.selecting_course, F.data.startswith("ar_select_course:"))
async def add_recipient_course_selected(callback: CallbackQuery, state: FSMContext):
    """Handles course selection and shows group keyboard."""
    try:
        course_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка выбора курса.", show_alert=True)
        await state.clear()
        return

    # Show group keyboard for the selected course
    group_builder = await get_group_selection_keyboard(course_id, callback_prefix="ar_select_group")
    if group_builder is None:
        await callback.message.edit_text("В этом курсе нет групп. Сначала создайте группу командой /set_group.")
        await callback.answer()
        await state.clear()
        return

    await state.update_data(course_id=course_id)
    await callback.message.edit_text("2/3: Выберите группу:", reply_markup=group_builder.as_markup())
    await state.set_state(AddRecipientStates.selecting_group)
    await callback.answer()

@router.callback_query(AddRecipientStates.selecting_group, F.data.startswith("ar_select_group:"))
async def add_recipient_group_selected(callback: CallbackQuery, state: FSMContext):
    """Handles group selection and prompts for username."""
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
        f"3/3: Введите Telegram username студента, которого хотите добавить в группу '{group.name}':\n"
        "(например, @username)"
    )
    await state.set_state(AddRecipientStates.entering_username)
    await callback.answer()

@router.message(AddRecipientStates.entering_username, F.text)
async def add_recipient_username_entered(msg: Message, state: FSMContext):
    """Handles username input and adds the student to the group."""
    data = await state.get_data()
    group_id = data.get("group_id")
    group_name = data.get("group_name", "Unknown Group")
    course_id = data.get("course_id")
    
    if not group_id or not course_id:
        await msg.answer("Ошибка: Потерян контекст группы или курса. Начните сначала с /add_recipient.")
        await state.clear()
        return
    
    # Validate username format
    username_raw = msg.text.strip()
    if not username_raw.startswith('@') or len(username_raw) <= 1:
        await msg.answer("Некорректный формат username. Пожалуйста, введите username в формате @username.")
        return  # Keep state for retry
    
    # Extract username without @ and convert to lowercase
    username = username_raw.lower().lstrip('@')
    
    async with async_session() as session:
        try:
            # Check if student already exists in the database
            student_result = await session.execute(
                select(Student).where(Student.tg_username == username)
            )
            student = student_result.scalars().first()
            
            if student:
                # Check if student is already in this group
                existing_link_result = await session.execute(
                    select(GroupStudent).where(
                        GroupStudent.group_id == group_id,
                        GroupStudent.student_id == student.id
                    )
                )
                existing_link = existing_link_result.scalars().first()
                
                if existing_link:
                    await msg.answer(f"⚠️ Студент @{username} уже состоит в группе '{group_name}'.")
                    await state.clear()
                    return
                
                # Check if student is in another group of this course
                other_group_result = await session.execute(
                    select(GroupStudent, Group)
                    .join(Group, GroupStudent.group_id == Group.id)
                    .where(
                        GroupStudent.student_id == student.id,
                        Group.course_id == course_id,
                        GroupStudent.group_id != group_id
                    )
                )
                other_group = other_group_result.first()
                
                if other_group:
                    other_group_name = other_group[1].name
                    await msg.answer(
                        f"❌ Студент @{username} уже состоит в группе '{other_group_name}' "
                        f"в рамках этого курса. Студент может состоять только в одной группе для данного курса."
                    )
                    await state.clear()
                    return
            else:
                # Create new student record
                student = Student(tg_username=username)
                session.add(student)
                await session.flush()
                await session.refresh(student)
                logger.info(f"Created new student '{username}' with ID {student.id}")
            
            # Add student to group
            new_link = GroupStudent(group_id=group_id, student_id=student.id)
            session.add(new_link)
            await session.commit()
            
            await msg.answer(f"✅ Студент @{username} успешно добавлен в группу '{group_name}'.")
        
        except Exception as e:
            logger.exception(f"Error adding student to group {group_id}: {e}")
            await session.rollback()
            await msg.answer("❌ Произошла ошибка при добавлении студента в группу. Попробуйте позже.")
    
    await state.clear()
