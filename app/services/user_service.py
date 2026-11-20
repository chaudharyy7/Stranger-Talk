from __future__ import annotations

from datetime import timedelta
from typing import Any

from pymongo.errors import DuplicateKeyError

from app.config import Settings
from app.database.models import AccountStatus, Membership
from app.database.session import Database
from app.utils.time import Clock


class UserService:
    def __init__(self, db: Database, settings: Settings, clock: Clock) -> None:
        self.db = db
        self.settings = settings
        self.clock = clock

    # ── users ────────────────────────────────────────────────────────────
    async def get(self, user_id: int) -> dict[str, Any] | None:
        return await self.db.users.find_one({"_id": user_id})

    async def get_or_create(self, user_id: int) -> tuple[dict[str, Any], bool]:
        """Return (user, created). Safe when two /start messages arrive simultaneously."""
        now = self.clock()
        doc = {
            "telegram_id": user_id,
            "membership_type": Membership.FREE,
            "account_status": AccountStatus.ACTIVE,
            "registration_date": None,
            "terms_accepted_at": None,
            "premium_expiry": None,
            "last_active": now,
            "stats": {"chats": 0, "searches": 0, "messages": 0},
            "created_at": now,
            "updated_at": now,
        }
        created = False
        for _ in range(2):
            try:
                res = await self.db.users.update_one({"_id": user_id}, {"$setOnInsert": doc}, upsert=True)
                created = res.upserted_id is not None
                break
            except DuplicateKeyError:  # concurrent upsert of the same _id; just read it
                continue
        user = await self.get(user_id)
        assert user is not None
        return user, created

    async def touch(self, user_id: int) -> None:
        """Update last_active at most once per minute (keeps writes cheap)."""
        now = self.clock()
        await self.db.users.update_one(
            {"_id": user_id, "last_active": {"$lt": now - timedelta(seconds=60)}},
            {"$set": {"last_active": now}},
        )

    async def clear_bot_blocked(self, user_id: int) -> None:
        """The user wrote to us again, so they have unblocked the bot."""
        await self.db.users.update_one({"_id": user_id, "bot_blocked": True}, {"$set": {"bot_blocked": False}})

    async def accept_terms(self, user_id: int) -> None:
        now = self.clock()
        await self.db.users.update_one(
            {"_id": user_id}, {"$set": {"terms_accepted_at": now, "updated_at": now}}
        )

    async def inc_stat(self, user_id: int, key: str, n: int = 1) -> None:
        await self.db.users.update_one({"_id": user_id}, {"$inc": {f"stats.{key}": n}})

    def effective_membership(self, user: dict[str, Any] | None) -> str:
        """Owners always have Premium. Everyone else: Premium only while the expiry lies in the
        future (lazy check; the sweeper downgrades the stored value too)."""
        if user and self.settings.is_owner(user["_id"]):
            return Membership.PREMIUM
        if not user or user.get("membership_type") != Membership.PREMIUM:
            return Membership.FREE
        expiry = user.get("premium_expiry")
        if expiry is None or expiry <= self.clock():
            return Membership.FREE
        return Membership.PREMIUM

    async def membership_of(self, user_id: int) -> str:
        return self.effective_membership(await self.get(user_id))

    # ── profiles ─────────────────────────────────────────────────────────
    async def get_profile(self, user_id: int) -> dict[str, Any] | None:
        return await self.db.profiles.find_one({"_id": user_id})

    async def get_profiles(self, user_ids: list[int]) -> dict[int, dict[str, Any]]:
        cursor = self.db.profiles.find({"_id": {"$in": user_ids}})
        return {doc["_id"]: doc async for doc in cursor}

    async def is_registered(self, user_id: int) -> bool:
        user = await self.get(user_id)
        return bool(user and user.get("registration_date") and await self.get_profile(user_id))

    async def complete_registration(
        self, user_id: int, display_name: str, age: int, country: str | None, gender: str
    ) -> None:
        now = self.clock()
        await self.db.profiles.update_one(
            {"_id": user_id},
            {"$set": {
                "user_id": user_id,
                "display_name": display_name,
                "age": age,
                "country": country,
                "gender": gender,
                "updated_at": now,
            }},
            upsert=True,
        )
        await self.db.users.update_one(
            {"_id": user_id, "registration_date": None},
            {"$set": {"registration_date": now, "updated_at": now}},
        )

    async def update_profile_field(self, user_id: int, field: str, value: Any) -> None:
        column = {"name": "display_name"}.get(field, field)
        if column not in {"display_name", "age", "country", "gender"}:
            raise ValueError(f"Unknown profile field: {field}")
        await self.db.profiles.update_one(
            {"_id": user_id}, {"$set": {column: value, "updated_at": self.clock()}}
        )

    # ── admin helpers ────────────────────────────────────────────────────
    async def set_account_status(self, user_id: int, status: str) -> None:
        await self.db.users.update_one(
            {"_id": user_id}, {"$set": {"account_status": status, "updated_at": self.clock()}}
        )
