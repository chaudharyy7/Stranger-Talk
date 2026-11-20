"""Matchmaking queue and conversation lifecycle.

Concurrency design (no transactions required, works on a standalone MongoDB):

* A user is "searching" while a document with _id=telegram_id exists in `matchmaking_queue`.
* To pair, a matcher atomically *claims* a compatible partner with find_one_and_delete.
  MongoDB guarantees only one caller can delete (claim) a given document.
* The matcher then claims *itself* with delete_one. If that fails, somebody else already
  matched us; the claimed partner is put back and we retry/stop.
* `conversation_participants` uses _id=telegram_id, so the database itself refuses a second
  active conversation for the same user.
* An in-process asyncio.Lock removes most contention inside one instance; the atomic
  operations above keep it correct across several instances.
* A background sweeper (see app/tasks.py) calls `pair_waiting` as a safety net.
"""
from __future__ import annotations

import asyncio
import enum
import logging
import random
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.config import Settings
from app.database.models import AccountStatus, ConversationStatus, Membership, Pref
from app.database.session import Database
from app.services.moderation_service import ModerationService
from app.services.stats_service import StatsService
from app.services.usage_service import UsageService
from app.services.user_service import UserService
from app.utils.time import Clock

logger = logging.getLogger(__name__)


class SearchStatus(enum.Enum):
    NOT_REGISTERED = "not_registered"
    BANNED = "banned"
    IN_CHAT = "in_chat"
    ALREADY_SEARCHING = "already_searching"
    GENDER_NOT_ALLOWED = "gender_not_allowed"
    LIMIT_REACHED = "limit_reached"
    WAITING = "waiting"
    MATCHED = "matched"
    MATCHED_ELSEWHERE = "matched_elsewhere"  # another worker paired us and will announce it


@dataclass
class MatchResult:
    conversation_id: ObjectId
    user_a: int
    user_b: int


@dataclass
class SearchOutcome:
    status: SearchStatus
    match: MatchResult | None = None
    used: int = 0
    limit: int = 0


@dataclass
class EndedConversation:
    conversation_id: ObjectId
    participants: tuple[int, ...]
    ended_by: int | None
    reason: str

    def partner_of(self, user_id: int) -> int | None:
        others = [p for p in self.participants if p != user_id]
        return others[0] if others else None


class MatchmakingService:
    MAX_ATTEMPTS = 5

    def __init__(
        self,
        db: Database,
        settings: Settings,
        users: UserService,
        usage: UsageService,
        moderation: ModerationService,
        stats: StatsService,
        clock: Clock,
    ) -> None:
        self.db = db
        self.settings = settings
        self.users = users
        self.usage = usage
        self.moderation = moderation
        self.stats = stats
        self.clock = clock
        self._lock = asyncio.Lock()

    # ── lookups ──────────────────────────────────────────────────────────
    async def get_active(self, user_id: int) -> dict[str, Any] | None:
        return await self.db.participants.find_one({"_id": user_id})

    async def in_queue(self, user_id: int) -> bool:
        return await self.db.queue.find_one({"_id": user_id}, {"_id": 1}) is not None

    async def get_conversation(self, conversation_id: ObjectId) -> dict[str, Any] | None:
        return await self.db.conversations.find_one({"_id": conversation_id})

    async def last_ended(self, user_id: int) -> dict[str, Any] | None:
        """Most recently ended conversation of the user, if it ended inside the report window."""
        cutoff = self.clock() - timedelta(minutes=self.settings.report_window_minutes)
        return await self.db.conversations.find_one(
            {"participants": user_id, "status": ConversationStatus.ENDED, "ended_at": {"$gte": cutoff}},
            sort=[("ended_at", -1)],
        )

    # ── searching ────────────────────────────────────────────────────────
    async def start_search(self, user_id: int, pref: str = Pref.ANY) -> SearchOutcome:
        user = await self.users.get(user_id)
        profile = await self.users.get_profile(user_id)
        if not user or not profile or not user.get("registration_date"):
            return SearchOutcome(SearchStatus.NOT_REGISTERED)
        if user.get("account_status") == AccountStatus.BANNED:
            return SearchOutcome(SearchStatus.BANNED)
        if await self.get_active(user_id):
            return SearchOutcome(SearchStatus.IN_CHAT)
        if await self.in_queue(user_id):
            return SearchOutcome(SearchStatus.ALREADY_SEARCHING)

        if pref not in Pref.ALL:
            pref = Pref.ANY
        membership = self.users.effective_membership(user)
        if pref != Pref.ANY and membership != Membership.PREMIUM:
            return SearchOutcome(SearchStatus.GENDER_NOT_ALLOWED)

        # Everything above is a rejected request: it does NOT use up a search.
        limit = self.settings.daily_limit(membership)
        ok, used = await self.usage.try_consume(user_id, limit)
        if not ok:
            return SearchOutcome(SearchStatus.LIMIT_REACHED, used=used, limit=limit)

        exclude = await self.moderation.relations(user_id)
        try:
            await self.db.queue.insert_one({
                "_id": user_id,
                "gender": profile["gender"],
                "pref": pref,
                "exclude": exclude,
                "joined_at": self.clock(),
            })
        except DuplicateKeyError:  # double tap raced past the check above
            await self.usage.refund(user_id)
            return SearchOutcome(SearchStatus.ALREADY_SEARCHING)

        await self.stats.incr("searches")
        await self.users.inc_stat(user_id, "searches")

        match = await self.try_match(user_id)
        if match:
            return SearchOutcome(SearchStatus.MATCHED, match, used, limit)
        if await self.get_active(user_id):
            return SearchOutcome(SearchStatus.MATCHED_ELSEWHERE, None, used, limit)
        return SearchOutcome(SearchStatus.WAITING, None, used, limit)

    async def cancel_search(self, user_id: int) -> bool:
        res = await self.db.queue.delete_one({"_id": user_id})
        if res.deleted_count:
            await self.stats.incr("failed")
            return True
        return False

    @staticmethod
    def _compat_query(me: dict[str, Any]) -> dict[str, Any]:
        """Partner must satisfy MY preference and I must satisfy THEIRS; nobody blocked."""
        query: dict[str, Any] = {
            "_id": {"$nin": [me["_id"], *me.get("exclude", [])]},
            "pref": {"$in": [Pref.ANY, me["gender"]]},
            "exclude": {"$ne": me["_id"]},
        }
        if me["pref"] != Pref.ANY:
            query["gender"] = me["pref"]
        return query

    async def try_match(self, user_id: int) -> MatchResult | None:
        async with self._lock:
            for _ in range(self.MAX_ATTEMPTS):
                me = await self.db.queue.find_one({"_id": user_id})
                if me is None:  # cancelled, timed out or already claimed by someone else
                    return None
                partner = await self.db.queue.find_one_and_delete(
                    self._compat_query(me), sort=[("joined_at", 1)]
                )
                if partner is None:
                    return None
                claimed_me = await self.db.queue.delete_one({"_id": user_id})
                if claimed_me.deleted_count == 1:
                    return await self._create_conversation(me, partner)
                # Somebody else claimed me first: give the partner back.
                await self._restore(partner)
                if await self.get_active(user_id):
                    return None
                await asyncio.sleep(random.uniform(0.005, 0.03))  # mutual-claim collision: retry
            return None

    async def _restore(self, queue_doc: dict[str, Any]) -> None:
        await self.db.queue.replace_one({"_id": queue_doc["_id"]}, queue_doc, upsert=True)

    async def _create_conversation(self, me: dict[str, Any], partner: dict[str, Any]) -> MatchResult | None:
        a, b = me["_id"], partner["_id"]
        cid, now = ObjectId(), self.clock()
        await self.db.conversations.insert_one({
            "_id": cid,
            "participants": [a, b],
            "status": ConversationStatus.ACTIVE,
            "started_at": now,
            "last_activity": now,
            "ended_at": None,
            "ended_by": None,
            "end_reason": None,
            "message_count": 0,
        })
        inserted: list[int] = []
        try:
            for doc in (me, partner):
                other = b if doc is me else a
                await self.db.participants.insert_one({
                    "_id": doc["_id"], "conversation_id": cid, "partner_id": other,
                    "pref": doc["pref"], "joined_at": now,
                })
                inserted.append(doc["_id"])
        except DuplicateKeyError:
            # One of them is already in a conversation (stale state). Undo and requeue the free one.
            await self.db.participants.delete_many({"_id": {"$in": inserted}, "conversation_id": cid})
            await self.db.conversations.delete_one({"_id": cid})
            for doc in (me, partner):
                if not await self.get_active(doc["_id"]):
                    await self._restore(doc)
            logger.warning("Match rollback for %s/%s: participant already in a conversation", a, b)
            return None

        await self.stats.incr("matches")
        await self.users.inc_stat(a, "chats")
        await self.users.inc_stat(b, "chats")
        return MatchResult(cid, a, b)

    async def pair_waiting(self, limit: int = 100) -> list[MatchResult]:
        """Safety net: try to pair every waiting user (oldest first)."""
        results: list[MatchResult] = []
        waiting = await self.db.queue.find({}, {"_id": 1}).sort("joined_at", 1).limit(limit).to_list()
        for doc in waiting:
            match = await self.try_match(doc["_id"])
            if match:
                results.append(match)
        return results

    async def expire_queue(self) -> list[int]:
        """Remove users who waited longer than QUEUE_TIMEOUT_SECONDS. Returns their ids."""
        cutoff = self.clock() - timedelta(seconds=self.settings.queue_timeout_seconds)
        expired: list[int] = []
        while True:
            doc = await self.db.queue.find_one_and_delete({"joined_at": {"$lt": cutoff}})
            if doc is None:
                break
            expired.append(doc["_id"])
            if self.settings.refund_timed_out_searches:
                await self.usage.refund(doc["_id"])
        await self.stats.incr("failed", len(expired))
        return expired

    # ── ending conversations ─────────────────────────────────────────────
    async def end_conversation(self, user_id: int, reason: str = "stopped") -> EndedConversation | None:
        mine = await self.db.participants.find_one({"_id": user_id})
        if mine is None:
            return None
        ended = await self._end_by_id(mine["conversation_id"], ended_by=user_id, reason=reason)
        if ended is None:  # someone else ended it a moment ago; just clear our stale row
            await self.db.participants.delete_one({"_id": user_id, "conversation_id": mine["conversation_id"]})
        return ended

    async def _end_by_id(self, conversation_id: ObjectId, ended_by: int | None, reason: str) -> EndedConversation | None:
        # Claiming through the conversation document guarantees exactly one winner.
        conv = await self.db.conversations.find_one_and_update(
            {"_id": conversation_id, "status": ConversationStatus.ACTIVE},
            {"$set": {
                "status": ConversationStatus.ENDED,
                "ended_at": self.clock(),
                "ended_by": ended_by,
                "end_reason": reason,
            }},
            return_document=ReturnDocument.AFTER,
        )
        if conv is None:
            return None
        await self.db.participants.delete_many(
            {"_id": {"$in": conv["participants"]}, "conversation_id": conversation_id}
        )
        return EndedConversation(conversation_id, tuple(conv["participants"]), ended_by, reason)

    async def expire_idle_conversations(self) -> list[EndedConversation]:
        cutoff = self.clock() - timedelta(minutes=self.settings.conversation_idle_minutes)
        stale = await self.db.conversations.find(
            {"status": ConversationStatus.ACTIVE, "last_activity": {"$lt": cutoff}}, {"_id": 1}
        ).limit(200).to_list()
        ended: list[EndedConversation] = []
        for doc in stale:
            res = await self._end_by_id(doc["_id"], ended_by=None, reason="idle")
            if res:
                ended.append(res)
        return ended

    async def remove_from_everything(self, user_id: int) -> EndedConversation | None:
        """Used when banning: leave the queue and end any conversation."""
        await self.db.queue.delete_one({"_id": user_id})
        return await self.end_conversation(user_id, reason="banned")

    async def touch_conversation(self, conversation_id: ObjectId) -> None:
        await self.db.conversations.update_one(
            {"_id": conversation_id},
            {"$set": {"last_activity": self.clock()}, "$inc": {"message_count": 1}},
        )
