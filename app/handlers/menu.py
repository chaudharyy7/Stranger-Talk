from __future__ import annotations

from aiogram.types import CallbackQuery, Message

from app.container import Container
from app.keyboards.menu import main_menu_kb
from app.utils.ui import reply
from app.utils.views import menu_text


async def show_menu(event: Message | CallbackQuery, c: Container, edit: bool = False) -> None:
    uid = event.from_user.id  # type: ignore[union-attr]
    await reply(event, await menu_text(c, uid), main_menu_kb(), edit=edit)
