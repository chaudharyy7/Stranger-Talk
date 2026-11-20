from __future__ import annotations

import logging

from pymongo import AsyncMongoClient
from pymongo.asynchronous.collection import AsyncCollection

logger = logging.getLogger(__name__)


class Database:
    """Thin wrapper around the async Mongo client exposing every collection by name."""

    def __init__(self, uri: str, name: str) -> None:
        self.name = name
        self.client: AsyncMongoClient = AsyncMongoClient(
            uri,
            tz_aware=True,
            serverSelectionTimeoutMS=8000,
            appname="anonymous-chat-bot",
        )
        self.db = self.client[name]

    def _c(self, name: str) -> AsyncCollection:
        return self.db[name]

    @property
    def users(self) -> AsyncCollection: return self._c("users")
    @property
    def profiles(self) -> AsyncCollection: return self._c("profiles")
    @property
    def subscriptions(self) -> AsyncCollection: return self._c("subscriptions")
    @property
    def payments(self) -> AsyncCollection: return self._c("payments")
    @property
    def daily_usage(self) -> AsyncCollection: return self._c("daily_usage")
    @property
    def conversations(self) -> AsyncCollection: return self._c("conversations")
    @property
    def participants(self) -> AsyncCollection: return self._c("conversation_participants")
    @property
    def messages_metadata(self) -> AsyncCollection: return self._c("messages_metadata")
    @property
    def reports(self) -> AsyncCollection: return self._c("reports")
    @property
    def blocked_users(self) -> AsyncCollection: return self._c("blocked_users")
    @property
    def banned_users(self) -> AsyncCollection: return self._c("banned_users")
    @property
    def queue(self) -> AsyncCollection: return self._c("matchmaking_queue")
    @property
    def stats_daily(self) -> AsyncCollection: return self._c("stats_daily")
    @property
    def staff(self) -> AsyncCollection: return self._c("staff")
    @property
    def broadcasts(self) -> AsyncCollection: return self._c("broadcasts")
    @property
    def bot_settings(self) -> AsyncCollection: return self._c("bot_settings")
    @property
    def schema_migrations(self) -> AsyncCollection: return self._c("schema_migrations")

    async def ping(self) -> bool:
        try:
            await self.client.admin.command("ping")
            return True
        except Exception:  # noqa: BLE001
            logger.exception("MongoDB ping failed")
            return False

    async def drop(self) -> None:
        await self.client.drop_database(self.name)

    async def close(self) -> None:
        await self.client.close()
