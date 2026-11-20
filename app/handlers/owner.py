"""Owner-only control commands. Every handler is behind IsOwner (OWNER_IDS). Owners are also admins,
so they additionally have every command from handlers/admin.py."""
from __future__ import annotations

import logging
import platform
import time

import aiogram
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject, Filter
from aiogram.types import CallbackQuery, Message

from app.container import Container
from app.keyboards.owner import broadcast_confirm_kb, broadcast_running_kb
from app.services.broadcast_service import AUDIENCES
from app.services.moderation_service import parse_object_id
from app.utils.formatting import esc, fmt_dt, fmt_duration
from app.utils.ui import safe_send

logger = logging.getLogger(__name__)


class IsOwner(Filter):
    async def __call__(self, event: Message | CallbackQuery, container: Container) -> bool:
        user = getattr(event, "from_user", None)
        return bool(user and container.settings.is_owner(user.id))


router = Router(name="owner")
router.message.filter(IsOwner())
router.callback_query.filter(IsOwner())

OWNER_HELP = (
    "👑 <b>Owner commands</b> (you also have every /admin command)\n\n"
    "📣 <b>Broadcast</b>\n"
    "/broadcast [--all|--free|--premium|--active] &lt;text&gt; — message users (HTML tags allowed)\n"
    "Reply to any message (photo, video, formatted text…) with /broadcast to send that message instead\n"
    "/broadcasts — recent broadcasts and their results\n\n"
    "🛠 <b>Control</b>\n"
    "/maintenance on [message] · /maintenance off · /maintenance — pause the bot for normal users\n"
    "/botinfo — uptime, database and traffic overview\n"
    "/dm &lt;user_id&gt; &lt;text&gt; — send a private message to one user as the bot\n\n"
    "👥 <b>Staff</b>\n"
    "/addadmin &lt;id&gt; · /removeadmin &lt;id&gt; · /admins\n\n"
    "👑 Premium features are always enabled for you; nothing to buy."
)


def _int(text: str | None) -> int | None:
    try:
        return int((text or "").split()[0])
    except (IndexError, ValueError):
        return None


@router.message(Command("owner"))
async def cmd_owner(message: Message) -> None:
    await message.answer(OWNER_HELP)


# ── broadcast ────────────────────────────────────────────────────────────
@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, command: CommandObject, bot: Bot, container: Container) -> None:
    c = container
    raw = (command.args or "").strip()
    audience = "all"
    if raw.startswith("--"):
        flag, _, raw = raw.partition(" ")
        audience = flag[2:].lower()
        raw = raw.strip()
    if audience not in AUDIENCES:
        await message.answer(f"Unknown audience. Use one of: {', '.join('--' + a for a in AUDIENCES)}")
        return

    reply = message.reply_to_message
    if raw and reply:
        await message.answer("Use either text <b>or</b> reply to a message — not both.")
        return
    if not raw and not reply:
        await message.answer(
            "Usage:\n<code>/broadcast [--all|--free|--premium|--active] your text</code>\n"
            "or reply to any message with <code>/broadcast</code> to send that message (keeps media and formatting)."
        )
        return
    if raw and len(raw) > 4096:
        await message.answer("Text is longer than Telegram's 4096-character limit.")
        return

    total = await c.broadcasts.count_audience(audience)
    if total == 0:
        await message.answer("There is nobody in that audience.")
        return

    # The preview is sent exactly the way users will receive it, so formatting errors show up
    # here and not after 5,000 failed sends.
    try:
        if reply:
            await bot.copy_message(chat_id=message.chat.id, from_chat_id=reply.chat.id, message_id=reply.message_id)
        else:
            await bot.send_message(message.chat.id, raw)
    except TelegramAPIError as exc:
        await message.answer(f"❌ Telegram rejected this message, nothing was created:\n<code>{esc(exc)}</code>")
        return

    bid = await c.broadcasts.create(
        message.from_user.id, audience, total,
        text=None if reply else raw,
        source_chat_id=reply.chat.id if reply else None,
        source_message_id=reply.message_id if reply else None,
    )
    eta = fmt_duration(int(total / c.settings.broadcast_rate_per_second))
    await message.answer(
        f"👆 This is exactly what will be sent to <b>{total}</b> users (audience: <b>{audience}</b>).\n"
        f"Estimated time: ~{eta}. Banned users and users who blocked the bot are skipped.",
        reply_markup=broadcast_confirm_kb(bid, total),
    )


@router.callback_query(F.data.startswith("bc:go:"))
async def cb_broadcast_go(call: CallbackQuery, bot: Bot, container: Container) -> None:
    bid = parse_object_id(call.data.split(":")[2])
    if bid is None or not await container.broadcasts.start(bot, bid):
        await call.answer("This broadcast was already started, cancelled or has expired.", show_alert=True)
        return
    await call.answer("Broadcast started.")
    if isinstance(call.message, Message):
        try:
            await call.message.edit_text(
                "📤 <b>Broadcast started.</b> I'll message you when it finishes.",
                reply_markup=broadcast_running_kb(bid),
            )
        except TelegramAPIError:
            pass


@router.callback_query(F.data.startswith("bc:no:"))
async def cb_broadcast_cancel(call: CallbackQuery, container: Container) -> None:
    bid = parse_object_id(call.data.split(":")[2])
    done = bool(bid and await container.broadcasts.discard(bid))
    await call.answer("Cancelled." if done else "Nothing to cancel.")
    if isinstance(call.message, Message):
        try:
            await call.message.edit_text("✖️ Broadcast cancelled. Nothing was sent.")
        except TelegramAPIError:
            pass


@router.callback_query(F.data.startswith("bc:stop:"))
async def cb_broadcast_stop(call: CallbackQuery, container: Container) -> None:
    bid = parse_object_id(call.data.split(":")[2])
    stopped = bool(bid and await container.broadcasts.stop(bid))
    await call.answer("Stopping…" if stopped else "That broadcast is not running.")


@router.message(Command("broadcasts"))
async def cmd_broadcasts(message: Message, container: Container) -> None:
    items = await container.broadcasts.recent(5)
    if not items:
        await message.answer("No broadcasts yet.")
        return
    lines = ["📣 <b>Recent broadcasts</b>\n"]
    for b in items:
        lines.append(
            f"• {fmt_dt(b['created_at'])} — <b>{b['status']}</b>, {b['audience']}: "
            f"✅ {b['sent']} · 🚫 {b['blocked']} · ⚠️ {b['failed']} / {b['total']}"
        )
    await message.answer("\n".join(lines))


# ── maintenance ──────────────────────────────────────────────────────────
@router.message(Command("maintenance"))
async def cmd_maintenance(message: Message, command: CommandObject, container: Container) -> None:
    parts = (command.args or "").strip().split(maxsplit=1)
    if not parts:
        on, text = await container.runtime.maintenance()
        await message.answer(f"Maintenance mode is <b>{'ON' if on else 'OFF'}</b>.\nMessage shown to users:\n{esc(text)}")
    elif parts[0].lower() == "on":
        await container.runtime.set_maintenance(True, parts[1] if len(parts) > 1 else None)
        await message.answer("🛠 Maintenance mode <b>ON</b>. Normal users are blocked; owners and admins are unaffected. Running chats are paused.")
    elif parts[0].lower() == "off":
        await container.runtime.set_maintenance(False)
        await message.answer("✅ Maintenance mode <b>OFF</b>. The bot works for everyone again.")
    else:
        await message.answer("Usage: /maintenance on [message] · /maintenance off")


# ── staff ────────────────────────────────────────────────────────────────
@router.message(Command("addadmin"))
async def cmd_addadmin(message: Message, command: CommandObject, bot: Bot, container: Container) -> None:
    uid = _int(command.args)
    if uid is None:
        await message.answer("Usage: /addadmin &lt;telegram_id&gt;")
        return
    if await container.staff.add_admin(uid, message.from_user.id):
        await safe_send(bot, uid, "🛠 You have been made an administrator of this bot. Send /admin to open the panel.")
        await message.answer(f"✅ <code>{uid}</code> is now an administrator.")
    else:
        await message.answer("That user is already an administrator.")


@router.message(Command("removeadmin"))
async def cmd_removeadmin(message: Message, command: CommandObject, container: Container) -> None:
    uid = _int(command.args)
    if uid is None:
        await message.answer("Usage: /removeadmin &lt;telegram_id&gt;")
        return
    if uid in container.settings.staff_ids:
        await message.answer("Owners and admins from the environment (OWNER_IDS / ADMIN_IDS) can only be changed there.")
    elif await container.staff.remove_admin(uid):
        await message.answer(f"✅ <code>{uid}</code> is no longer an administrator.")
    else:
        await message.answer("That user is not an administrator.")


@router.message(Command("admins"))
async def cmd_admins(message: Message, container: Container) -> None:
    staff = await container.staff.list_staff()

    def fmt(items: list) -> str:
        return ", ".join(f"<code>{i}</code>" for i, _ in items) or "—"

    await message.answer(
        "👥 <b>Staff</b>\n"
        f"Owners: {fmt(staff['owners'])}\n"
        f"Admins (environment): {fmt(staff['env_admins'])}\n"
        f"Admins (added with /addadmin): {fmt(staff['runtime_admins'])}"
    )


# ── direct message & info ────────────────────────────────────────────────
@router.message(Command("dm"))
async def cmd_dm(message: Message, command: CommandObject, bot: Bot) -> None:
    parts = (command.args or "").split(maxsplit=1)
    uid = _int(parts[0]) if parts else None
    if uid is None or len(parts) < 2:
        await message.answer("Usage: /dm &lt;telegram_id&gt; &lt;text&gt;")
        return
    ok = await safe_send(bot, uid, f"📩 <b>Message from the bot team</b>\n\n{parts[1]}")
    await message.answer("✅ Sent." if ok else "❌ Could not deliver (user never started the bot, blocked it, or the text has invalid HTML).")


@router.message(Command("botinfo"))
async def cmd_botinfo(message: Message, container: Container) -> None:
    c = container
    started = time.perf_counter()
    db_ok = await c.db.ping()
    ping_ms = (time.perf_counter() - started) * 1000
    snap = await c.stats.snapshot()
    maintenance, _ = await c.runtime.maintenance()
    uptime = int((c.clock() - c.started_at).total_seconds())
    await message.answer(
        "🤖 <b>Bot info</b>\n\n"
        f"Mode: <b>{c.settings.run_mode}</b> · env: <b>{c.settings.environment}</b>\n"
        f"Uptime: <b>{fmt_duration(uptime)}</b>\n"
        f"Database: <b>{'OK' if db_ok else 'DOWN'}</b> ({ping_ms:.0f} ms)\n"
        f"Maintenance: <b>{'ON' if maintenance else 'off'}</b>\n"
        f"Running broadcasts: <b>{len(c.broadcasts.running_ids())}</b>\n\n"
        f"Users: <b>{snap['total_users']}</b> · active 24h: <b>{snap['active_24h']}</b>\n"
        f"Searching now: <b>{snap['available_now']}</b> · active chats: <b>{snap['active_conversations']}</b>\n"
        f"Searches today: <b>{snap['searches_today']}</b> · matches: <b>{snap['matches_today']}</b>\n"
        f"Open reports: <b>{snap['open_reports']}</b> · banned: <b>{snap['banned_users']}</b>\n\n"
        f"Python {platform.python_version()} · aiogram {aiogram.__version__}"
    )
