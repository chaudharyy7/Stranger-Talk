from aiogram.types import InlineKeyboardButton as B, InlineKeyboardMarkup

from app.config import Settings


def plans_kb(settings: Settings, show_plans: bool = True) -> InlineKeyboardMarkup:
    rows = [
        [B(text=f"⭐ {p.title} — {p.stars} Stars", callback_data=f"buy:{p.id}")]
        for p in settings.plans.values()
    ] if show_plans else []
    rows.append([B(text="🏠 Main menu", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
