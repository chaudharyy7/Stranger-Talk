from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.container import Container
from app.database.models import Membership
from app.handlers import registration
from app.keyboards.menu import home_kb
from app.keyboards.profile import profile_kb
from app.utils.formatting import fmt_dt
from app.utils.ui import reply
from app.utils.views import membership_label, profile_text

router = Router(name="profile")
router.message.filter(F.chat.type == "private")


async def show_profile(event: Message | CallbackQuery, c: Container, edit: bool = False) -> None:
    profile = await c.users.get_profile(event.from_user.id)  # type: ignore[union-attr]
    if not profile:
        await reply(event, "Please send /start to create your profile first.")
        return
    await reply(event, profile_text(profile), profile_kb(), edit=edit)


@router.message(Command("profile"))
async def cmd_profile(message: Message, state: FSMContext, container: Container) -> None:
    await state.clear()
    await show_profile(message, container)


@router.callback_query(F.data == "menu:profile")
async def cb_profile(call: CallbackQuery, state: FSMContext, container: Container) -> None:
    await call.answer()
    await state.clear()
    await show_profile(call, container, edit=True)


@router.callback_query(F.data.startswith("pf:edit:"))
async def cb_edit(call: CallbackQuery, state: FSMContext, container: Container) -> None:
    field = call.data.split(":")[2]
    if field not in registration.ORDER:
        await call.answer()
        return
    uid = call.from_user.id
    if await container.matchmaking.get_active(uid) or await container.matchmaking.in_queue(uid):
        await call.answer("Finish your chat / search first (/stop), then edit your profile.", show_alert=True)
        return
    if not await container.users.is_registered(uid):
        await call.answer("Please /start first.", show_alert=True)
        return
    await call.answer()
    await state.clear()
    await state.update_data(mode="edit")
    await registration.ask_field(call, state, container, field)


@router.callback_query(F.data == "menu:stats")
async def cb_stats(call: CallbackQuery, container: Container) -> None:
    await call.answer()
    uid = call.from_user.id
    user = await container.users.get(uid)
    if not user:
        return
    membership = container.users.effective_membership(user)
    limit = container.settings.daily_limit(membership)
    used = await container.usage.used_today(uid)
    st = user.get("stats", {})
    lines = [
        "📊 <b>My Statistics</b>\n",
        f"Membership: <b>{membership_label(container, uid, membership)}</b>",
    ]
    if container.settings.is_owner(uid):
        lines.append("Premium: <b>always on (owner)</b>")
    elif membership == Membership.PREMIUM:
        lines.append(f"Premium until: <b>{fmt_dt(user.get('premium_expiry'))}</b>")
    lines += [
        f"Searches today: <b>{used}/{limit}</b> ({max(0, limit - used)} left, resets 00:00 UTC)",
        f"Total searches: <b>{st.get('searches', 0)}</b>",
        f"Conversations: <b>{st.get('chats', 0)}</b>",
        f"Messages sent: <b>{st.get('messages', 0)}</b>",
        f"Member since: <b>{fmt_dt(user.get('registration_date'))}</b>",
    ]
    await reply(call, "\n".join(lines), home_kb(), edit=True)
