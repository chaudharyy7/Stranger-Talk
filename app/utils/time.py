from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

Clock = Callable[[], datetime]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def utc_date(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m-%d")


def seconds_until_utc_midnight(now: datetime) -> int:
    now = now.astimezone(timezone.utc)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((tomorrow - now).total_seconds()))
