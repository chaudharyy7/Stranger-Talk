from __future__ import annotations

from app.database.session import Database


async def upgrade(db: Database) -> None:
    await db.broadcasts.create_index([("created_at", -1)])
    await db.broadcasts.create_index("status")
    # Broadcast audiences filter on these user fields.
    await db.users.create_index([("account_status", 1), ("registration_date", 1), ("bot_blocked", 1)])
