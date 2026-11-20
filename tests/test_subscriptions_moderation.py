import asyncio
from datetime import timedelta

from app.services.subscription_service import PaymentOutcomeStatus as P
from tests.conftest import register


def payload(uid, plan="monthly"):
    return f"premium:{plan}:{uid}"


async def pay(c, uid, charge="ch1", plan="monthly", amount=None, currency="XTR", pl=None):
    amount = amount if amount is not None else c.settings.plans[plan].stars
    return await c.subscriptions.process_successful_payment(uid, pl or payload(uid, plan), currency, amount, charge)


# ── payments / subscriptions ────────────────────────────────────────────
async def test_precheckout_validation(c):
    ok, _ = c.subscriptions.validate_precheckout(1, payload(1), "XTR", 250)
    assert ok
    assert not c.subscriptions.validate_precheckout(2, payload(1), "XTR", 250)[0]      # someone else's invoice
    assert not c.subscriptions.validate_precheckout(1, payload(1), "XTR", 1)[0]        # wrong price
    assert not c.subscriptions.validate_precheckout(1, payload(1), "USD", 250)[0]      # wrong currency
    assert not c.subscriptions.validate_precheckout(1, "garbage", "XTR", 250)[0]
    assert not c.subscriptions.validate_precheckout(1, "premium:lifetime:1", "XTR", 250)[0]


async def test_premium_activates_only_after_verified_payment(c, clock):
    await register(c, 1)
    assert await c.users.membership_of(1) == "free"        # nothing happens before payment
    out = await pay(c, 1)
    assert out.status == P.ACTIVATED
    assert out.expiry == clock() + timedelta(days=30)
    assert await c.users.membership_of(1) == "premium"
    sub = await c.subscriptions.get_subscription(1)
    assert sub["subscription_status"] == "active" and sub["plan_type"] == "monthly"
    assert sub["telegram_payment_charge_id"] == "ch1" and sub["start_date"] == clock()
    pay_doc = await c.subscriptions.find_payment("ch1")
    assert pay_doc["status"] == "activated" and pay_doc["stars"] == 250 and pay_doc["currency"] == "XTR"


async def test_invalid_payments_do_not_activate(c):
    await register(c, 1)
    assert (await pay(c, 1, amount=1)).status == P.INVALID
    assert (await pay(c, 1, currency="USD", charge="c2")).status == P.INVALID
    assert (await pay(c, 1, pl="premium:monthly:999", charge="c3")).status == P.INVALID
    assert await c.users.membership_of(1) == "free"
    assert await c.db.payments.count_documents({}) == 0


async def test_duplicate_payment_is_processed_once(c, clock):
    await register(c, 1)
    first = await pay(c, 1)
    again = await pay(c, 1)
    assert again.status == P.DUPLICATE
    assert (await c.users.get(1))["premium_expiry"] == first.expiry
    assert await c.db.payments.count_documents({}) == 1


async def test_concurrent_duplicate_payments_activate_exactly_once(c):
    await register(c, 1)
    results = await asyncio.gather(*[pay(c, 1, charge="same") for _ in range(6)])
    assert sum(1 for r in results if r.status == P.ACTIVATED) == 1
    assert (await c.users.get(1))["premium_expiry"] == next(r.expiry for r in results if r.expiry)


async def test_renewal_extends_and_yearly_plan(c, clock):
    await register(c, 1)
    await pay(c, 1, charge="a")
    out = await pay(c, 1, charge="b", plan="yearly")
    assert out.expiry == clock() + timedelta(days=30 + 365)


async def test_premium_expires_and_downgrades(c, clock):
    await register(c, 1)
    await pay(c, 1)
    clock.advance(days=29)
    assert await c.subscriptions.expire_due() == []
    clock.advance(days=2)
    assert await c.users.membership_of(1) == "free"          # lazy check is already correct
    assert await c.subscriptions.expire_due() == [1]          # sweeper persists the downgrade
    assert (await c.users.get(1))["membership_type"] == "free"
    assert (await c.subscriptions.get_subscription(1))["subscription_status"] == "expired"
    assert await c.subscriptions.expire_due() == []


async def test_expired_premium_loses_gender_search(c, clock):
    from app.services.matchmaking_service import SearchStatus
    await register(c, 1)
    await pay(c, 1)
    clock.advance(days=31)
    assert (await c.matchmaking.start_search(1, "female")).status == SearchStatus.GENDER_NOT_ALLOWED


async def test_admin_grant_and_revoke(c):
    await register(c, 1)
    await c.subscriptions.grant(1, 10)
    assert await c.users.membership_of(1) == "premium"
    assert await c.subscriptions.revoke(1)
    assert await c.users.membership_of(1) == "free"
    assert not await c.subscriptions.revoke(1)


async def test_refund_removes_time_and_is_idempotent(c):
    await register(c, 1)
    await pay(c, 1, charge="r1")
    assert await c.subscriptions.apply_refund("r1") == 1
    assert await c.users.membership_of(1) == "free"
    assert (await c.subscriptions.find_payment("r1"))["status"] == "refunded"
    assert await c.subscriptions.apply_refund("r1") is None
    assert await c.subscriptions.apply_refund("unknown") is None


async def test_refund_of_one_of_two_payments_keeps_the_other(c, clock):
    await register(c, 1)
    await pay(c, 1, charge="a")
    await pay(c, 1, charge="b")
    await c.subscriptions.apply_refund("a")
    assert await c.users.membership_of(1) == "premium"
    assert (await c.users.get(1))["premium_expiry"] == clock() + timedelta(days=30)


# ── blocking / reporting / bans ─────────────────────────────────────────
async def test_block_unblock(c):
    for u in (1, 2):
        await register(c, u)
    assert await c.moderation.block(1, 2) is True
    assert await c.moderation.block(1, 2) is False          # duplicate is harmless
    assert await c.moderation.block(1, 1) is False          # cannot block yourself
    assert await c.moderation.relations(1) == [2] and await c.moderation.relations(2) == [1]
    entry = (await c.moderation.list_blocked(1))[0]
    assert not await c.moderation.unblock(entry["_id"], 2)  # only the blocker can unblock
    assert await c.moderation.unblock(entry["_id"], 1)
    assert await c.moderation.relations(1) == []


async def test_report_is_saved_with_all_fields_and_evidence(c):
    for u in (1, 2):
        await register(c, u)
    await c.matchmaking.start_search(1)
    await c.matchmaking.start_search(2)
    cid = (await c.matchmaking.get_active(1))["conversation_id"]
    await c.messages.record(cid, 2, 1, "text", [501])
    await c.messages.record(cid, 1, 2, "text", [777])
    rid = await c.moderation.create_report(1, 2, cid, "harassment", "  rude  ")
    r = await c.moderation.get_report(rid)
    assert (r["reporter_id"], r["reported_id"], r["conversation_id"], r["category"]) == (1, 2, cid, "harassment")
    assert r["context"] == "rude" and r["status"] == "open" and r["created_at"]
    assert r["evidence_message_ids"] == [501]               # only what the reporter received
    assert await c.moderation.create_report(1, 2, cid, "spam") is None   # one report per conversation


async def test_report_resolution_and_counts(c):
    for u in (1, 2):
        await register(c, u)
    await c.matchmaking.start_search(1)
    await c.matchmaking.start_search(2)
    cid = (await c.matchmaking.get_active(1))["conversation_id"]
    rid = await c.moderation.create_report(1, 2, cid, "nonsense-category")
    assert (await c.moderation.get_report(rid))["category"] == "other"
    assert await c.moderation.report_counts(2) == (1, 0)
    assert (await c.moderation.next_open_report())["_id"] == rid
    assert await c.moderation.resolve_report(rid, "dismissed", 1000)
    assert not await c.moderation.resolve_report(rid, "dismissed", 1000)
    assert await c.moderation.next_open_report() is None


async def test_ban_and_unban(c):
    await register(c, 1)
    await c.moderation.ban(1, "abuse", 1000)
    assert await c.moderation.is_banned(1)
    assert (await c.users.get(1))["account_status"] == "banned"
    from app.services.matchmaking_service import SearchStatus
    assert (await c.matchmaking.start_search(1)).status == SearchStatus.BANNED
    assert await c.moderation.unban(1)
    assert (await c.users.get(1))["account_status"] == "active"


async def test_ban_removes_user_from_queue_and_chat(c):
    for u in (1, 2, 3):
        await register(c, u)
    await c.matchmaking.start_search(1)
    await c.matchmaking.start_search(2)
    await c.matchmaking.start_search(3)
    await c.moderation.ban(1, None, 1000)
    ended = await c.matchmaking.remove_from_everything(1)
    assert ended.partner_of(1) == 2
    assert await c.matchmaking.get_active(2) is None
    await c.moderation.ban(3, None, 1000)
    await c.matchmaking.remove_from_everything(3)
    assert not await c.matchmaking.in_queue(3)


async def test_stats_snapshot(c):
    for u in (1, 2, 3):
        await register(c, u)
    await c.subscriptions.grant(3, 5)
    await c.matchmaking.start_search(1)
    await c.matchmaking.start_search(2)
    await c.moderation.ban(3, None, 1000)
    snap = await c.stats.snapshot()
    assert snap["total_users"] == 3 and snap["registered_users"] == 3
    assert snap["premium_members"] == 1 and snap["free_members"] == 2
    assert snap["active_conversations"] == 1 and snap["matches_today"] == 1 and snap["searches_today"] == 2
    assert snap["banned_users"] == 1 and snap["available_now"] == 0
