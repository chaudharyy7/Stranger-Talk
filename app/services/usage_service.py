from __future__ import annotations

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.config import Settings
from app.database.session import Database
from app.utils.time import Clock, utc_date


class UsageService:
    """Daily search counters. The day key is the UTC date, so the reset at 00:00 UTC is automatic
    and survives bot restarts (everything lives in MongoDB)."""

    def __init__(self, db: Database, settings: Settings, clock: Clock) -> None:
        self.db = db
        self.settings = settings
        self.clock = clock

    def _key(self, user_id: int) -> tuple[str, str]:
        day = utc_date(self.clock())
        return f"{user_id}:{day}", day

    async def used_today(self, user_id: int) -> int:
        key, _ = self._key(user_id)
        doc = await self.db.daily_usage.find_one({"_id": key})
        return int(doc["count"]) if doc else 0

    async def try_consume(self, user_id: int, limit: int) -> tuple[bool, int]:
        """Atomically add one search if (and only if) the user is below `limit`.

        The filter {count < limit} + upsert makes this a single atomic operation, so concurrent
        requests can never push the counter above the limit. If the document exists and is at the
        limit, the upsert tries to insert a duplicate _id and fails -> limit reached.
        """
        key, day = self._key(user_id)
        for _ in range(6):
            try:
                doc = await self.db.daily_usage.find_one_and_update(
                    {"_id": key, "count": {"$lt": limit}},
                    {"$inc": {"count": 1}, "$setOnInsert": {"user_id": user_id, "date": day}},
                    upsert=True,
                    return_document=ReturnDocument.AFTER,
                )
                return True, int(doc["count"])
            except DuplicateKeyError:
                used = await self.used_today(user_id)
                if used >= limit:
                    return False, used
                # Lost a first-insert race against a concurrent request; retry on the update path.
        return False, await self.used_today(user_id)

    async def refund(self, user_id: int) -> None:
        key, _ = self._key(user_id)
        await self.db.daily_usage.update_one({"_id": key, "count": {"$gt": 0}}, {"$inc": {"count": -1}})
