from __future__ import annotations

from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from pymongo.errors import DuplicateKeyError

from app.config import Settings
from app.database.models import AccountStatus, ReportStatus, REPORT_CATEGORIES
from app.database.session import Database
from app.services.user_service import UserService
from app.utils.time import Clock


def parse_object_id(value: str) -> ObjectId | None:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


class ModerationService:
    def __init__(self, db: Database, settings: Settings, users: UserService, clock: Clock) -> None:
        self.db = db
        self.settings = settings
        self.users = users
        self.clock = clock

    # ── blocking ─────────────────────────────────────────────────────────
    async def block(self, blocker_id: int, blocked_id: int, conversation_id: ObjectId | None = None) -> bool:
        """Returns True when newly blocked, False when it already existed."""
        if blocker_id == blocked_id:
            return False
        try:
            await self.db.blocked_users.insert_one({
                "blocker_id": blocker_id,
                "blocked_id": blocked_id,
                "conversation_id": conversation_id,
                "created_at": self.clock(),
            })
            return True
        except DuplicateKeyError:
            return False

    async def list_blocked(self, blocker_id: int) -> list[dict[str, Any]]:
        cursor = self.db.blocked_users.find({"blocker_id": blocker_id}).sort("created_at", -1).limit(50)
        return await cursor.to_list()

    async def unblock(self, block_id: ObjectId, blocker_id: int) -> bool:
        res = await self.db.blocked_users.delete_one({"_id": block_id, "blocker_id": blocker_id})
        return res.deleted_count == 1

    async def relations(self, user_id: int) -> list[int]:
        """Everyone who must never be matched with `user_id` (blocked by them OR blocking them)."""
        ids: set[int] = set()
        async for d in self.db.blocked_users.find({"blocker_id": user_id}, {"blocked_id": 1}):
            ids.add(d["blocked_id"])
        async for d in self.db.blocked_users.find({"blocked_id": user_id}, {"blocker_id": 1}):
            ids.add(d["blocker_id"])
        return sorted(ids)

    async def is_blocked_between(self, a: int, b: int) -> bool:
        return b in await self.relations(a)

    # ── reports ──────────────────────────────────────────────────────────
    async def evidence_ids(self, conversation_id: ObjectId, reporter_id: int, reported_id: int, limit: int = 10) -> list[int]:
        """Ids of the last messages the reporter RECEIVED from the reported user.
        Only ids are kept; content stays inside the reporter's own Telegram chat."""
        cursor = self.db.messages_metadata.find({
            "conversation_id": conversation_id,
            "recipient_id": reporter_id,
            "sender_id": reported_id,
        }).sort("sent_at", -1).limit(limit)
        docs = await cursor.to_list()
        return [d["delivered_message_id"] for d in reversed(docs) if d.get("delivered_message_id")]

    async def create_report(
        self,
        reporter_id: int,
        reported_id: int,
        conversation_id: ObjectId,
        category: str,
        context: str | None = None,
    ) -> ObjectId | None:
        """Returns the report id, or None if this reporter already reported this conversation."""
        if category not in REPORT_CATEGORIES:
            category = "other"
        evidence = await self.evidence_ids(conversation_id, reporter_id, reported_id)
        try:
            res = await self.db.reports.insert_one({
                "reporter_id": reporter_id,
                "reported_id": reported_id,
                "conversation_id": conversation_id,
                "category": category,
                "context": (context or "").strip()[:500] or None,
                "evidence_message_ids": evidence,
                "status": ReportStatus.OPEN,
                "created_at": self.clock(),
                "reviewed_by": None,
                "reviewed_at": None,
            })
            return res.inserted_id
        except DuplicateKeyError:
            return None

    async def get_report(self, report_id: ObjectId) -> dict[str, Any] | None:
        return await self.db.reports.find_one({"_id": report_id})

    async def next_open_report(self, after: ObjectId | None = None) -> dict[str, Any] | None:
        query: dict[str, Any] = {"status": ReportStatus.OPEN}
        if after is not None:
            query["_id"] = {"$gt": after}
        return await self.db.reports.find_one(query, sort=[("_id", 1)])

    async def resolve_report(self, report_id: ObjectId, status: str, admin_id: int) -> bool:
        res = await self.db.reports.update_one(
            {"_id": report_id, "status": ReportStatus.OPEN},
            {"$set": {"status": status, "reviewed_by": admin_id, "reviewed_at": self.clock()}},
        )
        return res.modified_count == 1

    async def report_counts(self, user_id: int) -> tuple[int, int]:
        against = await self.db.reports.count_documents({"reported_id": user_id})
        filed = await self.db.reports.count_documents({"reporter_id": user_id})
        return against, filed

    # ── bans ─────────────────────────────────────────────────────────────
    async def is_banned(self, user_id: int) -> bool:
        return await self.db.banned_users.find_one({"_id": user_id}) is not None

    async def ban(self, user_id: int, reason: str | None, admin_id: int) -> None:
        await self.users.get_or_create(user_id)
        await self.db.banned_users.update_one(
            {"_id": user_id},
            {"$set": {"reason": reason, "banned_by": admin_id, "banned_at": self.clock()}},
            upsert=True,
        )
        await self.users.set_account_status(user_id, AccountStatus.BANNED)

    async def unban(self, user_id: int) -> bool:
        res = await self.db.banned_users.delete_one({"_id": user_id})
        await self.users.set_account_status(user_id, AccountStatus.ACTIVE)
        return res.deleted_count == 1
