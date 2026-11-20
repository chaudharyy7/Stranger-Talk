"""Small helpers shared by handlers."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

logger = logging.getLogger(__name__)


async def reply(
    event: Message | CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    edit: bool = False,
) -> None:
    """Answer a message, or answer/edit the message a callback button belongs to."""
    if isinstance(event, CallbackQuery):
        msg = event.message
        if isinstance(msg, Message):
            if edit:
                try:
                    await msg.edit_text(text, reply_markup=reply_markup)
                    return
                except TelegramBadRequest:
                    pass  # e.g. "message is not modified" or too old: fall back to a new message
            await msg.answer(text, reply_markup=reply_markup)
        else:
            await event.bot.send_message(event.from_user.id, text, reply_markup=reply_markup)
    else:
        await event.answer(text, reply_markup=reply_markup)


async def safe_send(bot: Bot, chat_id: int, text: str, **kwargs: Any) -> bool:
    """Send a message; returns False if the user can't be reached (blocked the bot, etc.)."""
    for attempt in range(2):
        try:
            await bot.send_message(chat_id, text, **kwargs)
            return True
        except TelegramRetryAfter as exc:
            if attempt == 1:
                return False
            await asyncio.sleep(min(float(exc.retry_after), 20.0))
        except TelegramForbiddenError:
            return False
        except TelegramAPIError:
            logger.warning("safe_send failed for chat %s", chat_id, exc_info=True)
            return False
    return False


def user_id_of(event: Message | CallbackQuery) -> int:
    return event.from_user.id  # type: ignore[union-attr]
