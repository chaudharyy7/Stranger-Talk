import asyncio

from tests.conftest import register


async def test_new_user_created_once(c):
    user, created = await c.users.get_or_create(42)
    assert created and user["membership_type"] == "free" and user["account_status"] == "active"
    again, created2 = await c.users.get_or_create(42)
    assert not created2 and again["_id"] == 42
    assert await c.db.users.count_documents({}) == 1


async def test_concurrent_start_creates_single_user(c):
    results = await asyncio.gather(*[c.users.get_or_create(7) for _ in range(20)])
    assert sum(1 for _, created in results if created) == 1
    assert await c.db.users.count_documents({}) == 1


async def test_registration_completion(c):
    await c.users.get_or_create(5)
    assert not await c.users.is_registered(5)
    await c.users.complete_registration(5, "Alice", 30, "IN", "female")
    assert await c.users.is_registered(5)
    profile = await c.users.get_profile(5)
    assert (profile["display_name"], profile["age"], profile["country"], profile["gender"]) == ("Alice", 30, "IN", "female")
    await c.users.update_profile_field(5, "name", "Alicia")
    assert (await c.users.get_profile(5))["display_name"] == "Alicia"


async def test_free_limit_is_100_and_premium_200(c):
    assert c.settings.daily_limit("free") == 100
    assert c.settings.daily_limit("premium") == 200
    for _ in range(100):
        ok, _ = await c.usage.try_consume(1, 100)
        assert ok
    ok, used = await c.usage.try_consume(1, 100)
    assert not ok and used == 100
    ok, used = await c.usage.try_consume(1, 200)  # premium limit allows more
    assert ok and used == 101


async def test_limit_resets_at_midnight_utc(c, clock):
    from datetime import datetime, timezone
    clock.now = datetime(2026, 3, 1, 23, 59, 50, tzinfo=timezone.utc)
    assert (await c.usage.try_consume(1, 1))[0]
    assert not (await c.usage.try_consume(1, 1))[0]
    clock.advance(seconds=20)  # now 00:00:10 on the next UTC day
    assert await c.usage.used_today(1) == 0
    assert (await c.usage.try_consume(1, 1))[0]


async def test_concurrent_requests_cannot_exceed_limit(c):
    results = await asyncio.gather(*[c.usage.try_consume(9, 100) for _ in range(150)])
    assert sum(1 for ok, _ in results if ok) == 100
    assert await c.usage.used_today(9) == 100


async def test_limit_survives_restart(db, settings, clock):
    from app.container import build_container
    first = build_container(settings, db, clock)
    await first.usage.try_consume(3, 100)
    second = build_container(settings, db, clock)  # "restarted bot"
    assert await second.usage.used_today(3) == 1


async def test_refund_never_goes_negative(c):
    await c.usage.refund(1)
    assert await c.usage.used_today(1) == 0
    await c.usage.try_consume(1, 5)
    await c.usage.refund(1)
    assert await c.usage.used_today(1) == 0
