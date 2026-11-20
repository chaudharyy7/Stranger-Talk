import asyncio

from app.container import build_container
from app.services.matchmaking_service import MatchmakingService, SearchStatus
from tests.conftest import register

S = SearchStatus


async def grant(c, uid):
    await c.subscriptions.grant(uid, 30)


async def test_two_users_are_matched(c):
    await register(c, 1)
    await register(c, 2)
    assert (await c.matchmaking.start_search(1)).status == S.WAITING
    out = await c.matchmaking.start_search(2)
    assert out.status == S.MATCHED
    a, b = await c.matchmaking.get_active(1), await c.matchmaking.get_active(2)
    assert a["partner_id"] == 2 and b["partner_id"] == 1
    assert a["conversation_id"] == b["conversation_id"]
    assert not await c.matchmaking.in_queue(1) and not await c.matchmaking.in_queue(2)


async def test_never_matched_with_self(c):
    await register(c, 1)
    assert (await c.matchmaking.start_search(1)).status == S.WAITING
    assert await c.matchmaking.try_match(1) is None
    assert await c.matchmaking.get_active(1) is None


async def test_duplicate_search_and_active_chat_rejected(c):
    for u in (1, 2):
        await register(c, u)
    await c.matchmaking.start_search(1)
    assert (await c.matchmaking.start_search(1)).status == S.ALREADY_SEARCHING
    await c.matchmaking.start_search(2)
    assert (await c.matchmaking.start_search(1)).status == S.IN_CHAT
    assert (await c.matchmaking.start_search(2)).status == S.IN_CHAT


async def test_unregistered_cannot_search(c):
    await c.users.get_or_create(1)
    assert (await c.matchmaking.start_search(1)).status == S.NOT_REGISTERED
    assert (await c.matchmaking.start_search(999)).status == S.NOT_REGISTERED


async def test_blocked_users_never_match_either_direction(c):
    for u in (1, 2, 3, 4):
        await register(c, u)
    await c.moderation.block(1, 2)      # 1 blocked 2
    await c.moderation.block(4, 3)      # 4 blocked 3
    assert (await c.matchmaking.start_search(1)).status == S.WAITING
    assert (await c.matchmaking.start_search(2)).status == S.WAITING   # 2 must not get 1
    assert (await c.matchmaking.start_search(3)).status == S.MATCHED   # 3 can match 1 or 2
    assert (await c.matchmaking.start_search(4)).status in (S.MATCHED, S.WAITING)
    partner_of_4 = await c.matchmaking.get_active(4)
    assert partner_of_4 is None or partner_of_4["partner_id"] != 3
    one = await c.matchmaking.get_active(1)
    assert one is None or one["partner_id"] != 2


async def test_free_user_cannot_use_gender_search_and_it_is_not_counted(c):
    await register(c, 1)
    out = await c.matchmaking.start_search(1, "female")
    assert out.status == S.GENDER_NOT_ALLOWED
    assert await c.usage.used_today(1) == 0
    assert not await c.matchmaking.in_queue(1)


async def test_premium_gender_search_picks_requested_gender(c):
    await register(c, 1, "male")
    await register(c, 2, "male")
    await register(c, 3, "female")
    await grant(c, 1)
    await grant(c, 2)
    assert (await c.matchmaking.start_search(2, "male")).status == S.WAITING    # male wants male
    out = await c.matchmaking.start_search(1, "female")                         # male wants female
    assert out.status == S.WAITING                                              # 2 is male -> no match
    assert (await c.matchmaking.start_search(3)).status == S.MATCHED            # female, any gender
    assert (await c.matchmaking.get_active(3))["partner_id"] == 1               # only 1 wants a female
    assert await c.matchmaking.in_queue(2)


async def test_gender_filter_respects_BOTH_users_preferences(c):
    await register(c, 1, "male")
    await register(c, 2, "female")
    await grant(c, 1)
    await grant(c, 2)
    await c.matchmaking.start_search(2, "female")        # 2 (female) only wants females
    out = await c.matchmaking.start_search(1, "female")  # 1 is male -> must NOT be matched with 2
    assert out.status == S.WAITING
    assert await c.matchmaking.get_active(1) is None and await c.matchmaking.get_active(2) is None


async def test_mutual_gender_preferences_match(c):
    await register(c, 1, "male")
    await register(c, 2, "female")
    await grant(c, 1)
    await grant(c, 2)
    await c.matchmaking.start_search(2, "male")
    assert (await c.matchmaking.start_search(1, "female")).status == S.MATCHED


async def test_prefer_not_to_say_only_matches_any_searches(c):
    await register(c, 1, "prefer_not_to_say")
    await register(c, 2, "female")
    await grant(c, 2)
    await c.matchmaking.start_search(2, "other")
    assert (await c.matchmaking.start_search(1)).status == S.WAITING
    await c.matchmaking.cancel_search(2)
    await register(c, 3, "male")
    assert (await c.matchmaking.start_search(3)).status == S.MATCHED


async def test_rejected_requests_do_not_use_up_searches(c):
    await register(c, 1)
    await c.matchmaking.start_search(1)
    before = await c.usage.used_today(1)
    await c.matchmaking.start_search(1)            # already searching
    await c.matchmaking.start_search(1, "male")    # gender not allowed (checked after, but rejected earlier)
    assert await c.usage.used_today(1) == before == 1


async def test_daily_limit_blocks_search_and_next_counts(make_container, db):
    c = make_container(free_daily_limit=3)
    for u in (1, 2):
        await register(c, u)
    assert (await c.matchmaking.start_search(1)).status == S.WAITING   # search 1
    assert (await c.matchmaking.start_search(2)).status == S.MATCHED   # search 1 for user 2
    await c.matchmaking.end_conversation(1, "next")
    assert (await c.matchmaking.start_search(1)).status == S.WAITING   # "Next" = search 2
    await c.matchmaking.cancel_search(1)
    assert (await c.matchmaking.start_search(1)).status == S.WAITING   # search 3
    await c.matchmaking.cancel_search(1)
    out = await c.matchmaking.start_search(1)
    assert out.status == S.LIMIT_REACHED and (out.used, out.limit) == (3, 3)


async def test_premium_gets_higher_limit(make_container):
    c = make_container(free_daily_limit=1, premium_daily_limit=2)
    await register(c, 1)
    await grant(c, 1)
    assert (await c.matchmaking.start_search(1)).status == S.WAITING
    await c.matchmaking.cancel_search(1)
    assert (await c.matchmaking.start_search(1)).status == S.WAITING
    await c.matchmaking.cancel_search(1)
    assert (await c.matchmaking.start_search(1)).status == S.LIMIT_REACHED


async def test_stop_ends_for_both_and_is_idempotent(c):
    for u in (1, 2):
        await register(c, u)
    await c.matchmaking.start_search(1)
    await c.matchmaking.start_search(2)
    ended = await c.matchmaking.end_conversation(1)
    assert ended and ended.partner_of(1) == 2
    assert await c.matchmaking.get_active(1) is None and await c.matchmaking.get_active(2) is None
    assert await c.matchmaking.end_conversation(2) is None
    assert (await c.matchmaking.last_ended(1))["_id"] == ended.conversation_id


async def test_simultaneous_stop_has_single_winner(c):
    for u in (1, 2):
        await register(c, u)
    await c.matchmaking.start_search(1)
    await c.matchmaking.start_search(2)
    results = await asyncio.gather(c.matchmaking.end_conversation(1), c.matchmaking.end_conversation(2))
    assert sum(1 for r in results if r) == 1


async def test_conversation_survives_bot_restart(db, settings, clock):
    c1 = build_container(settings, db, clock)
    for u in (1, 2):
        await register(c1, u)
    await c1.matchmaking.start_search(1)
    await c1.matchmaking.start_search(2)
    c2 = build_container(settings, db, clock)  # new process, same database
    assert (await c2.matchmaking.get_active(1))["partner_id"] == 2


async def test_queue_timeout_and_idle_cleanup(c, clock):
    for u in (1, 2, 3):
        await register(c, u)
    await c.matchmaking.start_search(1)
    clock.advance(seconds=c.settings.queue_timeout_seconds + 1)
    assert await c.matchmaking.expire_queue() == [1]
    assert not await c.matchmaking.in_queue(1)

    await c.matchmaking.start_search(2)
    await c.matchmaking.start_search(3)
    clock.advance(minutes=c.settings.conversation_idle_minutes - 1)
    assert await c.matchmaking.expire_idle_conversations() == []
    clock.advance(minutes=2)
    ended = await c.matchmaking.expire_idle_conversations()
    assert len(ended) == 1 and set(ended[0].participants) == {2, 3}
    assert await c.matchmaking.get_active(2) is None


async def test_database_refuses_second_active_conversation(c):
    for u in (1, 2, 3):
        await register(c, u)
    await c.matchmaking.start_search(1)
    await c.matchmaking.start_search(2)           # 1 and 2 are chatting
    me = {"_id": 3, "gender": "male", "pref": "any", "exclude": []}
    partner = {"_id": 2, "gender": "male", "pref": "any", "exclude": []}
    assert await c.matchmaking._create_conversation(me, partner) is None   # 2 is already busy
    assert (await c.matchmaking.get_active(2))["partner_id"] == 1
    assert await c.matchmaking.get_active(3) is None


async def test_concurrent_matchmaking_never_double_books(c, db, settings, clock):
    """40 users search at the same moment through TWO independent matchmaker instances."""
    n = 40
    for u in range(1, n + 1):
        await register(c, u)
    other = MatchmakingService(db, settings, c.users, c.usage, c.moderation, c.stats, clock)
    services = [c.matchmaking, other]
    await asyncio.gather(*[services[u % 2].start_search(u) for u in range(1, n + 1)])
    for _ in range(5):  # the sweeper is the safety net for rare mutual-claim collisions
        if not await c.db.queue.count_documents({}):
            break
        await c.matchmaking.pair_waiting()

    parts = await c.db.participants.find({}).to_list()
    assert len(parts) == n and len({p["_id"] for p in parts}) == n
    by_id = {p["_id"]: p for p in parts}
    for p in parts:  # partner symmetry and no self-matches
        assert p["partner_id"] != p["_id"]
        assert by_id[p["partner_id"]]["partner_id"] == p["_id"]
        assert by_id[p["partner_id"]]["conversation_id"] == p["conversation_id"]
    assert await c.db.conversations.count_documents({"status": "active"}) == n // 2
    assert await c.db.queue.count_documents({}) == 0


async def test_stats_track_searches_matches_failures(c, clock):
    for u in (1, 2, 3):
        await register(c, u)
    await c.matchmaking.start_search(1)
    await c.matchmaking.start_search(2)
    await c.matchmaking.start_search(3)
    clock.advance(seconds=c.settings.queue_timeout_seconds + 1)
    await c.matchmaking.expire_queue()
    assert await c.stats.today() == {"searches": 3, "matches": 1, "failed": 1}
