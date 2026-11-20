from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, LabeledPrice, Message

from app.container import Container
from app.keyboards.premium import plans_kb
from app.services.subscription_service import CURRENCY, SubscriptionService
from app.utils.ui import reply
from app.utils.views import premium_status_text

router = Router(name="premium")
router.message.filter(F.chat.type == "private")


async def show_premium(event: Message | CallbackQuery, c: Container, edit: bool = False) -> None:
    s = c.settings
    status = await premium_status_text(c, event.from_user.id)  # type: ignore[union-attr]
    plan_lines = "\n".join(f"• {p.title}: <b>{p.stars} ⭐</b> ({p.days} days)" for p in s.plans.values())
    text = (
        "👑 <b>Premium</b>\n\n"
        f"{status}\n\n"
        f"🆓 <b>Free</b>: {s.free_daily_limit} searches/day, random matching\n"
        f"👑 <b>Premium</b>: {s.premium_daily_limit} searches/day + <b>gender-specific matching</b>\n\n"
        f"<b>Plans</b> (paid with Telegram Stars)\n{plan_lines}\n\n"
        "Premium is activated only after Telegram confirms your payment. "
        "Buying again extends your current Premium. Questions: /paysupport"
    )
    owner = s.is_owner(event.from_user.id)  # type: ignore[union-attr]
    await reply(event, text, plans_kb(s, show_plans=not owner), edit=edit)


@router.message(Command("premium"))
async def cmd_premium(message: Message, container: Container) -> None:
    await show_premium(message, container)


@router.callback_query(F.data == "menu:premium")
async def cb_premium(call: CallbackQuery, container: Container) -> None:
    await call.answer()
    await show_premium(call, container, edit=True)


@router.callback_query(F.data.startswith("buy:"))
async def cb_buy(call: CallbackQuery, bot: Bot, container: Container) -> None:
    if container.settings.is_owner(call.from_user.id):
        await call.answer("You are the owner: Premium is always enabled for you.", show_alert=True)
        return
    plan = container.settings.plans.get(call.data.split(":")[1])
    if plan is None:
        await call.answer("Unknown plan.", show_alert=True)
        return
    if not await container.users.is_registered(call.from_user.id):
        await call.answer("Please /start and complete your profile first.", show_alert=True)
        return
    await call.answer()
    # Only an invoice is created here. Premium is NOT granted until Telegram confirms the payment.
    await bot.send_invoice(
        chat_id=call.from_user.id,
        title=plan.title,
        description=f"{plan.days} days of Premium: {container.settings.premium_daily_limit} daily searches and gender-specific matching.",
        payload=SubscriptionService.make_payload(plan.id, call.from_user.id),
        currency=CURRENCY,
        prices=[LabeledPrice(label=plan.title, amount=plan.stars)],
    )
