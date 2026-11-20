from __future__ import annotations

import os
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from app.config import Settings
from app.container import build_container
from app.database.migrations import run_migrations
from app.database.session import Database

MONGO_URI = os.getenv("TEST_MONGODB_URI", "mongodb://127.0.0.1:27017")


class FakeClock:
    """Controllable UTC clock (used for midnight-reset and expiry tests)."""

    def __init__(self) -> None:
        self.now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        bot_token="123456789:" + "A" * 35,
        min_age=18,
        admin_ids=frozenset({1000}),
        owner_ids=frozenset({2000}),
        broadcast_rate_per_second=1000.0,
        media_group_delay=0.05,
        rate_limit_events=1000,
    )


@pytest_asyncio.fixture
async def db(settings):
    database = Database(MONGO_URI, "test_" + uuid.uuid4().hex[:10])
    if not await database.ping():
        pytest.skip("MongoDB is not reachable (set TEST_MONGODB_URI)")
    await run_migrations(database, settings)
    yield database
    await database.drop()
    await database.close()


@pytest_asyncio.fixture
async def c(db, settings, clock):
    return build_container(settings, db, clock)


@pytest.fixture
def make_container(db, settings, clock):
    def _make(**overrides):
        return build_container(replace(settings, **overrides), db, clock)
    return _make


async def register(c, uid: int, gender: str = "male", name: str | None = None, country: str = "IN") -> None:
    await c.users.get_or_create(uid)
    await c.users.complete_registration(uid, name or f"User{uid}", 25, country, gender)
