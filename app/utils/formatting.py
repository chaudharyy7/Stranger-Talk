from __future__ import annotations

import html
from datetime import datetime, timezone


def esc(value: object) -> str:
    """Escape user-controlled text for Telegram HTML parse mode."""
    return html.escape(str(value), quote=False)


def fmt_dt(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def fmt_duration(seconds: int) -> str:
    hours, rem = divmod(max(0, int(seconds)), 3600)
    minutes = rem // 60
    return f"{hours}h {minutes}m"
