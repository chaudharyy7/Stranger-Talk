"""Document schema reference and constants.

MongoDB is schemaless, so this module is the single source of truth for collection names,
allowed values and the shape of each document.

users                     _id = telegram_id (int)
    membership_type       "free" | "premium"
    account_status        "active" | "banned"
    registration_date     datetime | None      (set when the profile is confirmed)
    terms_accepted_at     datetime | None
    premium_expiry        datetime | None
    last_active           datetime
    bot_blocked           bool (set when the user blocked the bot; skipped by broadcasts)
    stats                 {chats, searches, messages}
    created_at, updated_at

profiles                  _id = telegram_id;  display_name, age, optional country (ISO-2), gender, updated_at
subscriptions             _id = telegram_id;  plan_type, subscription_status, start_date, expiry_date,
                          telegram_payment_charge_id, updated_at
payments                  _id ObjectId; user_id, plan_id, days, stars, currency, payload,
                          telegram_payment_charge_id (UNIQUE), provider_payment_charge_id, status, created_at
daily_usage               _id "<telegram_id>:<YYYY-MM-DD UTC>"; user_id, date, count
conversations             _id ObjectId; participants [a, b], status, started_at, last_activity,
                          ended_at, ended_by, end_reason, message_count
conversation_participants _id = telegram_id (UNIQUE => one active chat per user);
                          conversation_id, partner_id, pref, joined_at
messages_metadata         conversation_id, sender_id, recipient_id, content_type, sent_at (TTL),
                          delivered_message_id   -- NEVER message content
reports                   _id ObjectId; reporter_id, reported_id, conversation_id, category, context,
                          evidence_message_ids, status, created_at, reviewed_by, reviewed_at
blocked_users             blocker_id, blocked_id, conversation_id, created_at  (UNIQUE pair)
banned_users              _id = telegram_id; reason, banned_by, banned_at
matchmaking_queue         _id = telegram_id; gender, pref, exclude [ids], joined_at
stats_daily               _id "<YYYY-MM-DD UTC>"; searches, matches, failed
staff                     _id = telegram_id of an administrator added at runtime by an owner; added_by, added_at
broadcasts                _id ObjectId; created_by, audience, mode ("text"|"copy"), text / source ids, status,
                          total, sent, failed, blocked, created_at, started_at, finished_at
bot_settings              _id "runtime": maintenance (bool), maintenance_message
schema_migrations         _id = version
"""
from __future__ import annotations


class Membership:
    FREE = "free"
    PREMIUM = "premium"


class AccountStatus:
    ACTIVE = "active"
    BANNED = "banned"


class Gender:
    MALE = "male"
    FEMALE = "female"
    OTHER = "other"
    PREFER_NOT = "prefer_not_to_say"
    ALL = (MALE, FEMALE, OTHER, PREFER_NOT)
    LABELS = {
        MALE: "♂️ Male",
        FEMALE: "♀️ Female",
        OTHER: "⚧ Other",
        PREFER_NOT: "🤐 Prefer not to say",
    }


class Pref:
    """What a user is searching for. Only MALE/FEMALE/OTHER are filterable."""
    ANY = "any"
    MALE = Gender.MALE
    FEMALE = Gender.FEMALE
    OTHER = Gender.OTHER
    ALL = (ANY, MALE, FEMALE, OTHER)
    LABELS = {ANY: "🌐 Any gender", MALE: "♂️ Male", FEMALE: "♀️ Female", OTHER: "⚧ Other"}


class ConversationStatus:
    ACTIVE = "active"
    ENDED = "ended"


class ReportStatus:
    OPEN = "open"
    DISMISSED = "dismissed"
    ACTIONED = "actioned"


class PaymentStatus:
    RECEIVED = "received"
    ACTIVATED = "activated"
    REFUNDED = "refunded"


class SubscriptionStatus:
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"
    REFUNDED = "refunded"


REPORT_CATEGORIES = {
    "harassment": "🤬 Harassment / abuse",
    "spam": "📢 Spam / scam",
    "inappropriate": "🔞 Inappropriate content",
    "underage": "🧒 Suspected underage user",
    "other": "❓ Other",
}
