from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.container import Container
from app.database.models import Membership, Pref
from app.handlers.menu import show_menu
from app.keyboards.chat import after_chat_kb, chat_kb, searching_kb
from app.keyboards.menu import gender_pref_kb, home_kb, upgrade_kb
from app.services.matchmaking_service import EndedConversation, MatchResult, SearchStatus
from app.utils.formatting import fmt_duration
from app.utils.time import seconds_until_utc_midnight
from app.utils.ui import reply, safe_send

logger = logging.getLogger(__name__)

router = Router(name="matchmaking")
router.message.filter(F.chat.type == "private")


# ── notifications (also used by background tasks) ────────────────────────
async def announce_match(bot: Bot, c: Container, match: MatchResult) -> None:
    a, b = match.user_a, match.user_b

    async def tell(user: int) -> bool:
        return await safe_send(
            bot, user,
            "✅ <b>Partner found!</b>\n\n"
            "💬 You are connected. Say hi! Everything you send is delivered anonymously.",
            reply_markup=chat_kb(),
        )

    ok_a = await tell(a)
    ok_b = await tell(b)
    if ok_a and ok_b:
        return
    # One side blocked the bot: close the chat so the other one isn't talking to nobody.
    unreachable = a if not ok_a else b
    await c.matchmaking.end_conversation(unreachable, reason="unreachable")
    for user, ok in ((a, ok_a), (b, ok_b)):
        if ok:
            await safe_send(
                bot, user, "😕 Your partner could not be reached. Let's find someone else!",
                reply_markup=after_chat_kb(match.conversation_id),
            )


async def notify_partner_left(bot: Bot, ended: EndedConversation, leaver_id: int, text: str | None = None) -> None:
    partner = ended.partner_of(leaver_id)
    if partner is not None:
        await safe_send(
            bot, partner, text or "😔 Your partner has left the conversation.",
            reply_markup=after_chat_kb(ended.conversation_id),
        )


# ── starting a search ────────────────────────────────────────────────────
async def begin_search(event: Message | CallbackQuery, bot: Bot, c: Container, pref: str = Pref.ANY) -> None:
    uid = event.from_user.id  # type: ignore[union-attr]
    out = await c.matchmaking.start_search(uid, pref)
    st = out.status

    if st == SearchStatus.NOT_REGISTERED:
        await reply(event, "👋 Please send /start first to create your profile.")
    elif st == SearchStatus.BANNED:
        await reply(event, "🚫 Your account has been suspended.")
    elif st == SearchStatus.IN_CHAT:
        await reply(event, "💬 You are already in a conversation. Use ⏭️ Next or ❌ Stop.", chat_kb())
    elif st == SearchStatus.ALREADY_SEARCHING:
        await reply(event, "🔍 You are already searching for a partner…", searching_kb())
    elif st == SearchStatus.GENDER_NOT_ALLOWED:
        await reply(event, (
            "🔍 <b>Gender search is a Premium feature.</b>\n\n"
            "Upgrade to choose who you meet (male, female or other) and get "
            f"{c.settings.premium_daily_limit} searches per day."
        ), upgrade_kb())
    elif st == SearchStatus.LIMIT_REACHED:
        wait = fmt_duration(seconds_until_utc_midnight(c.clock()))
        membership = await c.users.membership_of(uid)
        text = (
            f"⛔ <b>Daily search limit reached</b> ({out.used}/{out.limit}).\n"
            f"Your searches reset at 00:00 UTC (in {wait})."
        )
        if membership != Membership.PREMIUM:
            text += f"\n\n👑 Premium members get {c.settings.premium_daily_limit} searches per day."
            await reply(event, text, upgrade_kb())
        else:
            await reply(event, text, home_kb())
    elif st == SearchStatus.WAITING:
        await reply(event, (
            "🔍 <b>Searching for a partner…</b>\n"
            f"Searches today: {out.used}/{out.limit}\n\nWe'll message you as soon as someone is found."
        ), searching_kb())
    elif st == SearchStatus.MATCHED and out.match:
        await announce_match(bot, c, out.match)
    # MATCHED_ELSEWHERE: the worker that paired us announces it.


async def do_stop(event: Message | CallbackQuery, bot: Bot, c: Container) -> None:
    uid = event.from_user.id  # type: ignore[union-attr]
    ended = await c.matchmaking.end_conversation(uid, reason="stopped")
    if ended:
        await notify_partner_left(bot, ended, uid)
        await reply(event, "✅ Conversation ended.", after_chat_kb(ended.conversation_id))
    elif await c.matchmaking.cancel_search(uid):
        await reply(event, "✖️ Search cancelled.", home_kb())
    else:
        await reply(event, "You are not in a conversation. Use /find to meet someone.", home_kb())


async def do_next(event: Message | CallbackQuery, bot: Bot, c: Container) -> None:
    uid = event.from_user.id  # type: ignore[union-attr]
    active = await c.matchmaking.get_active(uid)
    pref = Pref.ANY
    if active:
        pref = active.get("pref", Pref.ANY)
        ended = await c.matchmaking.end_conversation(uid, reason="next")
        if ended:
            await notify_partner_left(bot, ended, uid)
    # Counts as a new valid search (limit checks happen inside start_search).
    await begin_search(event, bot, c, pref)


# ── commands ─────────────────────────────────────────────────────────────
@router.message(Command("find"))
async def cmd_find(message: Message, state: FSMContext, bot: Bot, container: Container) -> None:
    await state.clear()
    await begin_search(message, bot, container)


@router.message(Command("next"))
async def cmd_next(message: Message, state: FSMContext, bot: Bot, container: Container) -> None:
    await state.clear()
    await do_next(message, bot, container)


@router.message(Command("stop"))
async def cmd_stop(message: Message, state: FSMContext, bot: Bot, container: Container) -> None:
    await state.clear()
    await do_stop(message, bot, container)


# ── buttons ──────────────────────────────────────────────────────────────
@router.callback_query(F.data == "menu:find")
async def cb_find(call: CallbackQuery, state: FSMContext, bot: Bot, container: Container) -> None:
    await call.answer()
    await state.clear()
    await begin_search(call, bot, container)


@router.callback_query(F.data == "menu:gender")
async def cb_gender_menu(call: CallbackQuery, container: Container) -> None:
    await call.answer()
    if await container.users.membership_of(call.from_user.id) != Membership.PREMIUM:
        await reply(call, (
            "🔍 <b>Gender search is a Premium feature.</b>\n\n"
            "Premium lets you choose who you meet and raises your limit to "
            f"{container.settings.premium_daily_limit} searches per day."
        ), upgrade_kb(), edit=True)
        return
    await reply(call, "🔍 <b>Who would you like to talk to?</b>", gender_pref_kb(), edit=True)


@router.callback_query(F.data.startswith("gs:"))
async def cb_gender_pick(call: CallbackQuery, state: FSMContext, bot: Bot, container: Container) -> None:
    await call.answer()
    await state.clear()
    pref = call.data.split(":")[1]
    await begin_search(call, bot, container, pref if pref in Pref.ALL else Pref.ANY)


@router.callback_query(F.data == "chat:next")
async def cb_next(call: CallbackQuery, state: FSMContext, bot: Bot, container: Container) -> None:
    await call.answer()
    await state.clear()
    await do_next(call, bot, container)


@router.callback_query(F.data == "chat:stop")
async def cb_stop(call: CallbackQuery, state: FSMContext, bot: Bot, container: Container) -> None:
    await call.answer()
    await state.clear()
    await do_stop(call, bot, container)


@router.callback_query(F.data == "search:cancel")
async def cb_cancel_search(call: CallbackQuery, bot: Bot, container: Container) -> None:
    await call.answer()
    if await container.matchmaking.cancel_search(call.from_user.id):
        await reply(call, "✖️ Search cancelled.", home_kb(), edit=True)
    else:
        await reply(call, "You are not searching right now.", home_kb(), edit=True)
