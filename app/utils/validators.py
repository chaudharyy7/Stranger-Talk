from __future__ import annotations

import re

NAME_MIN, NAME_MAX = 2, 30
_NAME_ALLOWED = re.compile(r"^[\w '.\-]+$", re.UNICODE)
_LINKISH = re.compile(r"(https?:|t\.me|www\.|@|\.com\b)", re.IGNORECASE)
_LONG_DIGITS = re.compile(r"\d{5,}")


class ValidationError(ValueError):
    """User-facing validation error (message is safe to show)."""


def validate_name(text: str | None) -> str:
    if not text:
        raise ValidationError("Please send your name as text.")
    name = re.sub(r"\s+", " ", text.strip())
    if not NAME_MIN <= len(name) <= NAME_MAX:
        raise ValidationError(f"Your name must be {NAME_MIN}–{NAME_MAX} characters long.")
    if not _NAME_ALLOWED.match(name) or not any(ch.isalpha() for ch in name):
        raise ValidationError("Use letters only (spaces, ' . - are fine). No emojis or symbols.")
    if _LINKISH.search(name) or _LONG_DIGITS.search(name):
        raise ValidationError("For your privacy, don't put usernames, links or phone numbers in your name.")
    return name


def validate_age(text: str | None, min_age: int, max_age: int) -> int:
    if not text or not re.fullmatch(r"\d{1,3}", text.strip()):
        raise ValidationError("Please send your age as a number, for example 21.")
    age = int(text.strip())
    if age < min_age:
        raise ValidationError(f"Sorry, you must be at least {min_age} years old to use this bot.")
    if age > max_age:
        raise ValidationError("Please enter your real age.")
    return age
