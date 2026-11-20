"""Settings an owner can change while the bot runs (stored in MongoDB)."""
from __future__ import annotations

import time

from app.database.session import Database

DEFAULT_MAINTENANCE_TEXT = "🛠 The bot is under maintenance right now. Please try again a little later."


class RuntimeService:
    CACHE_SECONDS = 5.0

    def __init__(self, db: Database) -> None:
        self.db = db
        self._state: tuple[bool, str] = (False, DEFAULT_MAINTENANCE_TEXT)
        self._loaded_at = 0.0

    async def maintenance(self) -> tuple[bool, str]:
        if time.monotonic() - self._loaded_at > self.CACHE_SECONDS:
            doc = await self.db.bot_settings.find_one({"_id": "runtime"}) or {}
            self._state = (
                bool(doc.get("maintenance", False)),
                doc.get("maintenance_message") or DEFAULT_MAINTENANCE_TEXT,
            )
            self._loaded_at = time.monotonic()
        return self._state

    async def set_maintenance(self, enabled: bool, message: str | None = None) -> None:
        update: dict = {"maintenance": enabled}
        if message:
            update["maintenance_message"] = message
        await self.db.bot_settings.update_one({"_id": "runtime"}, {"$set": update}, upsert=True)
        self._loaded_at = 0.0  # visible to this process immediately
