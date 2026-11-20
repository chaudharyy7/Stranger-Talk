from aiogram.types import InlineKeyboardButton as B, InlineKeyboardMarkup


def profile_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [B(text="✏️ Name", callback_data="pf:edit:name"), B(text="✏️ Age", callback_data="pf:edit:age")],
        [B(text="✏️ Country", callback_data="pf:edit:country"), B(text="✏️ Gender", callback_data="pf:edit:gender")],
        [B(text="🏠 Main menu", callback_data="menu:home")],
    ])
