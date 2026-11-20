"""Background maintenance: pairing safety net, queue timeouts, idle chats, Premium expiry."""
from __future__ import annotations

import asyncio
import logging
import time

from aiogram import Bot

from app.container import Container
from app.handlers.matchmaking import announce_match
from app.keyboards.menu import upgrade_kb
from app.utils.ui import safe_send

logger = logging.getLogger(__name__)


async def sweep_queue(bot: Bot, c: Container) -> None:
    for match in await c.matchmaking.pair_waiting():
        await announce_match(bot, c, match)
    for user_id in await c.matchmaking.expire_queue():
        await safe_send(bot, user_id, "😕 No partner was found this time. Please try again in a moment — /find")


async def sweep_idle_conversations(bot: Bot, c: Container) -> None:
    for ended in await c.matchmaking.expire_idle_conversations():
        for user_id in ended.participants:
            await safe_send(
                bot, user_id,
                "⌛ The conversation ended because nobody wrote for a while. Use /find to meet someone new.",
            )


async def sweep_premium(bot: Bot, c: Container) -> None:
    for user_id in await c.subscriptions.expire_due():
        await safe_send(bot, user_id, "⌛ Your Premium has expired. Renew it any time with /premium.", reply_markup=upgrade_kb())


async def maintenance_loop(bot: Bot, c: Container, stop: asyncio.Event) -> None:
    last_slow = 0.0
    while not stop.is_set():
        try:
            await sweep_queue(bot, c)
            now = time.monotonic()
            if now - last_slow >= 30:
                last_slow = now
                await sweep_idle_conversations(bot, c)
                await sweep_premium(bot, c)
        except Exception:  # noqa: BLE001
            logger.exception("Maintenance cycle failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=3)
        except asyncio.TimeoutError:
            pass
