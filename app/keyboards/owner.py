from aiogram.types import InlineKeyboardButton as B, InlineKeyboardMarkup


def broadcast_confirm_kb(broadcast_id: object, total: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        B(text=f"✅ Send to {total} users", callback_data=f"bc:go:{broadcast_id}"),
        B(text="✖️ Cancel", callback_data=f"bc:no:{broadcast_id}"),
    ]])


def broadcast_running_kb(broadcast_id: object) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[B(text="⛔ Stop broadcast", callback_data=f"bc:stop:{broadcast_id}")]])
