from aiogram.types import InlineKeyboardButton as B, InlineKeyboardMarkup

from app.database.models import Gender
from app.utils import countries


def terms_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[B(text="✅ I agree and confirm my age", callback_data="reg:agree")]])


def gender_kb() -> InlineKeyboardMarkup:
    rows = [[B(text=Gender.LABELS[g], callback_data=f"reg:gender:{g}")] for g in Gender.ALL]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def countries_kb(page: int = 0) -> InlineKeyboardMarkup:
    pages = countries.total_pages()
    page = max(0, min(page, pages - 1))
    items = countries.page(page)
    rows = []
    for i in range(0, len(items), 2):
        rows.append([
            B(text=f"{countries.flag(code)} {name}"[:32], callback_data=f"reg:country:{code}")
            for code, name in items[i:i + 2]
        ])
    nav = []
    if page > 0:
        nav.append(B(text="⬅️ Prev", callback_data=f"reg:cpage:{page - 1}"))
    nav.append(B(text=f"{page + 1}/{pages}", callback_data="reg:noop"))
    if page < pages - 1:
        nav.append(B(text="Next ➡️", callback_data=f"reg:cpage:{page + 1}"))
    rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def country_results_kb(results: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    rows = [[B(text=f"{countries.flag(c)} {n}"[:40], callback_data=f"reg:country:{c}")] for c, n in results]
    rows.append([B(text="📋 Browse all countries", callback_data="reg:cpage:0")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [B(text="✅ Confirm & save", callback_data="reg:confirm")],
        [B(text="✏️ Name", callback_data="reg:edit:name"), B(text="✏️ Age", callback_data="reg:edit:age")],
        [B(text="✏️ Gender", callback_data="reg:edit:gender")],
    ])
