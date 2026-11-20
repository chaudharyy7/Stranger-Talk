"""Who may do what.

* Owners (OWNER_IDS)  – everything; they are also administrators and always have Premium.
* Admins (ADMIN_IDS + admins added at runtime by an owner) – moderation dashboard.
"""
from __future__ import annotations

import time
from datetime import datetime

from app.config import Settings
from app.database.session import Database
from app.utils.time import Clock


class StaffService:
    CACHE_SECONDS = 30.0

    def __init__(self, db: Database, settings: Settings, clock: Clock) -> None:
        self.db = db
        self.settings = settings
        self.clock = clock
        self._cache: set[int] = set()
        self._cache_at = 0.0

    def is_owner(self, user_id: int) -> bool:
        return self.settings.is_owner(user_id)

    async def _runtime_admins(self) -> set[int]:
        # Cached: the admin router's filter runs for every private message.
        if time.monotonic() - self._cache_at > self.CACHE_SECONDS:
            self._cache = {d["_id"] async for d in self.db.staff.find({}, {"_id": 1})}
            self._cache_at = time.monotonic()
        return self._cache

    def _invalidate(self) -> None:
        self._cache_at = 0.0

    async def is_admin(self, user_id: int) -> bool:
        if user_id in self.settings.staff_ids:
            return True
        return user_id in await self._runtime_admins()

    async def all_ids(self) -> list[int]:
        self._invalidate()
        return sorted(self.settings.staff_ids | await self._runtime_admins())

    async def add_admin(self, user_id: int, added_by: int) -> bool:
        """True when newly added. Env-configured staff are already admins."""
        if user_id in self.settings.staff_ids:
            return False
        res = await self.db.staff.update_one(
            {"_id": user_id},
            {"$setOnInsert": {"added_by": added_by, "added_at": self.clock()}},
            upsert=True,
        )
        self._invalidate()
        return res.upserted_id is not None

    async def remove_admin(self, user_id: int) -> bool:
        """Only runtime-added admins can be removed (env staff are managed in the environment)."""
        res = await self.db.staff.delete_one({"_id": user_id})
        self._invalidate()
        return res.deleted_count == 1

    async def list_staff(self) -> dict[str, list[tuple[int, datetime | None]]]:
        runtime = [(d["_id"], d.get("added_at")) async for d in self.db.staff.find({}).sort("added_at", 1)]
        return {
            "owners": [(i, None) for i in sorted(self.settings.owner_ids)],
            "env_admins": [(i, None) for i in sorted(self.settings.admin_ids - self.settings.owner_ids)],
            "runtime_admins": runtime,
        }
