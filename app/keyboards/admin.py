from aiogram.types import InlineKeyboardButton as B, InlineKeyboardMarkup


def admin_panel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [B(text="📊 Statistics", callback_data="adm:stats")],
        [B(text="🚨 Review reports", callback_data="adm:reports")],
    ])


def report_kb(report_id: object) -> InlineKeyboardMarkup:
    rid = str(report_id)
    return InlineKeyboardMarkup(inline_keyboard=[
        [B(text="📎 Evidence", callback_data=f"adm:evid:{rid}")],
        [B(text="🔨 Ban reported user", callback_data=f"adm:rban:{rid}"),
         B(text="✅ Dismiss", callback_data=f"adm:rdis:{rid}")],
        [B(text="⏭️ Next report", callback_data=f"adm:rnext:{rid}")],
    ])


def user_kb(user_id: int, banned: bool) -> InlineKeyboardMarkup:
    ban_btn = (
        B(text="♻️ Unban", callback_data=f"adm:unban:{user_id}")
        if banned else B(text="🔨 Ban", callback_data=f"adm:ban:{user_id}")
    )
    return InlineKeyboardMarkup(inline_keyboard=[
        [ban_btn],
        [B(text="👑 Grant 30d", callback_data=f"adm:grant:{user_id}:30"),
         B(text="👑 Grant 365d", callback_data=f"adm:grant:{user_id}:365")],
        [B(text="❌ Revoke Premium", callback_data=f"adm:revoke:{user_id}")],
    ])


def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[B(text="⬅️ Admin panel", callback_data="adm:home")]])
