from __future__ import annotations

import importlib
from types import ModuleType

from aiogram import Dispatcher, Router
from aiogram.fsm.storage.memory import MemoryStorage

from app.container import Container
from app.handlers import admin, matchmaking, messaging, owner, payments, premium, profile, registration, reports, start
from app.middlewares.access import AccessMiddleware
from app.middlewares.throttle import ThrottleMiddleware


def _router_of(module: ModuleType) -> Router:
    """A aiogram Router can be attached to only one Dispatcher. Production creates exactly one,
    but tests create many, so re-import the handler module to get a fresh Router when needed."""
    if module.router.parent_router is not None:
        module = importlib.reload(module)
    return module.router


def create_dispatcher(container: Container) -> Dispatcher:
    s = container.settings
    dp = Dispatcher(storage=MemoryStorage(), container=container)
    dp.update.outer_middleware(ThrottleMiddleware(s.rate_limit_events, s.rate_limit_window_seconds))
    dp.update.outer_middleware(AccessMiddleware(container))

    # Order matters: specific handlers first, the catch-all relay LAST.
    for module in (start, registration, payments, premium, owner, admin, reports, profile, matchmaking, messaging):
        dp.include_router(_router_of(module))
    return dp
