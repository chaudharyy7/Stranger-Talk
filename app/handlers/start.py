from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.container import Container
from app.handlers import registration
from app.handlers.menu import show_menu
from app.keyboards.chat import chat_kb
from app.keyboards.menu import home_kb
from app.utils.legal import help_text, privacy_text, terms_text
from app.utils.ui import reply

router = Router(name="start")
router.message.filter(F.chat.type == "private")


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, container: Container) -> None:
    await state.clear()
    uid = message.from_user.id
    await container.users.get_or_create(uid)

    if not await container.users.is_registered(uid):
        await registration.begin(message, state, container)
        return

    # Existing users never register again. If they restarted the bot in the middle of a chat,
    # the conversation is still alive in the database.
    if await container.matchmaking.get_active(uid):
        await message.answer("💬 You are still connected to your partner. Just keep typing!", reply_markup=chat_kb())
        return
    if await container.matchmaking.in_queue(uid):
        await message.answer("🔍 You are still in the search queue. Use /stop to cancel.")
        return
    await show_menu(message, container)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, container: Container) -> None:
    if await state.get_state() is None:
        await message.answer("Nothing to cancel.")
        return
    await state.clear()
    await message.answer("✖️ Cancelled.")
    if await container.users.is_registered(message.from_user.id):
        await show_menu(message, container)


@router.callback_query(F.data == "menu:home")
async def cb_home(call: CallbackQuery, state: FSMContext, container: Container) -> None:
    await call.answer()
    await state.clear()
    await show_menu(call, container, edit=True)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(help_text(), reply_markup=home_kb())


@router.callback_query(F.data == "menu:help")
async def cb_help(call: CallbackQuery) -> None:
    await call.answer()
    await reply(call, help_text(), home_kb(), edit=True)


@router.message(Command("terms"))
async def cmd_terms(message: Message, container: Container) -> None:
    await message.answer(terms_text(container.settings))


@router.message(Command("privacy"))
async def cmd_privacy(message: Message, container: Container) -> None:
    await message.answer(privacy_text(container.settings))
