from __future__ import annotations

import pycountry


def _build() -> list[tuple[str, str]]:
    items = []
    for c in pycountry.countries:
        name = getattr(c, "common_name", None) or c.name
        items.append((c.alpha_2, name))
    items.sort(key=lambda x: x[1].casefold())
    return items


COUNTRIES: list[tuple[str, str]] = _build()
_BY_CODE = dict(COUNTRIES)
PAGE_SIZE = 12


def is_valid_code(code: str) -> bool:
    return code in _BY_CODE


def country_name(code: str) -> str:
    return _BY_CODE.get(code, code)


def flag(code: str) -> str:
    if len(code) == 2 and code.isalpha():
        return "".join(chr(0x1F1E6 + ord(ch) - ord("A")) for ch in code.upper())
    return "🌍"


def total_pages() -> int:
    return (len(COUNTRIES) + PAGE_SIZE - 1) // PAGE_SIZE


def page(n: int) -> list[tuple[str, str]]:
    n = max(0, min(n, total_pages() - 1))
    return COUNTRIES[n * PAGE_SIZE:(n + 1) * PAGE_SIZE]


def search(query: str, limit: int = 10) -> list[tuple[str, str]]:
    q = query.strip().casefold()
    if len(q) < 2:
        return []
    starts = [c for c in COUNTRIES if c[1].casefold().startswith(q)]
    contains = [c for c in COUNTRIES if q in c[1].casefold() and c not in starts]
    return (starts + contains)[:limit]
