from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.config import Settings
from app.database.session import Database
from app.services.broadcast_service import BroadcastService
from app.services.matchmaking_service import MatchmakingService
from app.services.message_service import MessageService
from app.services.moderation_service import ModerationService
from app.services.runtime_service import RuntimeService
from app.services.staff_service import StaffService
from app.services.stats_service import StatsService
from app.services.subscription_service import SubscriptionService
from app.services.usage_service import UsageService
from app.services.user_service import UserService
from app.utils.time import Clock, utcnow


@dataclass
class Container:
    settings: Settings
    db: Database
    clock: Clock
    users: UserService
    usage: UsageService
    stats: StatsService
    moderation: ModerationService
    matchmaking: MatchmakingService
    messages: MessageService
    subscriptions: SubscriptionService
    staff: StaffService
    runtime: RuntimeService
    broadcasts: BroadcastService
    started_at: datetime


def build_container(settings: Settings, db: Database, clock: Clock = utcnow) -> Container:
    users = UserService(db, settings, clock)
    usage = UsageService(db, settings, clock)
    stats = StatsService(db, settings, clock)
    moderation = ModerationService(db, settings, users, clock)
    matchmaking = MatchmakingService(db, settings, users, usage, moderation, stats, clock)
    return Container(
        settings=settings,
        db=db,
        clock=clock,
        users=users,
        usage=usage,
        stats=stats,
        moderation=moderation,
        matchmaking=matchmaking,
        messages=MessageService(db, settings, clock),
        subscriptions=SubscriptionService(db, settings, clock),
        staff=StaffService(db, settings, clock),
        runtime=RuntimeService(db),
        broadcasts=BroadcastService(db, settings, clock),
        started_at=clock(),
    )
