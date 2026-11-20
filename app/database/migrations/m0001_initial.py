from __future__ import annotations

from app.database.session import Database


async def upgrade(db: Database) -> None:
    await db.users.create_index("account_status")
    await db.users.create_index("last_active")
    await db.users.create_index([("membership_type", 1), ("premium_expiry", 1)])

    await db.subscriptions.create_index("expiry_date")
    await db.subscriptions.create_index("subscription_status")

    await db.payments.create_index("telegram_payment_charge_id", unique=True)
    await db.payments.create_index([("user_id", 1), ("created_at", -1)])

    await db.daily_usage.create_index("date")
    await db.daily_usage.create_index([("user_id", 1), ("date", 1)])

    await db.conversations.create_index([("participants", 1), ("status", 1), ("ended_at", -1)])
    await db.conversations.create_index([("status", 1), ("last_activity", 1)])
    await db.conversations.create_index("started_at")

    await db.participants.create_index("conversation_id")

    await db.messages_metadata.create_index(
        [("conversation_id", 1), ("recipient_id", 1), ("sender_id", 1), ("sent_at", -1)]
    )

    await db.reports.create_index([("status", 1), ("created_at", 1)])
    await db.reports.create_index("reported_id")
    # One report per reporter per conversation (anti-spam).
    await db.reports.create_index([("reporter_id", 1), ("conversation_id", 1)], unique=True)

    await db.blocked_users.create_index([("blocker_id", 1), ("blocked_id", 1)], unique=True)
    await db.blocked_users.create_index("blocked_id")

    # Matchmaking queue: lookups filter on pref + gender and sort by joined_at.
    await db.queue.create_index([("pref", 1), ("gender", 1), ("joined_at", 1)])
    await db.queue.create_index("joined_at")
