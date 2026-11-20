"""End-to-end tests: raw Telegram updates -> real Dispatcher/handlers/services/MongoDB -> fake Bot API."""
import asyncio
from dataclasses import replace

import pytest
import pytest_asyncio
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import CopyMessage

from app.container import build_container
from tests.conftest import register
from tests.fakes import Harness

A, B, ADMIN = 11, 22, 1000


@pytest_asyncio.fixture
async def h(c):
    return Harness(c)


async def two_chatting(h):
    await register(h.c, A, "male")
    await register(h.c, B, "female")
    await h.connect(A, B)
    assert (await h.c.matchmaking.get_active(A))["partner_id"] == B


# ── registration ─────────────────────────────────────────────────────────
async def test_new_user_registration_flow(h):
    await h.send(A, "/start")
    assert "Welcome" in h.last_text(A) and "18" in h.last_text(A)
    assert not await h.c.users.is_registered(A)

    await h.press(A, "reg:agree")
    assert "call" in h.last_text(A).lower()
    await h.send(A, "@evil")                       # invalid name
    assert "⚠️" in h.last_text(A)
    await h.send(A, "Alice")
    await h.send(A, "17")                          # below configured minimum age
    assert "at least 18" in h.last_text(A)
    await h.send(A, "abc")
    assert "⚠️" in h.last_text(A)
    await h.send(A, "25")
    assert "gender" in h.last_text(A).lower()
    assert "country" not in h.last_text(A).lower()
    await h.press(A, "reg:gender:female")
    assert "confirm your profile" in h.last_text(A)
    assert "country" not in h.last_text(A).lower()
    assert not await h.c.users.is_registered(A)    # nothing saved before confirmation
    await h.press(A, "reg:confirm")

    profile = await h.c.users.get_profile(A)
    assert (profile["display_name"], profile["age"], profile["country"], profile["gender"]) == ("Alice", 25, None, "female")
    assert await h.c.users.is_registered(A)
    assert any("GLOBAL ANONYMOUS CHAT" in t and "Daily Searches: <b>0/100</b>" in t for t in h.texts_to(A))


async def test_edit_during_confirmation_screen(h):
    await h.send(A, "/start")
    await h.press(A, "reg:agree")
    await h.send(A, "Alice")
    await h.send(A, "25")
    await h.press(A, "reg:gender:male")
    await h.press(A, "reg:edit:name")
    await h.send(A, "Alicia")
    assert "Alicia" in h.last_text(A) and "confirm" in h.last_text(A).lower()
    await h.press(A, "reg:confirm")
    assert (await h.c.users.get_profile(A))["display_name"] == "Alicia"


async def test_existing_user_is_not_asked_to_register_again(h):
    await register(h.c, A)
    await h.send(A, "/start")
    text = h.last_text(A)
    assert "GLOBAL ANONYMOUS CHAT" in text and "Membership: <b>FREE</b>" in text
    assert "Welcome to Global" not in text


async def test_start_during_active_chat_keeps_conversation(h):
    await two_chatting(h)
    await h.send(A, "/start")
    assert "still connected" in h.last_text(A)
    assert await h.c.matchmaking.get_active(A)


async def test_profile_edit_for_existing_user(h):
    await register(h.c, A)
    await h.press(A, "pf:edit:name")
    await h.send(A, "Zed")
    assert (await h.c.users.get_profile(A))["display_name"] == "Zed"
    await h.press(A, "pf:edit:age")
    await h.send(A, "12")
    assert (await h.c.users.get_profile(A))["age"] == 25      # underage edit rejected


async def test_unregistered_user_cannot_search(h):
    await h.send(A, "/find")
    assert "/start" in h.last_text(A)


# ── matchmaking commands / buttons ───────────────────────────────────────
async def test_find_match_notifies_both_without_profile_details(h):
    await two_chatting(h)
    for uid in (A, B):
        assert "Partner found" in h.texts_to(uid)[-1]
        text = h.last_text(uid)
        assert all(label not in text for label in ("Name:", "Age:", "Gender:", "Country:"))
        assert "User11" not in text and "User22" not in text
        assert "@" not in text
    assert str(A) not in h.last_text(B) and str(B) not in h.last_text(A)


async def test_stop_and_next_buttons_have_backends(h):
    await two_chatting(h)
    await h.press(A, "chat:next")
    assert any("left the conversation" in t for t in h.texts_to(B))
    assert "Searching" in h.last_text(A)
    assert await h.c.matchmaking.in_queue(A) and await h.c.matchmaking.get_active(B) is None
    await h.send(A, "/stop")
    assert "cancelled" in h.last_text(A)
    await h.send(A, "/stop")
    assert "not in a conversation" in h.last_text(A)


async def test_free_user_gender_search_shows_upgrade(h):
    await register(h.c, A)
    await h.press(A, "menu:gender")
    assert "Premium" in h.last_text(A)
    await h.press(A, "gs:female")
    assert "Premium feature" in h.last_text(A)


async def test_premium_user_gender_menu_and_search(h):
    await register(h.c, A)
    await h.c.subscriptions.grant(A, 30)
    await h.press(A, "menu:gender")
    assert "Who would you like" in h.last_text(A)
    await h.press(A, "gs:female")
    assert "Searching" in h.last_text(A)


async def test_limit_reached_message_offers_premium(make_container):
    c = make_container(free_daily_limit=1)
    h = Harness(c)
    await register(c, A)
    await h.send(A, "/find")
    await h.send(A, "/stop")
    await h.send(A, "/find")
    assert "limit reached" in h.last_text(A) and "00:00 UTC" in h.last_text(A)


# ── message relay ────────────────────────────────────────────────────────
async def test_text_is_copied_not_forwarded(h):
    await two_chatting(h)
    await h.send(A, "hello stranger")
    copies = h.copies_to(B)
    assert len(copies) == 1 and isinstance(copies[0], CopyMessage)
    assert copies[0].from_chat_id == A
    assert not h.session.of("ForwardMessage")


@pytest.mark.parametrize("field,payload", [
    ("photo", [{"file_id": "f", "file_unique_id": "u", "width": 1, "height": 1}]),
    ("video", {"file_id": "f", "file_unique_id": "u", "width": 1, "height": 1, "duration": 1}),
    ("voice", {"file_id": "f", "file_unique_id": "u", "duration": 1}),
    ("audio", {"file_id": "f", "file_unique_id": "u", "duration": 1}),
    ("document", {"file_id": "f", "file_unique_id": "u"}),
    ("sticker", {"file_id": "f", "file_unique_id": "u", "type": "regular", "width": 1, "height": 1, "is_animated": False, "is_video": False}),
    ("animation", {"file_id": "f", "file_unique_id": "u", "width": 1, "height": 1, "duration": 1}),
    ("video_note", {"file_id": "f", "file_unique_id": "u", "length": 1, "duration": 1}),
])
async def test_supported_media_types_are_relayed(h, field, payload):
    await two_chatting(h)
    await h.send(A, None, **{field: payload})
    assert len(h.copies_to(B)) == 1


async def test_media_group_is_relayed_as_one_album(h):
    await two_chatting(h)
    photo = [{"file_id": "f", "file_unique_id": "u", "width": 1, "height": 1}]
    await h.send(A, None, photo=photo, media_group_id="g1")
    await h.send(A, None, photo=photo, media_group_id="g1")
    await h.send(A, None, photo=photo, media_group_id="g1")
    await asyncio.sleep(0.3)
    copies = h.copies_to(B)
    assert len(copies) == 1 and len(copies[0].message_ids) == 3     # one copyMessages call keeps the album


async def test_unsupported_type_gets_helpful_error(h):
    await two_chatting(h)
    await h.send(A, None, dice={"emoji": "🎲", "value": 3})
    assert "can't be sent" in h.last_text(A) and "Supported" in h.last_text(A)
    assert not h.copies_to(B)


async def test_contact_and_location_need_confirmation(h):
    await two_chatting(h)
    await h.send(A, None, contact={"phone_number": "123456", "first_name": "A"})
    assert "Privacy check" in h.last_text(A) and not h.copies_to(B)
    (key,) = h.c.messages.pending_private.keys()
    await h.press(A, f"priv:no:{key[1]}")
    assert not h.copies_to(B)
    await h.send(A, None, location={"latitude": 1.0, "longitude": 2.0})
    (key,) = h.c.messages.pending_private.keys()
    await h.press(A, f"priv:ok:{key[1]}")
    assert len(h.copies_to(B)) == 1


async def test_commands_are_never_relayed_to_partner(h):
    await two_chatting(h)
    await h.send(A, "/definitelynotacommand")
    assert "Unknown command" in h.last_text(A) and not h.copies_to(B)


async def test_message_without_partner_gets_hint(h):
    await register(h.c, A)
    await h.send(A, "anyone there?")
    assert "not connected" in h.last_text(A)


async def test_relay_stores_metadata_never_content(h):
    await two_chatting(h)
    await h.send(A, "my secret text")
    docs = await h.c.db.messages_metadata.find({}).to_list()
    assert len(docs) == 1 and docs[0]["content_type"] == "text" and docs[0]["sender_id"] == A
    assert "text" not in docs[0] and "my secret text" not in str(docs[0])
    assert (await h.c.users.get(A))["stats"]["messages"] == 1


async def test_partner_blocking_the_bot_closes_the_conversation(h):
    await two_chatting(h)
    method = CopyMessage(chat_id=B, from_chat_id=A, message_id=1)
    h.session.errors[("CopyMessage", B)] = TelegramForbiddenError(method, "Forbidden: bot was blocked by the user")
    await h.send(A, "hi")
    assert await h.c.matchmaking.get_active(A) is None
    assert "no longer be reached" in h.last_text(A)


async def test_delivery_failure_keeps_conversation(h):
    from aiogram.exceptions import TelegramBadRequest
    await two_chatting(h)
    method = CopyMessage(chat_id=B, from_chat_id=A, message_id=1)
    h.session.errors[("CopyMessage", B)] = TelegramBadRequest(method, "Bad Request: file is too big")
    await h.send(A, "x")
    assert "could not deliver" in h.last_text(A) and await h.c.matchmaking.get_active(A)


async def test_users_do_not_receive_other_pairs_messages(h):
    await register(h.c, 33)
    await register(h.c, 44)
    await two_chatting(h)
    await h.connect(33, 44)
    await h.send(A, "private to B")
    assert len(h.copies_to(B)) == 1 and not h.copies_to(33) and not h.copies_to(44)


# ── reporting & blocking ─────────────────────────────────────────────────
async def test_report_flow_hides_reporter_and_notifies_admins(h):
    await two_chatting(h)
    await h.send(A, "bad words")
    await h.press(A, "chat:report")
    await h.press(A, "repcat:harassment")
    await h.send(A, "he was rude")
    reports = await h.c.db.reports.find({}).to_list()
    assert len(reports) == 1
    r = reports[0]
    assert (r["reporter_id"], r["reported_id"], r["category"], r["context"]) == (A, B, "harassment", "he was rude")
    assert "Report submitted" in h.last_text(A)
    assert any("New report" in t for t in h.texts_to(ADMIN))
    assert not any("report" in t.lower() for t in h.texts_to(B))      # reported user learns nothing
    await h.press(A, "chat:report")                                    # already reported
    await h.press(A, "repcat:spam")
    await h.press(A, "repskip")
    assert "already reported" in h.last_text(A)


async def test_report_right_after_chat_ended_and_skip(h):
    await two_chatting(h)
    await h.send(A, "/stop")
    await h.send(A, "/report")
    await h.press(A, "repcat:spam")
    await h.press(A, "repskip")
    assert await h.c.db.reports.count_documents({"reporter_id": A, "reported_id": B}) == 1


async def test_report_without_conversation(h):
    await register(h.c, A)
    await h.send(A, "/report")
    assert "no conversation" in h.last_text(A).lower()


async def test_block_via_button_ends_chat_and_prevents_rematch(h):
    await two_chatting(h)
    await h.press(A, "chat:block")
    assert await h.c.matchmaking.get_active(A) is None
    assert not any("block" in t.lower() for t in h.texts_to(B))        # partner is not told
    assert any("left the conversation" in t for t in h.texts_to(B))
    await h.connect(A, B)
    assert await h.c.matchmaking.get_active(A) is None                 # never matched again


async def test_block_command_and_unblock_manager(h):
    await two_chatting(h)
    await h.send(A, "/block")
    assert await h.c.moderation.relations(A) == [B]
    await h.send(A, "/unblock")
    assert "Blocked users" in h.last_text(A)
    block = (await h.c.moderation.list_blocked(A))[0]
    await h.press(A, f"unb:{block['_id']}")
    assert await h.c.moderation.relations(A) == []
    await h.send(A, "/unblock")
    assert "haven't blocked" in h.last_text(A)


async def test_block_callbacks_do_not_expose_telegram_ids(h):
    await two_chatting(h)
    await h.send(A, "/stop")
    markup = [m for m in h.session.calls if type(m).__name__ == "SendMessage" and m.chat_id == A][-1].reply_markup
    data = " ".join(btn.callback_data for row in markup.inline_keyboard for btn in row)
    assert str(B) not in data


async def test_cannot_report_someone_elses_conversation(h):
    await two_chatting(h)
    await register(h.c, 99)
    cid = (await h.c.matchmaking.get_active(A))["conversation_id"]
    await h.press(99, f"rep:{cid}")
    assert "no conversation" in h.last_text(99).lower()
    await h.press(99, f"blk:{cid}")
    assert await h.c.moderation.relations(99) == []


# ── bans ─────────────────────────────────────────────────────────────────
async def test_banned_user_is_locked_out(h):
    await register(h.c, A)
    await h.c.moderation.ban(A, "x", ADMIN)
    await h.send(A, "/find")
    assert "suspended" in h.last_text(A)
    await h.press(A, "menu:find")
    assert not await h.c.matchmaking.in_queue(A)


# ── admin ────────────────────────────────────────────────────────────────
async def test_admin_commands_are_not_available_to_normal_users(h):
    await register(h.c, A)
    await register(h.c, B)
    for cmd in ("/admin", "/stats", f"/ban {B}", f"/grant {A} 30", f"/user {B}", "/reports"):
        await h.send(A, cmd)
        assert "Unknown command" in h.last_text(A), cmd
    assert not await h.c.moderation.is_banned(B)
    assert await h.c.users.membership_of(A) == "free"
    await h.press(A, "adm:stats")                      # buttons are protected too
    assert "System statistics" not in h.last_text(A)


async def test_admin_panel_stats_and_user_search(h):
    await register(h.c, A)
    await h.send(ADMIN, "/admin")
    assert "Admin panel" in h.last_text(ADMIN)
    await h.send(ADMIN, "/stats")
    assert "Users:" in h.last_text(ADMIN) and "Premium members" in h.last_text(ADMIN)
    await h.send(ADMIN, f"/user {A}")
    assert f"<code>{A}</code>" in h.last_text(ADMIN) and "User11" in h.last_text(ADMIN)
    await h.send(ADMIN, "/user 424242")
    assert "not found" in h.last_text(ADMIN)


async def test_admin_ban_unban_and_active_chat_cleanup(h):
    await two_chatting(h)
    await h.send(ADMIN, f"/ban {A} abusive")
    assert await h.c.moderation.is_banned(A)
    assert await h.c.matchmaking.get_active(B) is None
    assert any("left the conversation" in t for t in h.texts_to(B))
    assert any("suspended" in t for t in h.texts_to(A))
    await h.send(ADMIN, f"/unban {A}")
    assert not await h.c.moderation.is_banned(A)
    await h.send(ADMIN, f"/ban {ADMIN}")
    assert "cannot be banned" in h.last_text(ADMIN)


async def test_admin_grant_revoke_premium(h):
    await register(h.c, A)
    await h.send(ADMIN, f"/grant {A} 30")
    assert await h.c.users.membership_of(A) == "premium"
    assert any("received Premium" in t for t in h.texts_to(A))
    await h.send(ADMIN, f"/revoke {A}")
    assert await h.c.users.membership_of(A) == "free"
    await h.send(ADMIN, f"/grant {A} 0")
    assert "Usage" in h.last_text(ADMIN)


async def test_admin_reviews_reports_and_bans_from_report(h):
    await two_chatting(h)
    await h.press(A, "chat:report")
    await h.press(A, "repcat:inappropriate")
    await h.press(A, "repskip")
    await h.send(ADMIN, "/reports")
    assert "Report" in h.last_text(ADMIN) and f"<code>{B}</code>" in h.last_text(ADMIN)
    rid = (await h.c.db.reports.find_one({}))["_id"]
    await h.press(ADMIN, f"adm:rban:{rid}")
    assert await h.c.moderation.is_banned(B)
    assert (await h.c.moderation.get_report(rid))["status"] == "actioned"
    assert "No open reports" in h.last_text(ADMIN)


async def test_admin_evidence_copies_from_reporters_chat(h):
    await two_chatting(h)
    await h.send(B, "insult")
    await h.press(A, "chat:report")
    await h.press(A, "repcat:harassment")
    await h.press(A, "repskip")
    rid = (await h.c.db.reports.find_one({}))["_id"]
    await h.press(ADMIN, f"adm:evid:{rid}")
    ev = h.copies_to(ADMIN)
    assert len(ev) == 1 and ev[0].from_chat_id == A and len(ev[0].message_ids) == 1


# ── premium purchase & payments ──────────────────────────────────────────
async def test_buy_button_creates_stars_invoice_but_does_not_activate(h):
    await register(h.c, A)
    await h.press(A, "buy:monthly")
    (inv,) = h.session.of("SendInvoice")
    assert inv.currency == "XTR" and inv.prices[0].amount == 250 and not inv.provider_token
    assert inv.payload == f"premium:monthly:{A}"
    assert await h.c.users.membership_of(A) == "free"          # a click alone never grants Premium


async def test_pre_checkout_and_successful_payment(h):
    await register(h.c, A)
    user = {"id": A, "is_bot": False, "first_name": "x"}
    await h.raw(pre_checkout_query={"id": "p1", "from": user, "currency": "XTR", "total_amount": 250, "invoice_payload": f"premium:monthly:{A}"})
    assert h.session.of("AnswerPreCheckoutQuery")[-1].ok is True
    await h.raw(pre_checkout_query={"id": "p2", "from": user, "currency": "XTR", "total_amount": 5, "invoice_payload": f"premium:monthly:{A}"})
    assert h.session.of("AnswerPreCheckoutQuery")[-1].ok is False

    sp = {"currency": "XTR", "total_amount": 250, "invoice_payload": f"premium:monthly:{A}",
          "telegram_payment_charge_id": "chg-1", "provider_payment_charge_id": ""}
    await h.send(A, None, successful_payment=sp)
    assert await h.c.users.membership_of(A) == "premium"
    assert "Premium is active until" in h.last_text(A)
    await h.send(A, None, successful_payment=sp)                 # Telegram retry / replay
    assert "already processed" in h.last_text(A)
    assert await h.c.db.payments.count_documents({}) == 1


async def test_forged_successful_payment_is_rejected_and_admin_alerted(h):
    await register(h.c, A)
    sp = {"currency": "XTR", "total_amount": 1, "invoice_payload": f"premium:monthly:{A}",
          "telegram_payment_charge_id": "forged", "provider_payment_charge_id": ""}
    await h.send(A, None, successful_payment=sp)
    assert await h.c.users.membership_of(A) == "free"
    assert any("Unverifiable payment" in t for t in h.texts_to(ADMIN))


async def test_refunded_payment_message_revokes_premium(h):
    await register(h.c, A)
    await h.c.subscriptions.process_successful_payment(A, f"premium:monthly:{A}", "XTR", 250, "chg-9")
    await h.send(A, None, refunded_payment={"currency": "XTR", "total_amount": 250, "invoice_payload": f"premium:monthly:{A}", "telegram_payment_charge_id": "chg-9"})
    assert await h.c.users.membership_of(A) == "free"


async def test_admin_refund_command(h):
    await register(h.c, A)
    await h.c.subscriptions.process_successful_payment(A, f"premium:monthly:{A}", "XTR", 250, "chg-5")
    await h.send(ADMIN, f"/refund {A} chg-5")
    assert h.session.of("RefundStarPayment")
    assert await h.c.users.membership_of(A) == "free"
    await h.send(ADMIN, f"/refund {A} nope")
    assert "No such payment" in h.last_text(ADMIN)


async def test_premium_screen_shows_prices_and_status(h):
    await register(h.c, A)
    await h.send(A, "/premium")
    assert "250" in h.last_text(A) and "2000" in h.last_text(A) and "FREE" in h.last_text(A)
    await h.c.subscriptions.grant(A, 30)
    await h.send(A, "/premium")
    assert "Premium active until" in h.last_text(A)


# ── misc ─────────────────────────────────────────────────────────────────
async def test_help_terms_privacy_paysupport(h):
    await register(h.c, A)
    for cmd, needle in (("/help", "/find"), ("/terms", "18"), ("/privacy", "not</b>"), ("/paysupport", "Stars")):
        await h.send(A, cmd)
        assert needle in h.last_text(A), cmd


async def test_statistics_screen(h):
    await register(h.c, A)
    await h.press(A, "menu:stats")
    assert "My Statistics" in h.last_text(A) and "0/100" in h.last_text(A)


async def test_groups_are_ignored(h):
    await h.raw(message={"message_id": 1, "date": 1, "chat": {"id": -100, "type": "group", "title": "g"},
                         "from": {"id": A, "is_bot": False, "first_name": "x"}, "text": "/start"})
    assert not h.session.calls


async def test_rate_limiting_drops_floods(make_container):
    c = make_container(rate_limit_events=3, rate_limit_window_seconds=60)
    h = Harness(c)
    await register(c, A)
    for _ in range(6):
        await h.send(A, "/help")
    texts = h.texts_to(A)
    assert sum("Help" in t for t in texts) == 3 and sum("too fast" in t for t in texts) == 1


async def test_pre_checkout_is_never_throttled(make_container):
    c = make_container(rate_limit_events=1, rate_limit_window_seconds=60)
    h = Harness(c)
    await register(c, A)
    await h.send(A, "/help")
    user = {"id": A, "is_bot": False, "first_name": "x"}
    await h.raw(pre_checkout_query={"id": "p", "from": user, "currency": "XTR", "total_amount": 250, "invoice_payload": f"premium:monthly:{A}"})
    assert h.session.of("AnswerPreCheckoutQuery")[-1].ok is True


async def test_health_endpoint(c):
    from aiohttp.test_utils import TestClient, TestServer
    from app.web import build_app
    async with TestClient(TestServer(build_app(c))) as client:
        resp = await client.get("/health")
        assert resp.status == 200 and (await resp.json())["status"] == "ok"


async def test_background_sweeps_notify_users(h, clock):
    from app.tasks import sweep_idle_conversations, sweep_premium, sweep_queue
    await register(h.c, A)
    await h.send(A, "/find")
    clock.advance(seconds=h.c.settings.queue_timeout_seconds + 1)
    await sweep_queue(h.bot, h.c)
    assert "No partner was found" in h.last_text(A) and not await h.c.matchmaking.in_queue(A)

    await register(h.c, B)
    await h.c.subscriptions.grant(B, 1)
    clock.advance(days=2)
    await sweep_premium(h.bot, h.c)
    assert "Premium has expired" in h.last_text(B)

    await h.connect(A, B)
    clock.advance(minutes=h.c.settings.conversation_idle_minutes + 1)
    await sweep_idle_conversations(h.bot, h.c)
    assert "nobody wrote" in h.last_text(A) and "nobody wrote" in h.last_text(B)
    assert await h.c.matchmaking.get_active(A) is None


async def test_sweeper_announces_matches_missed_by_live_requests(h):
    from app.tasks import sweep_queue
    await register(h.c, A)
    await register(h.c, B)
    now = h.c.clock()
    for uid in (A, B):   # simulate two waiting users who were both enqueued without pairing
        await h.c.db.queue.insert_one({"_id": uid, "gender": "male", "pref": "any", "exclude": [], "joined_at": now})
    await sweep_queue(h.bot, h.c)
    assert "Partner found" in h.last_text(A) and "Partner found" in h.last_text(B)
