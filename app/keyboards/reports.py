from aiogram.types import InlineKeyboardButton as B, InlineKeyboardMarkup

from app.database.models import REPORT_CATEGORIES


def categories_kb() -> InlineKeyboardMarkup:
    rows = [[B(text=label, callback_data=f"repcat:{key}")] for key, label in REPORT_CATEGORIES.items()]
    rows.append([B(text="✖️ Cancel", callback_data="repcancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def skip_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[B(text="⏭️ Skip", callback_data="repskip")]])


def block_offer_kb(conversation_id: object) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [B(text="🚫 Also block this user", callback_data=f"blk:{conversation_id}")],
        [B(text="🏠 Main menu", callback_data="menu:home")],
    ])
