"""Owner role: always-Premium, owner-only commands (broadcast, maintenance, staff, dm, botinfo)."""
import asyncio
import time
from datetime import timedelta

import pytest
import pytest_asyncio
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.methods import SendMessage

from app.config import ConfigError, load_settings
from app.services.matchmaking_service import SearchStatus
from tests.conftest import register
from tests.fakes import Harness

OWNER, ADMIN = 2000, 1000


@pytest_asyncio.fixture
async def h(c):
    return Harness(c)


def sent_to(h, uid, text):
    return [m for m in h.session.of("SendMessage") if m.chat_id == uid and m.text == text]


async def last_broadcast(h):
    return await h.c.db.broadcasts.find_one({}, sort=[("_id", -1)])


async def run_broadcast(h, command, expect_users=None):
    """Send the /broadcast command, press 'send', wait for completion. Returns the broadcast doc."""
    await h.send(OWNER, command)
    doc = await last_broadcast(h)
    assert doc, h.last_text(OWNER)
    await h.press(OWNER, f"bc:go:{doc['_id']}")
    await h.c.broadcasts.wait(doc["_id"])
    return await h.c.broadcasts.get(doc["_id"])


# ── owner always has Premium ─────────────────────────────────────────────
async def test_owner_has_every_premium_feature_without_paying(c):
    await register(c, OWNER)
    assert await c.users.membership_of(OWNER) == "premium"
    assert (await c.users.get(OWNER))["membership_type"] == "free"      # nothing was granted in the DB
    out = await c.matchmaking.start_search(OWNER, "female")            # gender search allowed
    assert out.status == SearchStatus.WAITING
    assert out.limit == c.settings.premium_daily_limit == 200


async def test_regular_admin_is_not_premium(c):
    await register(c, ADMIN)
    assert await c.users.membership_of(ADMIN) == "free"
    assert (await c.matchmaking.start_search(ADMIN, "female")).status == SearchStatus.GENDER_NOT_ALLOWED


async def test_owner_ui_shows_owner_premium_and_never_sells_plans(h):
    await register(h.c, OWNER)
    await h.send(OWNER, "/start")
    assert "PREMIUM (owner)" in h.last_text(OWNER) and "/200" in h.last_text(OWNER)
    await h.send(OWNER, "/premium")
    assert "Owner" in h.last_text(OWNER)
    markup = [m for m in h.session.of("SendMessage") if m.chat_id == OWNER][-1].reply_markup
    assert not any(b.callback_data.startswith("buy:") for row in markup.inline_keyboard for b in row)
    await h.press(OWNER, "buy:monthly")
    assert not h.session.of("SendInvoice")
    assert h.session.of("AnswerCallbackQuery")[-1].show_alert is True
    await h.press(OWNER, "menu:gender")
    assert "Who would you like" in h.last_text(OWNER)
    await h.press(OWNER, "menu:stats")
    assert "always on (owner)" in h.last_text(OWNER)


async def test_owner_expiry_sweeper_and_premium_audience(c, clock):
    await register(c, OWNER)
    clock.advance(days=400)
    assert await c.subscriptions.expire_due() == []
    assert await c.users.membership_of(OWNER) == "premium"


async def test_owner_is_also_admin_and_cannot_be_banned(h):
    assert await h.c.staff.is_admin(OWNER) and h.c.staff.is_owner(OWNER)
    await h.send(OWNER, "/stats")
    assert "System statistics" in h.last_text(OWNER)
    await h.send(ADMIN, f"/ban {OWNER}")
    assert "cannot be banned" in h.last_text(ADMIN)
    assert not await h.c.moderation.is_banned(OWNER)


# ── owner commands are owner-only ────────────────────────────────────────
@pytest.mark.parametrize("who", [11, ADMIN])
async def test_owner_commands_are_unavailable_to_everyone_else(h, who):
    await register(h.c, 11)
    for cmd in ("/owner", "/broadcast hi all", "/broadcasts", "/maintenance on", "/addadmin 5",
                "/removeadmin 5", "/admins", "/dm 11 hello", "/botinfo"):
        await h.send(who, cmd)
        assert "Unknown command" in h.last_text(who), cmd
    assert await h.c.db.broadcasts.count_documents({}) == 0
    assert not (await h.c.runtime.maintenance())[0]
    assert await h.c.db.staff.count_documents({}) == 0


async def test_broadcast_buttons_are_owner_only(h):
    await register(h.c, 11)
    bid = await h.c.broadcasts.create(OWNER, "all", 1, text="x")
    await h.press(11, f"bc:go:{bid}")
    await h.press(ADMIN, f"bc:go:{bid}")
    assert (await h.c.broadcasts.get(bid))["status"] == "pending"


async def test_owner_help(h):
    await h.send(OWNER, "/owner")
    assert "/broadcast" in h.last_text(OWNER) and "/maintenance" in h.last_text(OWNER)


# ── broadcast ────────────────────────────────────────────────────────────
async def test_broadcast_reaches_only_eligible_users(h):
    for u in (11, 22, 33, OWNER):
        await register(h.c, u)
    await h.c.users.get_or_create(44)                    # never finished registration
    await register(h.c, 55)
    await h.c.moderation.ban(55, None, ADMIN)            # banned
    await register(h.c, 66)
    await h.c.db.users.update_one({"_id": 66}, {"$set": {"bot_blocked": True}})  # blocked the bot

    await h.send(OWNER, "/broadcast <b>Hello</b> everyone")
    assert "exactly what will be sent to <b>4</b> users" in h.last_text(OWNER)
    assert sent_to(h, OWNER, "<b>Hello</b> everyone")           # owner got the preview
    assert not sent_to(h, 11, "<b>Hello</b> everyone")           # nothing is sent before confirming

    doc = await last_broadcast(h)
    await h.press(OWNER, f"bc:go:{doc['_id']}")
    await h.c.broadcasts.wait(doc["_id"])
    for u in (11, 22, 33):
        assert len(sent_to(h, u, "<b>Hello</b> everyone")) == 1
    for u in (44, 55, 66):
        assert not sent_to(h, u, "<b>Hello</b> everyone")
    done = await h.c.broadcasts.get(doc["_id"])
    assert (done["status"], done["sent"], done["failed"], done["blocked"]) == ("done", 4, 0, 0)
    assert "Broadcast done" in h.last_text(OWNER) and "Delivered: <b>4</b>" in h.last_text(OWNER)


async def test_broadcast_audience_filters(h, clock):
    for u in (11, 22, 33, OWNER):
        await register(h.c, u)
    await h.c.subscriptions.grant(22, 30)
    await h.c.db.users.update_one({"_id": 33}, {"$set": {"last_active": clock() - timedelta(days=30)}})
    b = h.c.broadcasts
    assert await b.count_audience("all") == 4
    assert await b.count_audience("premium") == 2          # paying user + the owner (always premium)
    assert await b.count_audience("free") == 2
    assert await b.count_audience("active") == 3

    done = await run_broadcast(h, "/broadcast --premium Premium news")
    assert done["audience"] == "premium" and done["sent"] == 2
    assert sent_to(h, 22, "Premium news") and not sent_to(h, 11, "Premium news")


async def test_double_confirm_sends_once_and_cancel_sends_nothing(h):
    for u in (11, OWNER):
        await register(h.c, u)
    await h.send(OWNER, "/broadcast once only")
    doc = await last_broadcast(h)
    await h.press(OWNER, f"bc:go:{doc['_id']}")
    await h.press(OWNER, f"bc:go:{doc['_id']}")           # double tap
    assert h.session.of("AnswerCallbackQuery")[-1].show_alert is True
    await h.c.broadcasts.wait(doc["_id"])
    assert len(sent_to(h, 11, "once only")) == 1

    await h.send(OWNER, "/broadcast never sent")
    doc2 = await last_broadcast(h)
    await h.press(OWNER, f"bc:no:{doc2['_id']}")
    await h.press(OWNER, f"bc:go:{doc2['_id']}")           # too late
    assert (await h.c.broadcasts.get(doc2["_id"]))["status"] == "cancelled"
    assert not sent_to(h, 11, "never sent")


async def test_broadcast_by_replying_copies_any_message(h):
    for u in (11, 22, OWNER):
        await register(h.c, u)
    reply = {"message_id": 777, "date": int(time.time()), "chat": {"id": OWNER, "type": "private"},
             "from": {"id": OWNER, "is_bot": False, "first_name": "o"},
             "photo": [{"file_id": "f", "file_unique_id": "u", "width": 1, "height": 1}], "caption": "Sale!"}
    await h.send(OWNER, "/broadcast --free", reply_to_message=reply)
    doc = await last_broadcast(h)
    assert doc["mode"] == "copy" and doc["source_message_id"] == 777 and doc["audience"] == "free"
    await h.press(OWNER, f"bc:go:{doc['_id']}")
    await h.c.broadcasts.wait(doc["_id"])
    for u in (11, 22):
        (copy,) = h.copies_to(u)
        assert copy.from_chat_id == OWNER and copy.message_id == 777
    assert not h.copies_to(OWNER) or all(m.chat_id == OWNER for m in h.copies_to(OWNER))


async def test_broadcast_usage_errors(h):
    await register(h.c, 11)
    await h.send(OWNER, "/broadcast")
    assert "Usage" in h.last_text(OWNER)
    await h.send(OWNER, "/broadcast --bogus hi")
    assert "Unknown audience" in h.last_text(OWNER)
    await h.send(OWNER, "/broadcast --premium hi")                  # only the owner... who is unregistered
    assert "nobody" in h.last_text(OWNER)
    await h.send(OWNER, "/broadcast " + "x" * 5000)
    assert "4096" in h.last_text(OWNER)
    reply = {"message_id": 1, "date": 1, "chat": {"id": OWNER, "type": "private"}, "text": "t"}
    await h.send(OWNER, "/broadcast text and reply", reply_to_message=reply)
    assert "not both" in h.last_text(OWNER)
    assert await h.c.db.broadcasts.count_documents({}) == 0


async def test_broken_html_is_caught_by_the_preview_before_anything_is_created(h):
    await register(h.c, 11)
    await register(h.c, OWNER)
    err = TelegramBadRequest(SendMessage(chat_id=OWNER, text="x"), "Bad Request: can't parse entities")
    h.session.errors_once[("SendMessage", OWNER)] = err
    await h.send(OWNER, "/broadcast <b>unclosed")
    assert "rejected" in h.last_text(OWNER)
    assert await h.c.db.broadcasts.count_documents({}) == 0


async def test_users_who_blocked_the_bot_are_flagged_and_skipped_later(h):
    for u in (11, 22, OWNER):
        await register(h.c, u)
    h.session.errors[("SendMessage", 22)] = TelegramForbiddenError(SendMessage(chat_id=22, text="x"), "Forbidden: bot was blocked by the user")
    done = await run_broadcast(h, "/broadcast first")
    assert (done["sent"], done["blocked"]) == (2, 1)
    assert (await h.c.users.get(22))["bot_blocked"] is True
    assert await h.c.broadcasts.count_audience("all") == 2          # 22 is skipped from now on
    del h.session.errors[("SendMessage", 22)]
    await h.send(22, "/help")                                       # they unblocked and wrote again
    assert (await h.c.users.get(22))["bot_blocked"] is False
    assert await h.c.broadcasts.count_audience("all") == 3


async def test_broadcast_survives_rate_limit_and_other_errors(h):
    for u in (11, 22, 33, OWNER):
        await register(h.c, u)
    h.session.errors_once[("SendMessage", 11)] = TelegramRetryAfter(SendMessage(chat_id=11, text="x"), "Flood", 0)
    h.session.errors[("SendMessage", 22)] = TelegramBadRequest(SendMessage(chat_id=22, text="x"), "Bad Request: chat not found")
    done = await run_broadcast(h, "/broadcast hello")
    assert done["status"] == "done" and done["failed"] == 1 and done["sent"] == 3
    assert sent_to(h, 11, "hello") and sent_to(h, 33, "hello")


async def test_running_broadcast_can_be_stopped(make_container):
    c = make_container(broadcast_rate_per_second=10.0)
    h = Harness(c)
    for u in range(100, 140):
        await register(c, u)
    await register(c, OWNER)
    await h.send(OWNER, "/broadcast slow one")
    doc = await last_broadcast(h)
    await h.press(OWNER, f"bc:go:{doc['_id']}")
    await asyncio.sleep(0.5)
    await h.press(OWNER, f"bc:stop:{doc['_id']}")
    await c.broadcasts.wait(doc["_id"])
    done = await c.broadcasts.get(doc["_id"])
    assert done["status"] == "cancelled" and 0 < done["sent"] < 41


async def test_broadcast_history_and_interrupted_recovery(h):
    await register(h.c, 11)
    await register(h.c, OWNER)
    await run_broadcast(h, "/broadcast history test")
    await h.send(OWNER, "/broadcasts")
    assert "Recent broadcasts" in h.last_text(OWNER) and "done" in h.last_text(OWNER)
    await h.c.db.broadcasts.insert_one({"status": "running", "created_at": h.c.clock()})
    assert await h.c.broadcasts.mark_interrupted() == 1


# ── maintenance mode ─────────────────────────────────────────────────────
async def test_maintenance_blocks_normal_users_but_not_staff(h):
    await register(h.c, 11)
    await register(h.c, OWNER)
    await h.send(OWNER, "/maintenance")
    assert "OFF" in h.last_text(OWNER)
    await h.send(OWNER, "/maintenance on Back at 10:00 UTC")
    assert "ON" in h.last_text(OWNER)

    await h.send(11, "/find")
    assert h.last_text(11) == "Back at 10:00 UTC" and not await h.c.matchmaking.in_queue(11)
    await h.press(11, "menu:find")
    assert h.session.of("AnswerCallbackQuery")[-1].show_alert is True
    await h.raw(pre_checkout_query={"id": "p", "from": {"id": 11, "is_bot": False, "first_name": "x"},
                                    "currency": "XTR", "total_amount": 250, "invoice_payload": "premium:monthly:11"})
    assert h.session.of("AnswerPreCheckoutQuery")[-1].ok is False

    await h.send(OWNER, "/find")                                    # owner and admins are unaffected
    assert await h.c.matchmaking.in_queue(OWNER)
    await h.send(ADMIN, "/stats")
    assert "System statistics" in h.last_text(ADMIN)

    await h.send(OWNER, "/maintenance off")
    await h.send(11, "/find")
    assert await h.c.matchmaking.in_queue(11) or await h.c.matchmaking.get_active(11)


async def test_maintenance_setting_persists_across_restart(h, db, settings, clock):
    from app.container import build_container
    await h.c.runtime.set_maintenance(True, "later")
    fresh = build_container(settings, db, clock)
    assert await fresh.runtime.maintenance() == (True, "later")


# ── staff management ─────────────────────────────────────────────────────
async def test_owner_adds_and_removes_admins_at_runtime(h):
    await h.send(77, "/stats")
    assert "Unknown command" in h.last_text(77)
    await h.send(OWNER, "/addadmin 77")
    assert "now an administrator" in h.last_text(OWNER) and "administrator" in h.last_text(77)
    await h.send(77, "/stats")
    assert "System statistics" in h.last_text(77)
    assert 77 in await h.c.staff.all_ids()
    await h.send(OWNER, "/addadmin 77")
    assert "already" in h.last_text(OWNER)
    await h.send(OWNER, "/admins")
    assert "<code>77</code>" in h.last_text(OWNER) and f"<code>{OWNER}</code>" in h.last_text(OWNER)

    await h.send(77, "/addadmin 88")                                # admins can't create admins
    assert "Unknown command" in h.last_text(77)

    await h.send(OWNER, "/removeadmin 77")
    await h.send(77, "/stats")
    assert "Unknown command" in h.last_text(77)
    await h.send(OWNER, f"/removeadmin {ADMIN}")
    assert "environment" in h.last_text(OWNER) and await h.c.staff.is_admin(ADMIN)
    await h.send(OWNER, "/removeadmin 12345")
    assert "not an administrator" in h.last_text(OWNER)
    await h.send(OWNER, "/addadmin abc")
    assert "Usage" in h.last_text(OWNER)


async def test_runtime_admins_receive_staff_notifications(h):
    await h.c.staff.add_admin(77, OWNER)
    ids = await h.c.staff.all_ids()
    assert {ADMIN, OWNER, 77} <= set(ids)


# ── dm / botinfo ─────────────────────────────────────────────────────────
async def test_dm_and_botinfo(h):
    await register(h.c, 11)
    await h.send(OWNER, "/dm 11 Hello there")
    assert "Hello there" in h.last_text(11) and "Sent" in h.last_text(OWNER)
    await h.send(OWNER, "/dm 11")
    assert "Usage" in h.last_text(OWNER)
    h.session.errors[("SendMessage", 99)] = TelegramForbiddenError(SendMessage(chat_id=99, text="x"), "Forbidden")
    await h.send(OWNER, "/dm 99 hi")
    assert "Could not deliver" in h.last_text(OWNER)
    await h.send(OWNER, "/botinfo")
    text = h.last_text(OWNER)
    assert "Bot info" in text and "Database: <b>OK" in text and "Maintenance" in text


# ── configuration ────────────────────────────────────────────────────────
BASE = {"BOT_TOKEN": "123456789:" + "A" * 35, "MIN_AGE": "18"}


def test_owner_ids_config():
    s = load_settings({**BASE, "OWNER_IDS": "5, 6", "ADMIN_IDS": "7"})
    assert s.owner_ids == frozenset({5, 6}) and s.staff_ids == frozenset({5, 6, 7})
    assert s.is_owner(5) and not s.is_owner(7)
    with pytest.raises(ConfigError):
        load_settings({**BASE, "OWNER_IDS": "me"})


def test_production_needs_an_owner_or_admin():
    prod = {**BASE, "ENVIRONMENT": "production", "MIN_AGE_POLICY_CONFIRMED": "true"}
    with pytest.raises(ConfigError, match="OWNER_IDS"):
        load_settings(prod)
    assert load_settings({**prod, "OWNER_IDS": "5"}).is_production


def test_broadcast_rate_is_bounded():
    with pytest.raises(ConfigError):
        load_settings({**BASE, "BROADCAST_RATE_PER_SECOND": "500"})
