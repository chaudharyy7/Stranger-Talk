"""Premium subscriptions paid with Telegram Stars (currency XTR).

Rules enforced here:
* Premium is only activated from a *verified* successful_payment update (never from a button click).
* The payload, currency and amount must match a configured plan and the paying user.
* Each telegram_payment_charge_id is processed exactly once (unique index + status check).
"""
from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from pymongo.errors import DuplicateKeyError

from app.config import Plan, Settings
from app.database.models import Membership, PaymentStatus, SubscriptionStatus
from app.database.session import Database
from app.utils.time import Clock

logger = logging.getLogger(__name__)

CURRENCY = "XTR"


class PaymentOutcomeStatus(enum.Enum):
    ACTIVATED = "activated"
    DUPLICATE = "duplicate"
    INVALID = "invalid"


@dataclass
class PaymentOutcome:
    status: PaymentOutcomeStatus
    expiry: datetime | None = None
    plan: Plan | None = None
    reason: str = ""


class SubscriptionService:
    def __init__(self, db: Database, settings: Settings, clock: Clock) -> None:
        self.db = db
        self.settings = settings
        self.clock = clock

    # ── payload helpers ──────────────────────────────────────────────────
    @staticmethod
    def make_payload(plan_id: str, user_id: int) -> str:
        return f"premium:{plan_id}:{user_id}"

    def parse_payload(self, payload: str, user_id: int) -> Plan | None:
        parts = (payload or "").split(":")
        if len(parts) != 3 or parts[0] != "premium" or parts[2] != str(user_id):
            return None
        return self.settings.plans.get(parts[1])

    def validate_precheckout(self, user_id: int, payload: str, currency: str, total: int) -> tuple[bool, str | None]:
        plan = self.parse_payload(payload, user_id)
        if plan is None:
            return False, "This invoice is not valid. Please open /premium and try again."
        if currency != CURRENCY or total != plan.stars:
            return False, "The price of this plan has changed. Please open /premium and try again."
        return True, None

    # ── activation ───────────────────────────────────────────────────────
    async def process_successful_payment(
        self,
        user_id: int,
        payload: str,
        currency: str,
        total: int,
        charge_id: str,
        provider_charge_id: str | None = None,
    ) -> PaymentOutcome:
        ok, error = self.validate_precheckout(user_id, payload, currency, total)
        if not ok:
            logger.error("Rejected invalid payment from %s (charge %s): %s", user_id, charge_id, error)
            return PaymentOutcome(PaymentOutcomeStatus.INVALID, reason=error or "invalid")
        plan = self.parse_payload(payload, user_id)
        assert plan is not None

        try:
            await self.db.payments.insert_one({
                "user_id": user_id,
                "plan_id": plan.id,
                "days": plan.days,
                "stars": total,
                "currency": currency,
                "payload": payload,
                "telegram_payment_charge_id": charge_id,
                "provider_payment_charge_id": provider_charge_id,
                "status": PaymentStatus.RECEIVED,
                "created_at": self.clock(),
            })
        except DuplicateKeyError:
            existing = await self.db.payments.find_one({"telegram_payment_charge_id": charge_id})
            if existing and existing["status"] != PaymentStatus.RECEIVED:
                return PaymentOutcome(PaymentOutcomeStatus.DUPLICATE, plan=plan)
            # status RECEIVED => an earlier attempt crashed before activation: finish the job.

        # Atomically flip RECEIVED -> ACTIVATED so only one worker extends the subscription.
        flipped = await self.db.payments.update_one(
            {"telegram_payment_charge_id": charge_id, "status": PaymentStatus.RECEIVED},
            {"$set": {"status": PaymentStatus.ACTIVATED, "activated_at": self.clock()}},
        )
        if flipped.modified_count == 0:
            return PaymentOutcome(PaymentOutcomeStatus.DUPLICATE, plan=plan)

        expiry = await self._extend(user_id, plan.days, plan.id, charge_id)
        return PaymentOutcome(PaymentOutcomeStatus.ACTIVATED, expiry=expiry, plan=plan)

    async def _extend(self, user_id: int, days: int, plan_type: str, charge_id: str | None) -> datetime:
        now = self.clock()
        user = await self.db.users.find_one({"_id": user_id}) or {}
        current = user.get("premium_expiry")
        still_premium = user.get("membership_type") == Membership.PREMIUM and current and current > now
        base = current if still_premium else now
        expiry = base + timedelta(days=days)
        await self.db.users.update_one(
            {"_id": user_id},
            {"$set": {"membership_type": Membership.PREMIUM, "premium_expiry": expiry, "updated_at": now}},
            upsert=True,
        )
        await self.db.subscriptions.update_one(
            {"_id": user_id},
            {
                "$set": {
                    "user_id": user_id,
                    "plan_type": plan_type,
                    "subscription_status": SubscriptionStatus.ACTIVE,
                    "expiry_date": expiry,
                    "telegram_payment_charge_id": charge_id,
                    "updated_at": now,
                },
                "$setOnInsert": {"start_date": now},
            },
            upsert=True,
        )
        if not still_premium:  # a fresh period starts now
            await self.db.subscriptions.update_one({"_id": user_id}, {"$set": {"start_date": now}})
        return expiry

    # ── admin actions ────────────────────────────────────────────────────
    async def grant(self, user_id: int, days: int) -> datetime:
        return await self._extend(user_id, days, "admin_grant", None)

    async def revoke(self, user_id: int, status: str = SubscriptionStatus.REVOKED) -> bool:
        now = self.clock()
        res = await self.db.users.update_one(
            {"_id": user_id, "membership_type": Membership.PREMIUM},
            {"$set": {"membership_type": Membership.FREE, "premium_expiry": None, "updated_at": now}},
        )
        await self.db.subscriptions.update_one(
            {"_id": user_id}, {"$set": {"subscription_status": status, "updated_at": now}}
        )
        return res.modified_count == 1

    async def apply_refund(self, charge_id: str) -> int | None:
        """Mark a payment refunded and take the purchased days back. Returns the user id,
        or None when the payment is unknown or was already refunded (idempotent)."""
        payment = await self.db.payments.find_one_and_update(
            {"telegram_payment_charge_id": charge_id, "status": {"$ne": PaymentStatus.REFUNDED}},
            {"$set": {"status": PaymentStatus.REFUNDED, "refunded_at": self.clock()}},
        )
        if payment is None:
            return None
        user_id = payment["user_id"]
        user = await self.db.users.find_one({"_id": user_id}) or {}
        expiry = user.get("premium_expiry")
        if payment["status"] == PaymentStatus.ACTIVATED and expiry:
            new_expiry = expiry - timedelta(days=payment["days"])
            if new_expiry <= self.clock():
                await self.revoke(user_id, SubscriptionStatus.REFUNDED)
            else:
                await self.db.users.update_one({"_id": user_id}, {"$set": {"premium_expiry": new_expiry}})
                await self.db.subscriptions.update_one({"_id": user_id}, {"$set": {"expiry_date": new_expiry}})
        return user_id

    async def find_payment(self, charge_id: str) -> dict[str, Any] | None:
        return await self.db.payments.find_one({"telegram_payment_charge_id": charge_id})

    # ── expiry sweeper ───────────────────────────────────────────────────
    async def expire_due(self) -> list[int]:
        """Downgrade every user whose Premium has run out. Returns the downgraded user ids."""
        now = self.clock()
        due = await self.db.users.find(
            {"membership_type": Membership.PREMIUM, "premium_expiry": {"$lte": now}}, {"_id": 1}
        ).to_list()
        expired: list[int] = []
        for doc in due:
            res = await self.db.users.update_one(
                {"_id": doc["_id"], "membership_type": Membership.PREMIUM, "premium_expiry": {"$lte": now}},
                {"$set": {"membership_type": Membership.FREE, "updated_at": now}},
            )
            if res.modified_count:
                await self.db.subscriptions.update_one(
                    {"_id": doc["_id"]}, {"$set": {"subscription_status": SubscriptionStatus.EXPIRED, "updated_at": now}}
                )
                expired.append(doc["_id"])
        return expired

    async def get_subscription(self, user_id: int) -> dict[str, Any] | None:
        return await self.db.subscriptions.find_one({"_id": user_id})
