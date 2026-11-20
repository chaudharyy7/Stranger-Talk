"""Catch-all handler: relays every message between two connected users. Must be registered LAST."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.container import Container
from app.handlers.menu import show_menu
from app.keyboards.chat import after_chat_kb, private_confirm_kb
from app.keyboards.menu import home_kb
from app.services.message_service import CONTENT_LABELS, DeliveryFailed, PartnerUnreachable
from app.utils.ui import safe_send

logger = logging.getLogger(__name__)

router = Router(name="messaging")
router.message.filter(F.chat.type == "private")

SUPPORTED_HINT = "text, photos, videos, voice messages, audio, documents, stickers, GIFs and video notes"


@dataclass
class _GroupCtx:
    bot: Bot
    c: Container
    sender_id: int


async def deliver(bot: Bot, c: Container, sender_id: int, active: dict[str, Any], message_ids: list[int], ctype: str) -> bool:
    partner, cid = active["partner_id"], active["conversation_id"]
    try:
        if len(message_ids) == 1:
            delivered = [await c.messages.copy_one(bot, sender_id, message_ids[0], partner)]
        else:
            delivered = await c.messages.copy_many(bot, sender_id, message_ids, partner)
    except PartnerUnreachable:
        await c.matchmaking.end_conversation(sender_id, reason="unreachable")
        await safe_send(
            bot, sender_id, "😕 Your partner can no longer be reached, so the conversation was closed.",
            reply_markup=after_chat_kb(cid),
        )
        return False
    except DeliveryFailed as exc:
        logger.info("Delivery failed (%s): %s", ctype, exc)  # never log message content
        await safe_send(bot, sender_id, "⚠️ Telegram could not deliver that message (file too large or restricted?). Try something else.")
        return False

    await asyncio.gather(
        c.messages.record(cid, sender_id, partner, ctype, delivered),
        c.matchmaking.touch_conversation(cid),
        c.users.inc_stat(sender_id, "messages", len(message_ids)),
    )
    return True


async def _flush_group(ctx: _GroupCtx, message_ids: list[int]) -> None:
    active = await ctx.c.matchmaking.get_active(ctx.sender_id)
    if active:
        await deliver(ctx.bot, ctx.c, ctx.sender_id, active, message_ids, "media_group")


@router.message()
async def relay(message: Message, state: FSMContext, bot: Bot, container: Container) -> None:
    if message.from_user is None or message.successful_payment or message.refunded_payment:
        return
    c, uid = container, message.from_user.id
    is_command = bool(message.text and message.text.startswith("/"))
    active = await c.matchmaking.get_active(uid)

    if active is None:
        if await state.get_state():
            await message.answer("👆 Please finish the current step first, or send /cancel.")
        elif is_command:
            await message.answer("❓ Unknown command. Send /help to see what I can do.")
        else:
            await message.answer("💤 You are not connected to anyone. Use /find or the menu to meet someone.", reply_markup=home_kb())
        return

    if is_command:
        await message.answer("❓ Unknown command (it was not sent to your partner). Send /help for the list.")
        return

    ctype = c.messages.content_type(message)
    kind = c.messages.classify(ctype)

    if kind == "unsupported":
        label = CONTENT_LABELS.get(ctype, ctype.replace("_", " "))
        await message.answer(f"⚠️ Sorry, {label} can't be sent in anonymous chats.\nSupported: {SUPPORTED_HINT}.")
        return

    if kind == "confirm":
        what = "your phone number" if ctype == "contact" else "your location"
        if len(c.messages.pending_private) > 5000:
            c.messages.pending_private.clear()
        c.messages.pending_private[(uid, message.message_id)] = (active["conversation_id"], ctype)
        await message.answer(
            f"🔐 <b>Privacy check</b>\nThis would reveal {what} to a stranger. Do you really want to send it?",
            reply_markup=private_confirm_kb(message.message_id),
        )
        return

    if message.media_group_id:
        await c.messages.add_to_group(
            f"{uid}:{message.media_group_id}", message.message_id, _GroupCtx(bot, c, uid), _flush_group
        )
        return

    await deliver(bot, c, uid, active, [message.message_id], ctype)


@router.callback_query(F.data.startswith("priv:"))
async def on_private_confirm(call: CallbackQuery, bot: Bot, container: Container) -> None:
    await call.answer()
    try:
        _, action, mid = call.data.split(":")
        mid_int = int(mid)
    except ValueError:
        return
    c, uid = container, call.from_user.id
    pending = c.messages.pending_private.pop((uid, mid_int), None)
    msg = call.message if isinstance(call.message, Message) else None

    async def say(text: str) -> None:
        if msg:
            try:
                await msg.edit_text(text)
                return
            except Exception:  # noqa: BLE001
                pass
        await safe_send(bot, uid, text)

    if pending is None:
        await say("⌛ This request has expired.")
        return
    if action != "ok":
        await say("✖️ Not sent.")
        return
    cid, ctype = pending
    active = await c.matchmaking.get_active(uid)
    if not active or active["conversation_id"] != cid:
        await say("The conversation has already ended, so nothing was sent.")
        return
    if await deliver(bot, c, uid, active, [mid_int], ctype):
        await say("✅ Sent.")
