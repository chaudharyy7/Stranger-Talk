from aiogram.types import InlineKeyboardButton as B, InlineKeyboardMarkup

from app.database.models import Pref


def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [B(text="🎲 Find Random Partner", callback_data="menu:find")],
        [B(text="🔍 Gender Search (Premium)", callback_data="menu:gender")],
        [B(text="👤 My Profile", callback_data="menu:profile"), B(text="📊 My Statistics", callback_data="menu:stats")],
        [B(text="👑 Upgrade to Premium", callback_data="menu:premium")],
        [B(text="ℹ️ Help & Commands", callback_data="menu:help")],
    ])


def home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[B(text="🏠 Main menu", callback_data="menu:home")]])


def upgrade_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [B(text="👑 Upgrade to Premium", callback_data="menu:premium")],
        [B(text="🏠 Main menu", callback_data="menu:home")],
    ])


def gender_pref_kb() -> InlineKeyboardMarkup:
    rows = [[B(text=Pref.LABELS[p], callback_data=f"gs:{p}")] for p in (Pref.MALE, Pref.FEMALE, Pref.OTHER, Pref.ANY)]
    rows.append([B(text="⬅️ Back", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
