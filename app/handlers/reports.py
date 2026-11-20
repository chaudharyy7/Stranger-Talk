from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton as B, InlineKeyboardMarkup, Message
from bson import ObjectId

from app.container import Container
from app.database.models import REPORT_CATEGORIES
from app.handlers.matchmaking import notify_partner_left
from app.keyboards.menu import home_kb
from app.keyboards.reports import block_offer_kb, categories_kb, skip_kb
from app.services.moderation_service import parse_object_id
from app.states.report import ReportStates
from app.utils import countries
from app.utils.formatting import esc
from app.utils.ui import reply, safe_send

logger = logging.getLogger(__name__)

router = Router(name="reports")
router.message.filter(F.chat.type == "private")


async def _target(c: Container, uid: int, cid: ObjectId | None = None) -> tuple[ObjectId, int] | None:
    """Find (conversation_id, partner_id) the user may report/block: the current chat, or the one
    that just ended. If `cid` is given it must be a conversation the user took part in."""
    if cid is None:
        active = await c.matchmaking.get_active(uid)
        if active:
            return active["conversation_id"], active["partner_id"]
        conv = await c.matchmaking.last_ended(uid)
    else:
        conv = await c.matchmaking.get_conversation(cid)
    if not conv or uid not in conv["participants"]:
        return None
    partner = next(p for p in conv["participants"] if p != uid)
    return conv["_id"], partner


# ── reporting ────────────────────────────────────────────────────────────
async def start_report(event: Message | CallbackQuery, state: FSMContext, c: Container, cid: ObjectId | None) -> None:
    uid = event.from_user.id  # type: ignore[union-attr]
    target = await _target(c, uid, cid)
    if target is None:
        await reply(event, "There is no conversation to report. You can report a partner while chatting, or shortly after the chat ended.", home_kb())
        return
    if cid is not None:  # report from an "after chat" button: enforce the report window
        recent = await c.matchmaking.last_ended(uid)
        active = await c.matchmaking.get_active(uid)
        in_window = (recent and recent["_id"] == target[0]) or (active and active["conversation_id"] == target[0])
        if not in_window:
            await reply(event, "⌛ The time window to report this conversation has passed.", home_kb())
            return
    await state.clear()
    await state.update_data(report_cid=str(target[0]), report_partner=target[1])
    await reply(event, "🚨 <b>Report your partner</b>\nWhy are you reporting them? Your identity is never revealed to them.", categories_kb())


@router.message(Command("report"))
async def cmd_report(message: Message, state: FSMContext, container: Container) -> None:
    await start_report(message, state, container, None)


@router.callback_query(F.data == "chat:report")
async def cb_chat_report(call: CallbackQuery, state: FSMContext, container: Container) -> None:
    await call.answer()
    await start_report(call, state, container, None)


@router.callback_query(F.data.startswith("rep:"))
async def cb_report_by_id(call: CallbackQuery, state: FSMContext, container: Container) -> None:
    await call.answer()
    cid = parse_object_id(call.data.split(":", 1)[1])
    if cid is None:
        return
    await start_report(call, state, container, cid)


@router.callback_query(F.data.startswith("repcat:"))
async def cb_category(call: CallbackQuery, state: FSMContext) -> None:
    key = call.data.split(":")[1]
    data = await state.get_data()
    if key not in REPORT_CATEGORIES or "report_cid" not in data:
        await call.answer("This report session expired. Please start again with /report.", show_alert=True)
        return
    await call.answer()
    await state.update_data(report_category=key)
    await state.set_state(ReportStates.context)
    await reply(call, "📝 Add a short description if you like (what happened?), or press Skip.", skip_kb())


@router.callback_query(F.data == "repcancel")
async def cb_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.clear()
    await reply(call, "✖️ Report cancelled.", home_kb())


async def submit_report(event: Message | CallbackQuery, state: FSMContext, bot: Bot, c: Container, context: str | None) -> None:
    uid = event.from_user.id  # type: ignore[union-attr]
    data = await state.get_data()
    await state.clear()
    cid = parse_object_id(data.get("report_cid", ""))
    partner, category = data.get("report_partner"), data.get("report_category", "other")
    if cid is None or partner is None:
        await reply(event, "This report session expired. Please start again with /report.", home_kb())
        return
    rid = await c.moderation.create_report(uid, partner, cid, category, context)
    if rid is None:
        await reply(event, "ℹ️ You have already reported this conversation. Thank you.", block_offer_kb(cid))
        return
    await reply(event, (
        "✅ <b>Report submitted.</b> Our moderators will review it.\n"
        "The reported user will never learn who reported them."
    ), block_offer_kb(cid))
    for admin_id in await c.staff.all_ids():
        await safe_send(bot, admin_id, f"🚨 New report ({esc(category)}). Open /reports to review.")


@router.message(StateFilter(ReportStates.context), Command("skip"))
async def on_skip_cmd(message: Message, state: FSMContext, bot: Bot, container: Container) -> None:
    await submit_report(message, state, bot, container, None)


@router.message(StateFilter(ReportStates.context), F.text, ~F.text.startswith("/"))
async def on_context(message: Message, state: FSMContext, bot: Bot, container: Container) -> None:
    await submit_report(message, state, bot, container, message.text)


@router.callback_query(F.data == "repskip", StateFilter(ReportStates.context))
async def on_skip(call: CallbackQuery, state: FSMContext, bot: Bot, container: Container) -> None:
    await call.answer()
    await submit_report(call, state, bot, container, None)


# ── blocking ─────────────────────────────────────────────────────────────
async def do_block(event: Message | CallbackQuery, bot: Bot, c: Container, cid: ObjectId | None) -> None:
    uid = event.from_user.id  # type: ignore[union-attr]
    target = await _target(c, uid, cid)
    if target is None:
        await reply(event, "There is nobody to block right now. Use /block while chatting or right after a chat.", home_kb())
        return
    conversation_id, partner = target
    await c.moderation.block(uid, partner, conversation_id)
    active = await c.matchmaking.get_active(uid)
    if active and active["conversation_id"] == conversation_id:
        ended = await c.matchmaking.end_conversation(uid, reason="blocked")
        if ended:  # the partner is NOT told they were blocked
            await notify_partner_left(bot, ended, uid)
    await reply(event, "🚫 <b>User blocked.</b> You will never be matched with them again.\nManage blocks with /unblock.", InlineKeyboardMarkup(inline_keyboard=[
        [B(text="🎲 Find new partner", callback_data="menu:find")],
        [B(text="🏠 Main menu", callback_data="menu:home")],
    ]))


@router.message(Command("block"))
async def cmd_block(message: Message, state: FSMContext, bot: Bot, container: Container) -> None:
    await state.clear()
    await do_block(message, bot, container, None)


@router.callback_query(F.data == "chat:block")
async def cb_chat_block(call: CallbackQuery, bot: Bot, container: Container) -> None:
    await call.answer()
    await do_block(call, bot, container, None)


@router.callback_query(F.data.startswith("blk:"))
async def cb_block_by_id(call: CallbackQuery, bot: Bot, container: Container) -> None:
    await call.answer()
    cid = parse_object_id(call.data.split(":", 1)[1])
    if cid is None:
        return
    await do_block(call, bot, container, cid)


async def _blocked_list_view(c: Container, uid: int) -> tuple[str, InlineKeyboardMarkup]:
    blocks = await c.moderation.list_blocked(uid)
    if not blocks:
        return "✅ You haven't blocked anyone.", home_kb()
    profiles = await c.users.get_profiles([b["blocked_id"] for b in blocks])
    rows = []
    for i, b in enumerate(blocks, 1):
        p: dict[str, Any] | None = profiles.get(b["blocked_id"])
        label = (
            f"{p['display_name']} · {countries.flag(p['country'])}"
            if p and p.get("country") else p["display_name"] if p else f"User #{i}"
        )
        rows.append([B(text=f"♻️ Unblock {label}"[:60], callback_data=f"unb:{b['_id']}")])
    rows.append([B(text="🏠 Main menu", callback_data="menu:home")])
    return "🚫 <b>Blocked users</b>\nTap to unblock:", InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("unblock"))
async def cmd_unblock(message: Message, container: Container) -> None:
    text, kb = await _blocked_list_view(container, message.from_user.id)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("unb:"))
async def cb_unblock(call: CallbackQuery, container: Container) -> None:
    block_id = parse_object_id(call.data.split(":", 1)[1])
    if block_id is None:
        await call.answer()
        return
    done = await container.moderation.unblock(block_id, call.from_user.id)
    await call.answer("Unblocked." if done else "Already unblocked.")
    text, kb = await _blocked_list_view(container, call.from_user.id)
    await reply(call, text, kb, edit=True)
