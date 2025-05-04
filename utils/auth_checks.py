# utils/auth_checks.py
import logging
import inspect
from typing import Optional

from aiogram.types import Message

from config import settings

logger = logging.getLogger(__name__)

# ----- Admin Check ----- 
def is_admin(uid: int, uname: Optional[str]) -> bool:
    """Checks if a user ID or username is in the admin set."""
    admin_set = settings.admin_id_set
    logger.info(
        f"Checking admin access: user_id={uid}, username='{uname}', admin_set={admin_set}"
    )
    if uid in admin_set:
        logger.info(f"Admin access check result (by ID): True")
        return True
    if uname:
        plain_uname_lower = uname.lower()
        at_uname_lower = f"@{plain_uname_lower}"
        # Check both with and without @, case-insensitive
        if plain_uname_lower in admin_set or at_uname_lower in admin_set:
            logger.info(f"Admin access check result (by Username '{uname}'): True")
            return True
    logger.info(f"Admin access check result: False")
    return False

def admin_guard(handler):
    """Decorator to restrict access to admin-only handlers."""
    handler_params = inspect.signature(handler).parameters

    async def wrapper(msg: Message, *args, **kwargs):
        user = msg.from_user
        logger.debug(f"Admin guard activated for {handler.__name__} by user_id={user.id}, username='{user.username}'")
        if not is_admin(user.id, user.username):
            logger.warning(
                f"Access denied by admin_guard for {handler.__name__}: user_id={user.id}, username='{user.username}'"
            )
            await msg.answer("⛔️ Access denied (Admin only)")
            return
        logger.info(
            f"Access granted by admin_guard for {handler.__name__}: user_id={user.id}, username='{user.username}'"
        )

        # Prepare args based *only* on what the handler expects
        final_kwargs = {}
        if 'msg' in handler_params:
            final_kwargs['msg'] = msg
        if 'command' in handler_params:
            command = kwargs.get('command')
            if command is not None:
                final_kwargs['command'] = command
            else:
                logger.error(f"Handler {handler.__name__} expects 'command' but not found in kwargs")
                await msg.answer("Error: Missing command arguments.")
                return
        if 'state' in handler_params:
            state_arg = kwargs.get('state') # Get state from aiogram's kwargs
            if state_arg is not None:
                final_kwargs['state'] = state_arg
            else:
                # This shouldn't happen if aiogram passes state correctly
                logger.error(f"Handler {handler.__name__} expects 'state' but not found in kwargs")
                await msg.answer("Internal error: State context missing.")
                return

        try:
            # Call handler only with arguments defined in its signature
            return await handler(**final_kwargs)
        except Exception as e:
            logger.exception(f"Error calling handler {handler.__name__} from admin_guard: {e}")
            await msg.answer("An internal error occurred.")
            return

    return wrapper 