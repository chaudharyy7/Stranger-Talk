from aiogram.types import InlineKeyboardButton as B, InlineKeyboardMarkup


def chat_kb() -> InlineKeyboardMarkup:
    """Shown while two users are connected."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [B(text="⏭️ Next", callback_data="chat:next"), B(text="❌ Stop", callback_data="chat:stop")],
        [B(text="🚨 Report", callback_data="chat:report"), B(text="🚫 Block", callback_data="chat:block")],
    ])


def after_chat_kb(conversation_id: object) -> InlineKeyboardMarkup:
    """Shown after a chat ended. Report/Block refer to the finished conversation by its opaque id."""
    cid = str(conversation_id)
    return InlineKeyboardMarkup(inline_keyboard=[
        [B(text="🎲 Find new partner", callback_data="menu:find")],
        [B(text="🚨 Report", callback_data=f"rep:{cid}"), B(text="🚫 Block", callback_data=f"blk:{cid}")],
        [B(text="🏠 Main menu", callback_data="menu:home")],
    ])


def searching_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[B(text="✖️ Cancel search", callback_data="search:cancel")]])


def private_confirm_kb(message_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        B(text="✅ Send anyway", callback_data=f"priv:ok:{message_id}"),
        B(text="✖️ Don't send", callback_data=f"priv:no:{message_id}"),
    ]])
