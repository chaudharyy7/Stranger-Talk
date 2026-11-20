"""Admin-only dashboard. Every handler is guarded by IsAdmin (ADMIN_IDS), so non-admins fall
through to the normal "unknown command" reply and learn nothing about these features."""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject, Filter
from aiogram.types import CallbackQuery, Message
from bson import ObjectId

from app.container import Container
from app.database.models import AccountStatus, REPORT_CATEGORIES, ReportStatus
from app.handlers.matchmaking import notify_partner_left
from app.keyboards.admin import admin_panel_kb, back_kb, report_kb, user_kb
from app.services.moderation_service import parse_object_id
from app.utils import countries
from app.utils.formatting import esc, fmt_dt
from app.utils.ui import reply, safe_send

logger = logging.getLogger(__name__)


class IsAdmin(Filter):
    async def __call__(self, event: Message | CallbackQuery, container: Container) -> bool:
        user = getattr(event, "from_user", None)
        return bool(user and await container.staff.is_admin(user.id))


router = Router(name="admin")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


# ── shared actions ───────────────────────────────────────────────────────
async def ban_user_full(bot: Bot, c: Container, user_id: int, reason: str | None, admin_id: int) -> None:
    await c.moderation.ban(user_id, reason, admin_id)
    ended = await c.matchmaking.remove_from_everything(user_id)
    if ended:
        await notify_partner_left(bot, ended, user_id)
    await safe_send(bot, user_id, "🚫 Your account has been suspended for violating the rules.")


async def stats_text(c: Container) -> str:
    s = await c.stats.snapshot()
    return (
        "📊 <b>System statistics</b>\n\n"
        f"👥 Users: <b>{s['total_users']}</b> (registered: {s['registered_users']})\n"
        f"🟢 Active last 24h: <b>{s['active_24h']}</b>\n"
        f"🔎 Available now (searching): <b>{s['available_now']}</b>\n"
        f"💬 Active conversations: <b>{s['active_conversations']}</b>\n"
        f"🆓 Free members: <b>{s['free_members']}</b>\n"
        f"👑 Premium members: <b>{s['premium_members']}</b>\n\n"
        f"🔍 Searches today (UTC): <b>{s['searches_today']}</b>\n"
        f"✅ Successful matches today: <b>{s['matches_today']}</b>\n"
        f"❌ Failed attempts today: <b>{s['failed_today']}</b>\n\n"
        f"🚨 Open reports: <b>{s['open_reports']}</b>\n"
        f"🔨 Banned users: <b>{s['banned_users']}</b>"
    )


async def user_card(c: Container, user_id: int) -> tuple[str, bool] | None:
    user = await c.users.get(user_id)
    if not user:
        return None
    profile = await c.users.get_profile(user_id)
    ban = await c.db.banned_users.find_one({"_id": user_id})
    sub = await c.subscriptions.get_subscription(user_id)
    against, filed = await c.moderation.report_counts(user_id)
    active = await c.matchmaking.get_active(user_id)
    queued = await c.matchmaking.in_queue(user_id)
    used = await c.usage.used_today(user_id)
    membership = c.users.effective_membership(user)
    st = user.get("stats", {})

    lines = [f"🔎 <b>User</b> <code>{user_id}</code>"]
    lines.append(f"Status: <b>{user['account_status']}</b>" + (f" — {esc(ban.get('reason') or 'no reason')}" if ban else ""))
    if profile:
        country = profile.get("country")
        country_text = (
            f"{countries.flag(country)} {esc(countries.country_name(country))}"
            if country else "not set"
        )
        lines.append(
            f"Profile: {esc(profile['display_name'])}, {profile['age']}, "
            f"{country_text}, {profile['gender']}"
        )
    else:
        lines.append("Profile: <i>not registered</i>")
    if c.settings.is_owner(user_id):
        lines.append("Membership: <b>premium (owner — always on)</b>")
    else:
        lines.append(f"Membership: <b>{membership}</b> (expiry: {fmt_dt(user.get('premium_expiry'))})")
    if sub:
        lines.append(f"Subscription: {sub.get('plan_type')} / {sub.get('subscription_status')} / start {fmt_dt(sub.get('start_date'))} / end {fmt_dt(sub.get('expiry_date'))}")
    payments = await c.db.payments.find({"user_id": user_id}).sort("created_at", -1).limit(3).to_list()
    for p in payments:
        lines.append(f"  💳 {p['plan_id']} {p['stars']}⭐ {p['status']} <code>{esc(p['telegram_payment_charge_id'])}</code>")
    lines.append(f"Searches today: {used} | total {st.get('searches', 0)} | chats {st.get('chats', 0)} | msgs {st.get('messages', 0)}")
    lines.append(f"Reports against: <b>{against}</b> | filed: {filed}")
    lines.append(f"Now: {'💬 in chat' if active else '🔎 searching' if queued else '💤 idle'}")
    lines.append(f"Registered: {fmt_dt(user.get('registration_date'))} | last active: {fmt_dt(user.get('last_active'))}")
    return "\n".join(lines), user["account_status"] == AccountStatus.BANNED


async def send_report(event: Message | CallbackQuery, c: Container, report: dict | None, edit: bool = False) -> None:
    if report is None:
        await reply(event, "✅ No open reports.", back_kb(), edit=edit)
        return
    against, _ = await c.moderation.report_counts(report["reported_id"])
    text = (
        "🚨 <b>Report</b>\n\n"
        f"Category: <b>{esc(REPORT_CATEGORIES.get(report['category'], report['category']))}</b>\n"
        f"Reporter: <code>{report['reporter_id']}</code>\n"
        f"Reported: <code>{report['reported_id']}</code> (total reports against: {against})\n"
        f"Conversation: <code>{report['conversation_id']}</code>\n"
        f"Time: {fmt_dt(report['created_at'])}\n"
        f"Context: {esc(report['context']) if report.get('context') else '<i>none</i>'}\n"
        f"Evidence messages: {len(report.get('evidence_message_ids', []))}"
    )
    await reply(event, text, report_kb(report["_id"]), edit=edit)


def _int_arg(command: CommandObject, index: int = 0) -> int | None:
    parts = (command.args or "").split()
    try:
        return int(parts[index])
    except (IndexError, ValueError):
        return None


# ── commands ─────────────────────────────────────────────────────────────
@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    await message.answer((
        "🛠 <b>Admin panel</b>\n\n"
        "/stats – statistics\n/reports – review reports\n/user &lt;id&gt; – user card\n"
        "/ban &lt;id&gt; [reason] · /unban &lt;id&gt;\n/grant &lt;id&gt; &lt;days&gt; · /revoke &lt;id&gt;\n"
        "/refund &lt;id&gt; &lt;charge_id&gt;"
    ), reply_markup=admin_panel_kb())


@router.callback_query(F.data == "adm:home")
async def cb_home(call: CallbackQuery) -> None:
    await call.answer()
    await reply(call, "🛠 <b>Admin panel</b>", admin_panel_kb(), edit=True)


@router.message(Command("stats"))
async def cmd_stats(message: Message, container: Container) -> None:
    await message.answer(await stats_text(container), reply_markup=back_kb())


@router.callback_query(F.data == "adm:stats")
async def cb_stats(call: CallbackQuery, container: Container) -> None:
    await call.answer()
    await reply(call, await stats_text(container), back_kb(), edit=True)


@router.message(Command("user"))
async def cmd_user(message: Message, command: CommandObject, container: Container) -> None:
    uid = _int_arg(command)
    if uid is None:
        await message.answer("Usage: /user &lt;telegram_id&gt;")
        return
    card = await user_card(container, uid)
    if card is None:
        await message.answer("User not found.")
        return
    await message.answer(card[0], reply_markup=user_kb(uid, card[1]))


@router.message(Command("ban"))
async def cmd_ban(message: Message, command: CommandObject, bot: Bot, container: Container) -> None:
    uid = _int_arg(command)
    if uid is None:
        await message.answer("Usage: /ban &lt;telegram_id&gt; [reason]")
        return
    if await container.staff.is_admin(uid):
        await message.answer("Administrators cannot be banned.")
        return
    reason = " ".join((command.args or "").split()[1:]) or None
    await ban_user_full(bot, container, uid, reason, message.from_user.id)
    await message.answer(f"🔨 User <code>{uid}</code> banned.")


@router.message(Command("unban"))
async def cmd_unban(message: Message, command: CommandObject, bot: Bot, container: Container) -> None:
    uid = _int_arg(command)
    if uid is None:
        await message.answer("Usage: /unban &lt;telegram_id&gt;")
        return
    await container.moderation.unban(uid)
    await safe_send(bot, uid, "♻️ Your account has been restored. Please follow the rules.")
    await message.answer(f"♻️ User <code>{uid}</code> unbanned.")


@router.message(Command("grant"))
async def cmd_grant(message: Message, command: CommandObject, bot: Bot, container: Container) -> None:
    uid, days = _int_arg(command, 0), _int_arg(command, 1)
    if uid is None or days is None or not 1 <= days <= 3650:
        await message.answer("Usage: /grant &lt;telegram_id&gt; &lt;days 1-3650&gt;")
        return
    expiry = await container.subscriptions.grant(uid, days)
    await safe_send(bot, uid, f"🎁 You received Premium until <b>{fmt_dt(expiry)}</b>!")
    await message.answer(f"👑 Premium granted to <code>{uid}</code> until {fmt_dt(expiry)}.")


@router.message(Command("revoke"))
async def cmd_revoke(message: Message, command: CommandObject, container: Container) -> None:
    uid = _int_arg(command)
    if uid is None:
        await message.answer("Usage: /revoke &lt;telegram_id&gt;")
        return
    done = await container.subscriptions.revoke(uid)
    await message.answer("❌ Premium revoked." if done else "User had no active Premium.")


@router.message(Command("refund"))
async def cmd_refund(message: Message, command: CommandObject, bot: Bot, container: Container) -> None:
    parts = (command.args or "").split()
    uid = _int_arg(command)
    if uid is None or len(parts) < 2:
        await message.answer("Usage: /refund &lt;telegram_id&gt; &lt;charge_id&gt;")
        return
    charge_id = parts[1]
    payment = await container.subscriptions.find_payment(charge_id)
    if payment is None or payment["user_id"] != uid:
        await message.answer("No such payment for this user.")
        return
    try:
        await bot.refund_star_payment(user_id=uid, telegram_payment_charge_id=charge_id)
    except TelegramAPIError as exc:
        await message.answer(f"❌ Telegram refused the refund: {esc(exc)}")
        return
    await container.subscriptions.apply_refund(charge_id)
    await message.answer("💸 Refunded; the purchased Premium time was removed.")


@router.message(Command("reports"))
async def cmd_reports(message: Message, container: Container) -> None:
    await send_report(message, container, await container.moderation.next_open_report())


# ── buttons ──────────────────────────────────────────────────────────────
@router.callback_query(F.data == "adm:reports")
async def cb_reports(call: CallbackQuery, container: Container) -> None:
    await call.answer()
    await send_report(call, container, await container.moderation.next_open_report())


@router.callback_query(F.data.startswith("adm:rnext:"))
async def cb_next_report(call: CallbackQuery, container: Container) -> None:
    await call.answer()
    after = parse_object_id(call.data.split(":")[2])
    report = await container.moderation.next_open_report(after) or await container.moderation.next_open_report()
    await send_report(call, container, report)


@router.callback_query(F.data.startswith("adm:rdis:"))
async def cb_dismiss(call: CallbackQuery, container: Container) -> None:
    rid = parse_object_id(call.data.split(":")[2])
    if rid is None:
        await call.answer()
        return
    await container.moderation.resolve_report(rid, ReportStatus.DISMISSED, call.from_user.id)
    await call.answer("Dismissed.")
    await send_report(call, container, await container.moderation.next_open_report())


@router.callback_query(F.data.startswith("adm:rban:"))
async def cb_report_ban(call: CallbackQuery, bot: Bot, container: Container) -> None:
    rid = parse_object_id(call.data.split(":")[2])
    report = await container.moderation.get_report(rid) if rid else None
    if report is None:
        await call.answer("Report not found.", show_alert=True)
        return
    if await container.staff.is_admin(report["reported_id"]):
        await call.answer("Administrators cannot be banned.", show_alert=True)
        return
    await ban_user_full(bot, container, report["reported_id"], f"Report: {report['category']}", call.from_user.id)
    await container.moderation.resolve_report(report["_id"], ReportStatus.ACTIONED, call.from_user.id)
    await call.answer("User banned.")
    await send_report(call, container, await container.moderation.next_open_report())


@router.callback_query(F.data.startswith("adm:evid:"))
async def cb_evidence(call: CallbackQuery, bot: Bot, container: Container) -> None:
    rid = parse_object_id(call.data.split(":")[2])
    report = await container.moderation.get_report(rid) if rid else None
    ids = (report or {}).get("evidence_message_ids") or []
    if not ids:
        await call.answer("No evidence messages stored for this report.", show_alert=True)
        return
    await call.answer()
    try:  # copies the messages straight from the reporter's own chat; nothing is stored by us
        await bot.copy_messages(chat_id=call.from_user.id, from_chat_id=report["reporter_id"], message_ids=ids)
    except TelegramAPIError:
        await call.answer("The reporter deleted these messages, so they can't be shown.", show_alert=True)


@router.callback_query(F.data.startswith("adm:ban:"))
async def cb_ban(call: CallbackQuery, bot: Bot, container: Container) -> None:
    uid = int(call.data.split(":")[2])
    if await container.staff.is_admin(uid):
        await call.answer("Administrators cannot be banned.", show_alert=True)
        return
    await ban_user_full(bot, container, uid, "Banned from admin panel", call.from_user.id)
    await call.answer("Banned.")
    card = await user_card(container, uid)
    if card:
        await reply(call, card[0], user_kb(uid, card[1]), edit=True)


@router.callback_query(F.data.startswith("adm:unban:"))
async def cb_unban(call: CallbackQuery, container: Container) -> None:
    uid = int(call.data.split(":")[2])
    await container.moderation.unban(uid)
    await call.answer("Unbanned.")
    card = await user_card(container, uid)
    if card:
        await reply(call, card[0], user_kb(uid, card[1]), edit=True)


@router.callback_query(F.data.startswith("adm:grant:"))
async def cb_grant(call: CallbackQuery, container: Container) -> None:
    _, _, uid, days = call.data.split(":")
    await container.subscriptions.grant(int(uid), int(days))
    await call.answer(f"Granted {days} days.")
    card = await user_card(container, int(uid))
    if card:
        await reply(call, card[0], user_kb(int(uid), card[1]), edit=True)


@router.callback_query(F.data.startswith("adm:revoke:"))
async def cb_revoke(call: CallbackQuery, container: Container) -> None:
    uid = int(call.data.split(":")[2])
    await container.subscriptions.revoke(uid)
    await call.answer("Premium revoked.")
    card = await user_card(container, uid)
    if card:
        await reply(call, card[0], user_kb(uid, card[1]), edit=True)
