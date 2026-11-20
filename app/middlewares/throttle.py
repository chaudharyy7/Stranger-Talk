from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Update


class ThrottleMiddleware(BaseMiddleware):
    """Sliding-window rate limit per Telegram user (messages and button presses).

    Payment updates are never throttled: Telegram requires an answer to pre_checkout_query."""

    def __init__(self, max_events: int, window_seconds: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.max_events = max_events
        self.window = window_seconds
        self.clock = clock
        self._hits: dict[int, deque[float]] = defaultdict(deque)
        self._warned: dict[int, float] = {}

    async def __call__(self, handler: Callable[[Update, dict[str, Any]], Awaitable[Any]], event: Update, data: dict[str, Any]) -> Any:
        user = data.get("event_from_user")
        if user is None or event.pre_checkout_query is not None:
            return await handler(event, data)
        if event.message is None and event.callback_query is None:
            return await handler(event, data)

        now = self.clock()
        hits = self._hits[user.id]
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.max_events:
            if now - self._warned.get(user.id, 0) > self.window:
                self._warned[user.id] = now
                if event.callback_query:
                    await event.callback_query.answer("⏳ Slow down a little.", show_alert=False)
                elif event.message:
                    await event.message.answer("⏳ You're sending too fast. Please slow down.")
            return None
        hits.append(now)
        if len(self._hits) > 20000:  # bound memory
            for uid in [u for u, h in self._hits.items() if not h or now - h[-1] > self.window]:
                self._hits.pop(uid, None)
                self._warned.pop(uid, None)
        return await handler(event, data)
