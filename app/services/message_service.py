"""Anonymous relay of Telegram messages between two users.

Messages are re-sent with copyMessage/copyMessages so Telegram never attaches a
"forwarded from" header. Only metadata (type, ids, timestamps) is stored — never content.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from bson import ObjectId

from app.config import Settings
from app.database.session import Database
from app.utils.time import Clock

logger = logging.getLogger(__name__)

RELAY_OK = {"text", "photo", "video", "voice", "audio", "document", "sticker", "animation", "video_note"}
RELAY_CONFIRM = {"contact", "location", "venue"}  # reveal personal data: ask the sender first

CONTENT_LABELS = {
    "poll": "polls", "dice": "dice", "game": "games", "invoice": "invoices", "story": "stories",
    "paid_media": "paid media", "checklist": "checklists",
}


class PartnerUnreachable(Exception):
    """The partner blocked the bot or deleted the chat."""


class DeliveryFailed(Exception):
    """Telegram refused this particular message."""


@dataclass
class _Group:
    ids: list[int] = field(default_factory=list)
    ctx: Any = None
    task: asyncio.Task | None = None


class MessageService:
    def __init__(self, db: Database, settings: Settings, clock: Clock) -> None:
        self.db = db
        self.settings = settings
        self.clock = clock
        self._groups: dict[str, _Group] = {}
        # (user_id, message_id) -> conversation_id awaiting "send contact/location?" confirmation
        self.pending_private: dict[tuple[int, int], tuple[ObjectId, str]] = {}

    # ── classification ───────────────────────────────────────────────────
    @staticmethod
    def content_type(message: Any) -> str:
        ct = message.content_type
        return str(getattr(ct, "value", ct))

    @staticmethod
    def classify(content_type: str) -> str:
        if content_type in RELAY_OK:
            return "ok"
        if content_type in RELAY_CONFIRM:
            return "confirm"
        return "unsupported"

    # ── Telegram calls with rate-limit / error handling ──────────────────
    async def _call(self, factory: Callable[[], Awaitable[Any]]) -> Any:
        for attempt in range(3):
            try:
                return await factory()
            except TelegramRetryAfter as exc:
                if attempt == 2:
                    raise DeliveryFailed("rate limited") from exc
                await asyncio.sleep(min(float(exc.retry_after), 30.0))
            except TelegramForbiddenError as exc:
                raise PartnerUnreachable() from exc
            except TelegramBadRequest as exc:
                text = str(exc).lower()
                if "chat not found" in text or "user is deactivated" in text:
                    raise PartnerUnreachable() from exc
                raise DeliveryFailed(str(exc)) from exc
            except TelegramAPIError as exc:
                raise DeliveryFailed(str(exc)) from exc
        raise DeliveryFailed("unknown")

    async def copy_one(self, bot: Bot, from_chat_id: int, message_id: int, to_chat_id: int) -> int:
        res = await self._call(lambda: bot.copy_message(
            chat_id=to_chat_id,
            from_chat_id=from_chat_id,
            message_id=message_id,
            protect_content=self.settings.protect_content,
        ))
        return res.message_id

    async def copy_many(self, bot: Bot, from_chat_id: int, message_ids: list[int], to_chat_id: int) -> list[int]:
        res = await self._call(lambda: bot.copy_messages(
            chat_id=to_chat_id,
            from_chat_id=from_chat_id,
            message_ids=sorted(message_ids),
            protect_content=self.settings.protect_content,
        ))
        return [m.message_id for m in res]

    # ── media groups (albums) ────────────────────────────────────────────
    async def add_to_group(
        self,
        group_id: str,
        message_id: int,
        ctx: Any,
        on_flush: Callable[[Any, list[int]], Awaitable[None]],
    ) -> None:
        """Collect album parts for a moment, then flush them as ONE copyMessages call
        (this is what keeps the album grouped on the receiving side)."""
        group = self._groups.setdefault(group_id, _Group(ctx=ctx))
        group.ids.append(message_id)
        if group.task and not group.task.done():
            group.task.cancel()
        group.task = asyncio.create_task(self._flush_later(group_id, on_flush))

    async def _flush_later(self, group_id: str, on_flush: Callable[[Any, list[int]], Awaitable[None]]) -> None:
        try:
            await asyncio.sleep(self.settings.media_group_delay)
        except asyncio.CancelledError:
            return
        group = self._groups.pop(group_id, None)
        if group is None:
            return
        try:
            await on_flush(group.ctx, sorted(group.ids))
        except Exception:  # noqa: BLE001
            logger.exception("Failed to flush media group")

    # ── metadata (no content!) ───────────────────────────────────────────
    async def record(
        self,
        conversation_id: ObjectId,
        sender_id: int,
        recipient_id: int,
        content_type: str,
        delivered_ids: list[int],
    ) -> None:
        now = self.clock()
        docs = [{
            "conversation_id": conversation_id,
            "sender_id": sender_id,
            "recipient_id": recipient_id,
            "content_type": content_type,
            "delivered_message_id": mid,
            "sent_at": now,
        } for mid in delivered_ids]
        if docs:
            await self.db.messages_metadata.insert_many(docs)
