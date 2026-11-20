from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Update

from app.container import Container
from app.database.models import AccountStatus

logger = logging.getLogger(__name__)

BANNED_TEXT = "🚫 Your account has been suspended. You can no longer use this bot."


class AccessMiddleware(BaseMiddleware):
    """Private chats only, blocks banned users, keeps `last_active` fresh."""

    def __init__(self, container: Container) -> None:
        self.c = container

    async def __call__(self, handler: Callable[[Update, dict[str, Any]], Awaitable[Any]], event: Update, data: dict[str, Any]) -> Any:
        user = data.get("event_from_user")
        if user is None or user.is_bot:
            return await handler(event, data)

        chat = None
        if event.message:
            chat = event.message.chat
        elif event.callback_query and event.callback_query.message:
            chat = event.callback_query.message.chat
        if chat is not None and chat.type != "private":
            return None  # this bot works in private chats only

        maintenance, maintenance_text = await self.c.runtime.maintenance()
        if maintenance and not await self.c.staff.is_admin(user.id):
            if event.pre_checkout_query:
                await event.pre_checkout_query.answer(ok=False, error_message="The bot is under maintenance. Try again later.")
            elif event.callback_query:
                await event.callback_query.answer(maintenance_text, show_alert=True)
            elif event.message:
                await event.message.answer(maintenance_text)
            return None

        doc = await self.c.users.get(user.id)
        if doc and doc.get("account_status") == AccountStatus.BANNED:
            if event.pre_checkout_query:
                await event.pre_checkout_query.answer(ok=False, error_message="Your account is suspended.")
            elif event.callback_query:
                await event.callback_query.answer(BANNED_TEXT, show_alert=True)
            elif event.message:
                await event.message.answer(BANNED_TEXT)
            return None

        if doc:
            await self.c.users.touch(user.id)
            if doc.get("bot_blocked"):
                await self.c.users.clear_bot_blocked(user.id)
        return await handler(event, data)
