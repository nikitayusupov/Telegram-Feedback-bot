"""
notifications.py

Utility functions for sending notifications to curators and other users.
"""

import logging
from typing import List, Optional
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from sqlmodel import select

from db import async_session
from models import Curator, CuratorCourse, Course

logger = logging.getLogger(__name__)

async def notify_curators_about_feedback(
    bot: Bot,
    course_id: int,
    student_username: str,
    topic: str,
    feedback_text: str,
    course_name: Optional[str] = None
) -> int:
    """
    Send notification to all curators of a specific course about new feedback.
    
    Args:
        bot: Bot instance for sending messages
        course_id: ID of the course
        student_username: Username of the student (or "Аноним" if anonymous)
        topic: Topic of the feedback
        feedback_text: The feedback text
        course_name: Name of the course (optional, will be fetched if not provided)
    
    Returns:
        Number of curators successfully notified
    """
    try:
        async with async_session() as session:
            # Get course name if not provided
            if course_name is None:
                course = await session.get(Course, course_id)
                course_name = course.name if course else f"[Курс ID: {course_id}]"
            
            # Find all curators for this course who have tg_user_id
            curators_stmt = (
                select(Curator)
                .join(CuratorCourse, Curator.id == CuratorCourse.curator_id)
                .where(
                    CuratorCourse.course_id == course_id,
                    Curator.tg_user_id.is_not(None)
                )
            )
            curators_result = await session.execute(curators_stmt)
            curators = curators_result.scalars().all()
            
            if not curators:
                logger.info(f"No curators with tg_user_id found for course '{course_name}' (ID: {course_id})")
                return 0
            
            # Prepare notification message
            is_anonymous = student_username == "Аноним"
            anonymity_indicator = "🔒 " if is_anonymous else "👤 "
            
            notification_text = (
                f"📬 <b>Новый отзыв по курсу '{course_name}'</b>\n\n"
                f"{anonymity_indicator}<b>От:</b> {student_username}\n"
                f"📝 <b>Тема:</b> {topic}\n\n"
                f"💬 <b>Отзыв:</b>\n{feedback_text}"
            )
            
            # Truncate if too long (Telegram message limit is ~4096 characters)
            if len(notification_text) > 4000:
                truncated_feedback = feedback_text[:3800] + "..."
                notification_text = (
                    f"📬 <b>Новый отзыв по курсу '{course_name}'</b>\n\n"
                    f"{anonymity_indicator}<b>От:</b> {student_username}\n"
                    f"📝 <b>Тема:</b> {topic}\n\n"
                    f"💬 <b>Отзыв:</b>\n{truncated_feedback}\n\n"
                    f"<i>Сообщение сокращено из-за размера.</i>"
                )
            
            # Send notifications to all curators
            successful_notifications = 0
            for curator in curators:
                try:
                    await bot.send_message(
                        chat_id=curator.tg_user_id,
                        text=notification_text
                    )
                    successful_notifications += 1
                    logger.info(f"Sent feedback notification to curator '{curator.tg_username}' (ID: {curator.tg_user_id})")
                    
                except TelegramForbiddenError:
                    logger.warning(f"Curator '{curator.tg_username}' (ID: {curator.tg_user_id}) has blocked the bot")
                except TelegramBadRequest as e:
                    logger.warning(f"Failed to send notification to curator '{curator.tg_username}' (ID: {curator.tg_user_id}): {e}")
                except Exception as e:
                    logger.error(f"Unexpected error sending notification to curator '{curator.tg_username}' (ID: {curator.tg_user_id}): {e}")
            
            logger.info(f"Feedback notifications sent: {successful_notifications}/{len(curators)} curators for course '{course_name}'")
            return successful_notifications
            
    except Exception as e:
        logger.error(f"Error in notify_curators_about_feedback for course {course_id}: {e}")
        return 0 