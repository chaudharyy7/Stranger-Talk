"""Short in-bot versions of the Terms and Privacy Policy.
The full templates live in docs/. Have them reviewed by a lawyer before launch."""
from __future__ import annotations

from app.config import Settings

PRIVACY_NOTICE = (
    "🔒 <b>Privacy notice</b>\n"
    "Your Telegram username, phone number and Telegram ID are never shown to chat partners.\n"
    "Anonymity applies <b>between users</b>: messages pass through this bot's servers and Telegram, "
    "so the bot operator and Telegram are technically able to see them. Don't share secrets."
)


def terms_text(s: Settings) -> str:
    return (
        "📜 <b>Terms of Service (summary)</b>\n\n"
        f"1. You must be at least <b>{s.min_age}</b> years old.\n"
        "2. Be respectful. Harassment, hate, threats, scams, spam, sexual content involving minors, "
        "illegal content and sharing others' private data are forbidden and lead to a permanent ban.\n"
        "3. Do not try to identify, track or contact other users outside the bot.\n"
        "4. Partners are strangers. We cannot verify what they say. Never send money or personal data.\n"
        "5. Users can report and block. Reports are reviewed by administrators, who may ban accounts.\n"
        "6. Premium is a digital service paid with Telegram Stars. It is activated only after Telegram "
        "confirms the payment. Refunds are handled through /paysupport and Telegram's rules.\n"
        "7. The service is provided \"as is\" without guarantees of availability.\n\n"
        "Full terms: see the TERMS_OF_SERVICE document of this project."
    )


def privacy_text(s: Settings) -> str:
    return (
        "🔒 <b>Privacy Policy (summary)</b>\n\n"
        "<b>What we store</b>: your Telegram ID, the profile you entered (name, age, optional country, gender), "
        "membership/payment records, daily search counters, block/report records, and message "
        f"<i>metadata</i> (type, time, ids) for {s.metadata_ttl_days} days. We do <b>not</b> store message text or media.\n\n"
        "<b>What partners see</b>: none of your profile details. Never your username, "
        "phone number or Telegram ID.\n\n"
        "<b>Important</b>: messages are relayed through this bot, so the bot operator and Telegram can "
        "technically access them. Anonymity is between users only.\n\n"
        "<b>Reports</b>: when you report someone, administrators may review the last messages you "
        "received from them, directly from your Telegram chat.\n\n"
        "<b>Your rights</b>: contact the administrator to access or delete your data.\n\n"
        "Full policy: see the PRIVACY_POLICY document of this project."
    )


def help_text() -> str:
    return (
        "ℹ️ <b>Help &amp; Commands</b>\n\n"
        "/start – Start the bot / main menu\n"
        "/find – Find a random partner\n"
        "/next – Disconnect and search again\n"
        "/stop – End the conversation (or cancel the search)\n"
        "/profile – View and edit your profile\n"
        "/report – Report your current (or just-finished) partner\n"
        "/block – Block your current partner\n"
        "/unblock – Manage blocked users\n"
        "/premium – Premium plans\n"
        "/terms, /privacy, /paysupport – Legal and payment support\n"
        "/cancel – Cancel what you are currently doing\n\n"
        "While connected, everything you send (text, photos, videos, voice, audio, files, stickers, "
        "GIFs, video notes) is delivered anonymously. Contacts and locations need your confirmation.\n\n"
        + PRIVACY_NOTICE
    )
