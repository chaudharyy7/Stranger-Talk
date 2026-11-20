"""Text builders for screens that several handlers show."""
from __future__ import annotations

from typing import Any

from app.container import Container
from app.database.models import Gender, Membership
from app.utils import countries
from app.utils.formatting import esc, fmt_dt


def profile_text(profile: dict[str, Any], title: str = "👤 <b>Your profile</b>") -> str:
    country = profile.get("country")
    country_line = (
        f"Country: <b>{countries.flag(country)} {esc(countries.country_name(country))}</b>\n"
        if country else ""
    )
    return (
        f"{title}\n\n"
        f"Name: <b>{esc(profile['display_name'])}</b>\n"
        f"Age: <b>{profile['age']}</b>\n"
        f"{country_line}"
        f"Gender: <b>{Gender.LABELS.get(profile['gender'], profile['gender'])}</b>"
    )


def membership_label(c: Container, user_id: int, membership: str) -> str:
    return "PREMIUM (owner)" if c.settings.is_owner(user_id) else membership.upper()


async def menu_text(c: Container, user_id: int) -> str:
    user = await c.users.get(user_id)
    membership = c.users.effective_membership(user)
    limit = c.settings.daily_limit(membership)
    used = await c.usage.used_today(user_id)
    return (
        "🌍 <b>GLOBAL ANONYMOUS CHAT</b>\n\n"
        f"Membership: <b>{membership_label(c, user_id, membership)}</b>\n"
        f"Daily Searches: <b>{used}/{limit}</b>"
    )


async def premium_status_text(c: Container, user_id: int) -> str:
    if c.settings.is_owner(user_id):
        return "👑 <b>Owner</b> — all Premium features are always enabled for you."
    user = await c.users.get(user_id)
    if c.users.effective_membership(user) == Membership.PREMIUM:
        return f"👑 Premium active until <b>{fmt_dt(user.get('premium_expiry'))}</b>"
    return "Membership: <b>FREE</b>"
