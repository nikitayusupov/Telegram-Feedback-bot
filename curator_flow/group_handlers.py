from typing import Optional, Union
import logging # Import logging
import inspect # Import inspect module

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlmodel import select

from config import settings
from db import async_session
from models import Course, Group, Curator, CuratorGroup 

# Import the keyboard utility
from utils.keyboards import get_course_selection_keyboard
# Import the constant
from utils.constants import NO_COURSES_FOUND

# Get a logger instance for this module
logger = logging.getLogger(__name__)

# ----- FSM States -------------------------------------------
class SetGroupStates(StatesGroup):
    selecting_course = State()
    entering_group_name = State()

class ListGroupStates(StatesGroup):
    selecting_course = State()

# ----- Helper: curator check -----------------------------------------------
def is_curator(uid: int, uname: Optional[str]) -> bool:
    """Checks if a user with the given username exists in the Curator table."""
    logger.info(
        f"Checking curator access via DB: user_id={uid}, username='{uname}'"
    )

    # Check only by username (case-insensitive, must start with @)
    if uname:
        username_lower = uname.lower()
        if not username_lower.startswith('@'):
            logger.info(f"Curator check failed: username '{uname}' does not start with @")
            return False

        logger.warning("is_curator function is now just a placeholder. Check moved to async curator_guard.")
        pass # Logic moved to the guard
    else:
        logger.info(f"Curator check failed: no username provided.")
        return False # Cannot check without username

    # Placeholder return, actual logic in guard
    logger.info(f"Curator check result (placeholder): False")
    return False

def curator_guard(handler):
    """Decorator to restrict access to curator-only handlers."""
    handler_params = inspect.signature(handler).parameters
    
    # Make the wrapper async to perform DB checks
    async def wrapper(event: Union[Message, CallbackQuery], *args, **kwargs):
        # Determine user info from Message or CallbackQuery
        if isinstance(event, Message):
            user = event.from_user
            msg_for_reply = event
        elif isinstance(event, CallbackQuery):
            user = event.from_user
            msg_for_reply = event.message
        else:
            logger.error(f"curator_guard applied to unsupported event type: {type(event)}")
            return # Cannot proceed

        logger.debug(f"Curator guard activated for {handler.__name__} by user_id={user.id}, username='{user.username}'")
        
        # --- Database Check --- 
        is_curator_flag = False
        if user.username:
            username_lower = user.username.lower()

            # Prepare username for DB lookup (remove leading '@')
            db_username = username_lower.lstrip('@')

            async with async_session() as session:
                result = await session.execute(
                    select(Curator).where(Curator.tg_username == db_username)
                )
                curator_record = result.scalars().first()
                if curator_record:
                    is_curator_flag = True
                    logger.info(f"DB Check: User '{user.username}' (lookup: '{db_username}') found in Curator table.")
                else:
                    logger.info(f"DB Check: User '{user.username}' (lookup: '{db_username}') not found in Curator table.")
        else:
            logger.info(f"DB Check: User {user.id} has no username.")
        # --- End Database Check ---
            
        if not is_curator_flag:
            logger.warning(
                f"Access denied by curator_guard for {handler.__name__}: user_id={user.id}, username='{user.username}'"
            )
            await msg_for_reply.answer("⛔️ У вас нет прав куратора.") # Updated message
            return

        logger.info(
            f"Access granted by curator_guard for {handler.__name__}: user_id={user.id}, username='{user.username}'"
        )
        
        # Filter kwargs to only include what the handler expects
        filtered_kwargs = {k: v for k, v in kwargs.items() if k in handler_params}
        # Add the event as 'msg' if the handler expects 'msg' 
        # and the event is actually a Message instance.
        if 'msg' in handler_params and isinstance(event, Message):
             filtered_kwargs['msg'] = event
        # Note: If the handler expects 'callback: CallbackQuery', 
        # aiogram should place it in kwargs, so the filtering above handles it.
        
        try:
            return await handler(**filtered_kwargs)
        except Exception as e:
            logger.error(f"Error calling handler {handler.__name__} from curator_guard: {e}", exc_info=True)
            return # Prevent further processing

    return wrapper


router = Router()

# ----- Curator commands skeleton (set_group example) ------------------------
@router.message(Command("set_group"))
@curator_guard
async def set_group_start(msg: Message, state: FSMContext):
    """Starts the set_group flow by showing course selection."""
    # Cancel previous operation if any
    current_state = await state.get_state()
    if current_state is not None:
        logger.info(f"User {msg.from_user.id} initiated /set_group, cancelling previous state: {current_state}")
        await state.clear()
        await msg.answer("(Предыдущая операция отменена)")
        
    builder = await get_course_selection_keyboard(callback_prefix="sg_select_course")

    if builder is None:
        await msg.answer(NO_COURSES_FOUND) # Use constant
        await state.clear()
        return

    await msg.answer("Выберите курс для группы:", reply_markup=builder.as_markup())
    await state.set_state(SetGroupStates.selecting_course)

@router.callback_query(SetGroupStates.selecting_course, F.data.startswith("sg_select_course:"))
async def set_group_course_selected(callback: CallbackQuery, state: FSMContext):
    """Handles course selection and asks for the group name."""
    try:
        course_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Error selecting course.", show_alert=True)
        return

    await state.update_data(course_id=course_id)
    await state.set_state(SetGroupStates.entering_group_name)
    
    await callback.answer("Курс выбран!")
    await callback.message.edit_text("Теперь введите название группы:") 
    # Or: await callback.message.answer("Now, enter the name for the group:") 

@router.message(SetGroupStates.entering_group_name, F.text)
async def set_group_name_entered(msg: Message, state: FSMContext):
    """Handles group name input and saves the group."""
    data = await state.get_data()
    course_id = data.get("course_id")
    group_name = msg.text.strip()

    if not course_id:
        await msg.answer("Ошибка: Контекст курса потерян. Пожалуйста, начните снова с /set_group.")
        await state.clear()
        return
    if not group_name:
         await msg.answer("Название группы не может быть пустым. Пожалуйста, введите корректное название.")
         return # Keep state for user to retry

    # Check length
    if len(group_name) > 100:
        await msg.answer("Название группы не может быть длиннее 100 символов. Пожалуйста, введите более короткое название.")
        return # Keep state

    async with async_session() as s:
        # Course should exist because it was selected
        # Fetch course name for confirmation message (optional but nice)
        course_result = await s.execute(select(Course).where(Course.id == course_id))
        course = course_result.scalars().first()
        if not course: # Should ideally not happen
             await msg.answer("Ошибка: Курс не найден. Пожалуйста, начните снова.")
             await state.clear()
             return
        course_name = course.name # Get name for the message

        # Query for existing group WITH THE SAME NAME *IN THIS COURSE*
        group_result = await s.execute(
            select(Group).where(Group.name == group_name, Group.course_id == course_id)
        )
        group = group_result.scalars().first() # Get Group instance or None

        if group:
            # Group with this name already exists in this course
            await msg.answer(
                f"Ошибка: Группа с именем '{group_name}' уже существует в курсе '{course_name}'.\n"
                f"Пожалуйста, введите другое название группы."
            )
            return # Keep state for user to retry
        else:
            # Create the new group as it doesn't exist in this course
            group = Group(name=group_name, course_id=course_id)
            s.add(group)
            await s.commit() # Commit changes for the new group
            await s.refresh(group) # Refresh to get ID if needed later

            # --- Link the creator as the first curator for this new group ---
            creator_username = msg.from_user.username
            if creator_username:
                creator_username_db = creator_username.lower().lstrip('@')
                curator_result = await s.execute(
                    select(Curator).where(Curator.tg_username == creator_username_db)
                )
                creator_curator = curator_result.scalars().first()
                
                if creator_curator:
                    # Link the curator to the new group
                    link = CuratorGroup(curator_id=creator_curator.id, group_id=group.id)
                    s.add(link)
                    await s.commit() # Commit the link
                    logger.info(f"Linked creator {creator_username} (ID: {creator_curator.id}) to new group '{group.name}' (ID: {group.id})")
                else:
                    # This should ideally not happen if curator_guard worked, but handle defensively
                    logger.warning(f"Could not find Curator record for creator '{creator_username}' to link to new group {group.id}")
                    await msg.answer(f"⚠️ Группа '{group_name}' создана, но не удалось автоматически назначить вас куратором. Обратитесь к администратору.")
            else:
                 logger.warning(f"Creator {msg.from_user.id} has no username, cannot auto-link to new group {group.id}")
                 await msg.answer(f"⚠️ Группа '{group_name}' создана, но не удалось автоматически назначить вас куратором (нет username). Обратитесь к администратору.")
            # --- End linking creator ---

            # Send confirmation
            await msg.answer(f"✅ Группа '{group_name}' создана в курсе '{course_name}'.")

    await state.clear() # Clear state after successful operation

@router.message(Command("list_groups"))
@curator_guard
async def list_groups_start(msg: Message, state: FSMContext):
    """Starts the /list_groups flow by showing course selection."""
    # Cancel previous operation if any
    current_state = await state.get_state()
    if current_state is not None:
        logger.info(f"User {msg.from_user.id} initiated /list_groups, cancelling previous state: {current_state}")
        await state.clear()
        await msg.answer("(Предыдущая операция отменена)")
        
    builder = await get_course_selection_keyboard(callback_prefix="lg_select_course")

    if builder is None:
        await msg.answer(NO_COURSES_FOUND) # Use constant
        await state.clear() # No state needed if no courses
        return

    await msg.answer("Выберите курс для отображения групп:", reply_markup=builder.as_markup())
    await state.set_state(ListGroupStates.selecting_course)

@router.callback_query(ListGroupStates.selecting_course, F.data.startswith("lg_select_course:"))
async def list_groups_course_selected(callback: CallbackQuery, state: FSMContext):
    """Handles course selection and lists groups for that course."""
    try:
        course_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Error selecting course.", show_alert=True)
        await state.clear()
        return

    async with async_session() as s:
        # Get course name for the header
        course = await s.get(Course, course_id)
        if not course:
            await callback.answer("Selected course not found.", show_alert=True)
            await state.clear()
            return
            
        # Get groups for the selected course
        stmt = select(Group).where(Group.course_id == course_id).order_by(Group.name)
        results = await s.execute(stmt)
        groups = results.scalars().all()

    # Edit the original message to show the results
    if not groups:
        response_text = f"Нет групп для курса '{course.name}'."
    else:
        response_lines = [f"Группы для <b>{course.name}</b>:"]
        response_lines.extend([f"  - {group.name} (ID: {group.id})" for group in groups])
        response_text = "\n".join(response_lines)

    await callback.message.edit_text(response_text)
    await callback.answer() # Acknowledge the button press
    await state.clear()

# TODO: implement /set_recipients, /set_questions, /send_now handlers
# These handlers will likely need similar structure:
# - Command filter
# - curator_guard
# - Argument parsing (CommandObject)
# - Async session
# - Database operations (select, add, commit, refresh) using SQLModel models
# - User feedback message 