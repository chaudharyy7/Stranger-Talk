from __future__ import annotations

from datetime import timedelta
from typing import Any

from app.config import Settings
from app.database.models import AccountStatus, Membership, ReportStatus
from app.database.session import Database
from app.utils.time import Clock, utc_date


class StatsService:
    def __init__(self, db: Database, settings: Settings, clock: Clock) -> None:
        self.db = db
        self.settings = settings
        self.clock = clock

    async def incr(self, field: str, n: int = 1) -> None:
        if n:
            await self.db.stats_daily.update_one(
                {"_id": utc_date(self.clock())}, {"$inc": {field: n}}, upsert=True
            )

    async def today(self) -> dict[str, int]:
        doc = await self.db.stats_daily.find_one({"_id": utc_date(self.clock())}) or {}
        return {k: int(doc.get(k, 0)) for k in ("searches", "matches", "failed")}

    async def snapshot(self) -> dict[str, Any]:
        now = self.clock()
        c = self.db
        today = await self.today()
        return {
            "total_users": await c.users.count_documents({}),
            "registered_users": await c.users.count_documents({"registration_date": {"$ne": None}}),
            "active_24h": await c.users.count_documents({"last_active": {"$gte": now - timedelta(hours=24)}}),
            "available_now": await c.queue.count_documents({}),
            "active_conversations": await c.conversations.count_documents({"status": "active"}),
            "free_members": await c.users.count_documents({"membership_type": Membership.FREE}),
            "premium_members": await c.users.count_documents({"membership_type": Membership.PREMIUM}),
            "searches_today": today["searches"],
            "matches_today": today["matches"],
            "failed_today": today["failed"],
            "open_reports": await c.reports.count_documents({"status": ReportStatus.OPEN}),
            "banned_users": await c.users.count_documents({"account_status": AccountStatus.BANNED}),
        }
