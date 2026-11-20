"""Tiny migration runner for MongoDB (the Alembic equivalent for this project).

Each migration is an async function registered in MIGRATIONS with an increasing version.
Applied versions are recorded in the `schema_migrations` collection, so every migration
runs exactly once. Add new migrations by creating a module and appending it to the list.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from pymongo.errors import OperationFailure

from app.config import Settings
from app.database.migrations import m0001_initial, m0002_owner_tools
from app.database.session import Database

logger = logging.getLogger(__name__)

MIGRATIONS = [
    (1, "initial indexes", m0001_initial.upgrade),
    (2, "owner tools: staff, broadcasts, bot_settings", m0002_owner_tools.upgrade),
]


async def run_migrations(db: Database, settings: Settings) -> None:
    applied = {doc["_id"] async for doc in db.schema_migrations.find({}, {"_id": 1})}
    for version, name, fn in MIGRATIONS:
        if version in applied:
            continue
        logger.info("Applying migration %s: %s", version, name)
        await fn(db)
        await db.schema_migrations.insert_one(
            {"_id": version, "name": name, "applied_at": datetime.now(timezone.utc)}
        )
    await ensure_ttl_index(db, settings)


async def ensure_ttl_index(db: Database, settings: Settings) -> None:
    """The TTL depends on settings, so it is (re)applied on every start."""
    seconds = settings.metadata_ttl_days * 86400
    try:
        await db.messages_metadata.create_index("sent_at", expireAfterSeconds=seconds)
    except OperationFailure:
        # An index with the same key but another TTL exists: update it in place.
        await db.db.command(
            "collMod",
            "messages_metadata",
            index={"keyPattern": {"sent_at": 1}, "expireAfterSeconds": seconds},
        )
