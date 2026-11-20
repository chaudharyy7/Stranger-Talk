from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import Message, PreCheckoutQuery

from app.container import Container
from app.services.subscription_service import PaymentOutcomeStatus
from app.utils.formatting import esc, fmt_dt
from app.utils.ui import safe_send

logger = logging.getLogger(__name__)

router = Router(name="payments")


@router.pre_checkout_query()
async def on_pre_checkout(query: PreCheckoutQuery, container: Container) -> None:
    ok, error = container.subscriptions.validate_precheckout(
        query.from_user.id, query.invoice_payload, query.currency, query.total_amount
    )
    if ok:
        await query.answer(ok=True)
    else:
        await query.answer(ok=False, error_message=error or "Payment cannot be processed.")


@router.message(F.successful_payment)
async def on_successful_payment(message: Message, bot: Bot, container: Container) -> None:
    sp = message.successful_payment
    uid = message.from_user.id
    outcome = await container.subscriptions.process_successful_payment(
        uid, sp.invoice_payload, sp.currency, sp.total_amount,
        sp.telegram_payment_charge_id, sp.provider_payment_charge_id,
    )
    if outcome.status == PaymentOutcomeStatus.ACTIVATED:
        await message.answer(
            f"✅ <b>Payment received. Thank you!</b>\n👑 Premium is active until <b>{fmt_dt(outcome.expiry)}</b>."
        )
    elif outcome.status == PaymentOutcomeStatus.DUPLICATE:
        await message.answer("ℹ️ This payment was already processed.")
    else:
        await message.answer("⚠️ We couldn't verify this payment automatically. Please contact /paysupport — your payment is not lost.")
        for admin_id in await container.staff.all_ids():
            await safe_send(
                bot, admin_id,
                f"⚠️ Unverifiable payment from user <code>{uid}</code>, charge <code>{esc(sp.telegram_payment_charge_id)}</code>: {esc(outcome.reason)}",
            )


@router.message(F.refunded_payment)
async def on_refunded_payment(message: Message, container: Container) -> None:
    rp = message.refunded_payment
    user_id = await container.subscriptions.apply_refund(rp.telegram_payment_charge_id)
    if user_id is not None:
        logger.info("Refund applied for charge %s", rp.telegram_payment_charge_id)


@router.message(Command("paysupport"), F.chat.type == "private")
async def cmd_paysupport(message: Message, container: Container) -> None:
    await message.answer(
        "💳 <b>Payment support</b>\n\n"
        "Premium is paid with Telegram Stars and is activated automatically after Telegram confirms the payment.\n"
        "• Paid but no Premium? Wait a minute, then send /premium to check your status.\n"
        "• Want a refund or have a dispute? Contact the administrator and include your payment ID "
        "(shown in Telegram under Settings → My Stars → transaction details).\n"
        "Refunded payments remove the purchased Premium time."
    )
