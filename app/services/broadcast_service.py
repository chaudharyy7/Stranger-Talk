"""Owner broadcasts: send one message to many users, safely.

* Audience: all | free | premium | active (seen in the last 7 days). Banned, unregistered and
  users who blocked the bot are never included.
* Throttled below Telegram's ~30 msg/s limit (BROADCAST_RATE_PER_SECOND), honours RetryAfter.
* Users who blocked the bot are flagged (`bot_blocked`) and skipped in future broadcasts.
* Runs in the background, progress is stored in the `broadcasts` collection, can be stopped.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter
from bson import ObjectId
from pymongo import ReturnDocument

from app.config import Settings
from app.database.models import AccountStatus, Membership
from app.database.session import Database
from app.utils.time import Clock
from app.utils.ui import safe_send

logger = logging.getLogger(__name__)

AUDIENCES = ("all", "free", "premium", "active")


class BroadcastService:
    def __init__(self, db: Database, settings: Settings, clock: Clock) -> None:
        self.db = db
        self.settings = settings
        self.clock = clock
        self._tasks: dict[ObjectId, asyncio.Task] = {}
        self._stop: set[ObjectId] = set()

    # ── audience ─────────────────────────────────────────────────────────
    def audience_query(self, audience: str) -> dict[str, Any]:
        now = self.clock()
        query: dict[str, Any] = {
            "account_status": AccountStatus.ACTIVE,
            "registration_date": {"$ne": None},
            "bot_blocked": {"$ne": True},
        }
        premium = {"$or": [
            {"membership_type": Membership.PREMIUM, "premium_expiry": {"$gt": now}},
            {"_id": {"$in": list(self.settings.owner_ids)}},   # owners always count as Premium
        ]}
        if audience == "premium":
            query.update(premium)
        elif audience == "free":
            query["$nor"] = [premium]
        elif audience == "active":
            query["last_active"] = {"$gte": now - timedelta(days=7)}
        return query

    async def count_audience(self, audience: str) -> int:
        return await self.db.users.count_documents(self.audience_query(audience))

    # ── lifecycle ────────────────────────────────────────────────────────
    async def create(
        self,
        created_by: int,
        audience: str,
        total: int,
        text: str | None = None,
        source_chat_id: int | None = None,
        source_message_id: int | None = None,
    ) -> ObjectId:
        res = await self.db.broadcasts.insert_one({
            "created_by": created_by,
            "audience": audience,
            "mode": "copy" if source_message_id else "text",
            "text": text,
            "source_chat_id": source_chat_id,
            "source_message_id": source_message_id,
            "status": "pending",
            "total": total,
            "sent": 0, "failed": 0, "blocked": 0,
            "created_at": self.clock(),
            "started_at": None, "finished_at": None,
        })
        return res.inserted_id

    async def get(self, broadcast_id: ObjectId) -> dict[str, Any] | None:
        return await self.db.broadcasts.find_one({"_id": broadcast_id})

    async def recent(self, limit: int = 5) -> list[dict[str, Any]]:
        return await self.db.broadcasts.find({}).sort("created_at", -1).limit(limit).to_list()

    async def discard(self, broadcast_id: ObjectId) -> bool:
        res = await self.db.broadcasts.update_one(
            {"_id": broadcast_id, "status": "pending"}, {"$set": {"status": "cancelled", "finished_at": self.clock()}}
        )
        return res.modified_count == 1

    async def start(self, bot: Bot, broadcast_id: ObjectId) -> bool:
        """Start exactly once (pending -> running is an atomic claim, so a double tap can't double-send)."""
        doc = await self.db.broadcasts.find_one_and_update(
            {"_id": broadcast_id, "status": "pending"},
            {"$set": {"status": "running", "started_at": self.clock()}},
            return_document=ReturnDocument.AFTER,
        )
        if doc is None:
            return False
        task = asyncio.create_task(self._run(bot, doc))
        self._tasks[broadcast_id] = task
        task.add_done_callback(lambda _t, b=broadcast_id: self._tasks.pop(b, None))
        return True

    async def stop(self, broadcast_id: ObjectId) -> bool:
        if broadcast_id in self._tasks:
            self._stop.add(broadcast_id)
            return True
        return False

    def running_ids(self) -> list[ObjectId]:
        return list(self._tasks)

    async def wait(self, broadcast_id: ObjectId) -> None:
        task = self._tasks.get(broadcast_id)
        if task:
            await asyncio.shield(task)

    async def shutdown(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def mark_interrupted(self) -> int:
        """At startup: broadcasts that were running when the process died cannot continue."""
        res = await self.db.broadcasts.update_many(
            {"status": "running"}, {"$set": {"status": "interrupted", "finished_at": self.clock()}}
        )
        return res.modified_count

    # ── the sending loop ─────────────────────────────────────────────────
    async def _send_one(self, bot: Bot, doc: dict[str, Any], user_id: int) -> None:
        if doc["mode"] == "copy":
            await bot.copy_message(chat_id=user_id, from_chat_id=doc["source_chat_id"], message_id=doc["source_message_id"])
        else:
            await bot.send_message(user_id, doc["text"])

    async def _run(self, bot: Bot, doc: dict[str, Any]) -> None:
        bid = doc["_id"]
        sent = failed = blocked = 0
        delay = 1.0 / self.settings.broadcast_rate_per_second
        status = "done"
        try:
            cursor = self.db.users.find(self.audience_query(doc["audience"]), {"_id": 1}).sort("_id", 1)
            async for user in cursor:
                if bid in self._stop:
                    status = "cancelled"
                    break
                uid = user["_id"]
                for attempt in range(2):
                    try:
                        await self._send_one(bot, doc, uid)
                        sent += 1
                    except TelegramRetryAfter as exc:
                        if attempt == 0:
                            await asyncio.sleep(min(float(exc.retry_after) + 1, 60))
                            continue
                        failed += 1
                    except TelegramForbiddenError:
                        blocked += 1
                        await self.db.users.update_one({"_id": uid}, {"$set": {"bot_blocked": True}})
                    except TelegramAPIError:
                        failed += 1
                    break
                if (sent + failed + blocked) % 25 == 0:
                    await self.db.broadcasts.update_one(
                        {"_id": bid}, {"$set": {"sent": sent, "failed": failed, "blocked": blocked}}
                    )
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            status = "interrupted"
            raise
        except Exception:  # noqa: BLE001
            logger.exception("Broadcast %s crashed", bid)
            status = "failed"
        finally:
            self._stop.discard(bid)
            await self.db.broadcasts.update_one(
                {"_id": bid},
                {"$set": {"status": status, "sent": sent, "failed": failed, "blocked": blocked, "finished_at": self.clock()}},
            )
        if status != "interrupted":
            await safe_send(
                bot, doc["created_by"],
                f"📣 <b>Broadcast {status}</b>\n"
                f"✅ Delivered: <b>{sent}</b>\n🚫 Blocked the bot: <b>{blocked}</b>\n⚠️ Failed: <b>{failed}</b>\n"
                f"Audience: {doc['audience']} ({doc['total']} at start)",
            )
