"""A fake Telegram: records every Bot API call and feeds raw updates into the real Dispatcher."""
from __future__ import annotations

import asyncio
import itertools
import time
from datetime import datetime, timezone
from typing import Any

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.methods import CopyMessage, CopyMessages
from aiogram.types import Chat, Message, MessageId, User

from app.bootstrap import create_dispatcher

_ids = itertools.count(5000)


class FakeSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[Any] = []
        self.errors: dict[tuple[str, int], Exception] = {}  # (method name, chat_id) -> exception to raise
        self.errors_once: dict[tuple[str, int], Exception] = {}  # same, but raised a single time

    async def close(self) -> None:  # pragma: no cover
        pass

    async def stream_content(self, *a: Any, **k: Any):  # pragma: no cover
        yield b""

    async def make_request(self, bot: Bot, method: Any, timeout: int | None = None) -> Any:
        name = type(method).__name__
        self.calls.append(method)
        chat_id = getattr(method, "chat_id", None)
        err = self.errors_once.pop((name, chat_id), None) or self.errors.get((name, chat_id))
        if err is not None:
            raise err
        if name in {"SendMessage", "SendInvoice"}:
            return Message(
                message_id=next(_ids), date=datetime.now(timezone.utc),
                chat=Chat(id=int(chat_id), type="private"), text=getattr(method, "text", None),
            )
        if name == "GetMe":
            return User(id=1, is_bot=True, first_name="bot", username="testbot")
        if name == "CopyMessage":
            return MessageId(message_id=next(_ids))
        if name == "CopyMessages":
            return [MessageId(message_id=next(_ids)) for _ in method.message_ids]
        return True

    def of(self, name: str) -> list[Any]:
        return [m for m in self.calls if type(m).__name__ == name]


class Harness:
    def __init__(self, container: Any) -> None:
        self.c = container
        self.session = FakeSession()
        self.bot = Bot("123456789:" + "A" * 35, session=self.session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        self.dp = create_dispatcher(container)
        self._update_id = itertools.count(1)

    # ── building updates ─────────────────────────────────────────────────
    def _user(self, uid: int) -> dict[str, Any]:
        return {"id": uid, "is_bot": False, "first_name": f"U{uid}"}

    async def send(self, uid: int, text: str | None = None, **extra: Any) -> None:
        msg: dict[str, Any] = {
            "message_id": next(_ids), "date": int(time.time()),
            "chat": {"id": uid, "type": "private"}, "from": self._user(uid),
        }
        if text is not None:
            msg["text"] = text
            if text.startswith("/"):
                msg["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
        msg.update(extra)
        await self.dp.feed_raw_update(self.bot, {"update_id": next(self._update_id), "message": msg})

    async def press(self, uid: int, data: str) -> None:
        await self.dp.feed_raw_update(self.bot, {"update_id": next(self._update_id), "callback_query": {
            "id": str(next(_ids)), "from": self._user(uid), "chat_instance": "x", "data": data,
            "message": {"message_id": 1, "date": int(time.time()), "chat": {"id": uid, "type": "private"},
                        "from": {"id": 1, "is_bot": True, "first_name": "bot"}, "text": "menu"},
        }})

    async def raw(self, **fields: Any) -> None:
        await self.dp.feed_raw_update(self.bot, {"update_id": next(self._update_id), **fields})

    # ── inspecting what the bot did ──────────────────────────────────────
    def texts_to(self, uid: int) -> list[str]:
        out = []
        for m in self.session.calls:
            name = type(m).__name__
            if name in {"SendMessage", "EditMessageText"} and getattr(m, "chat_id", None) == uid:
                out.append(m.text)
        return out

    def last_text(self, uid: int) -> str:
        return self.texts_to(uid)[-1]

    def copies_to(self, uid: int) -> list[Any]:
        return [m for m in self.session.calls if isinstance(m, (CopyMessage, CopyMessages)) and m.chat_id == uid]

    async def connect(self, a: int, b: int) -> None:
        """Make two registered users chat with each other through the real handlers."""
        await self.send(a, "/find")
        await self.send(b, "/find")
