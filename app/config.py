"""Application settings, loaded from environment variables (.env supported)."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Mapping

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    """Raised when the configuration is missing or unsafe."""


@dataclass(frozen=True)
class Plan:
    id: str
    title: str
    stars: int
    days: int


@dataclass(frozen=True)
class Settings:
    bot_token: str
    min_age: int
    environment: str = "development"
    min_age_confirmed: bool = False
    max_age: int = 100
    mongodb_uri: str = "mongodb://127.0.0.1:27017"
    mongodb_db: str = "anonymous_chat"
    admin_ids: frozenset[int] = frozenset()
    owner_ids: frozenset[int] = frozenset()
    log_level: str = "INFO"

    free_daily_limit: int = 100
    premium_daily_limit: int = 200
    monthly_price_stars: int = 250
    yearly_price_stars: int = 2000
    monthly_days: int = 30
    yearly_days: int = 365

    queue_timeout_seconds: int = 120
    conversation_idle_minutes: int = 60
    refund_timed_out_searches: bool = False
    rate_limit_events: int = 30
    rate_limit_window_seconds: int = 10
    protect_content: bool = False
    metadata_ttl_days: int = 7
    report_window_minutes: int = 30
    media_group_delay: float = 1.0
    broadcast_rate_per_second: float = 20.0

    run_mode: str = "polling"
    webhook_base_url: str = ""
    webhook_secret: str = ""
    port: int = 8080

    @property
    def plans(self) -> dict[str, Plan]:
        return {
            "monthly": Plan("monthly", "Monthly Premium", self.monthly_price_stars, self.monthly_days),
            "yearly": Plan("yearly", "Yearly Premium", self.yearly_price_stars, self.yearly_days),
        }

    def daily_limit(self, membership: str) -> int:
        return self.premium_daily_limit if membership == "premium" else self.free_daily_limit

    @property
    def staff_ids(self) -> frozenset[int]:
        """Owners are always administrators too."""
        return self.admin_ids | self.owner_ids

    def is_owner(self, user_id: int) -> bool:
        return user_id in self.owner_ids

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def webhook_path(self) -> str:
        return f"/webhook/{self.webhook_secret}"

    @property
    def webhook_url(self) -> str:
        return self.webhook_base_url.rstrip("/") + self.webhook_path


def _int(env: Mapping[str, str], name: str, default: int | None = None) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        if default is None:
            raise ConfigError(f"{name} is required.")
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}.") from exc


def _bool(env: Mapping[str, str], name: str, default: bool = False) -> bool:
    raw = env.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _admin_ids(raw: str) -> frozenset[int]:
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part:
            try:
                ids.add(int(part))
            except ValueError as exc:
                raise ConfigError(f"ADMIN_IDS contains a non-numeric value: {part!r}") from exc
    return frozenset(ids)


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    if env is None:
        load_dotenv()
        env = os.environ

    token = env.get("BOT_TOKEN", "").strip()
    if not token:
        raise ConfigError("BOT_TOKEN is missing. Set it in your environment or .env file.")

    if not env.get("MIN_AGE", "").strip():
        raise ConfigError(
            "MIN_AGE is not set. The minimum age is a product/legal decision that this project "
            "deliberately does not assume. Set MIN_AGE (for example 18) in your environment."
        )

    base_url = env.get("WEBHOOK_BASE_URL", "").strip()
    if not base_url and env.get("RAILWAY_PUBLIC_DOMAIN"):
        base_url = "https://" + env["RAILWAY_PUBLIC_DOMAIN"].strip()

    settings = Settings(
        bot_token=token,
        min_age=_int(env, "MIN_AGE"),
        max_age=_int(env, "MAX_AGE", 100),
        min_age_confirmed=_bool(env, "MIN_AGE_POLICY_CONFIRMED"),
        environment=env.get("ENVIRONMENT", "development").strip().lower(),
        mongodb_uri=(env.get("MONGODB_URI") or env.get("MONGO_URL") or "mongodb://127.0.0.1:27017").strip(),
        mongodb_db=env.get("MONGODB_DB", "anonymous_chat").strip() or "anonymous_chat",
        admin_ids=_admin_ids(env.get("ADMIN_IDS", "")),
        owner_ids=_admin_ids(env.get("OWNER_IDS", "")),
        log_level=env.get("LOG_LEVEL", "INFO").strip().upper(),
        free_daily_limit=_int(env, "FREE_DAILY_LIMIT", 100),
        premium_daily_limit=_int(env, "PREMIUM_DAILY_LIMIT", 200),
        monthly_price_stars=_int(env, "MONTHLY_PRICE_STARS", 250),
        yearly_price_stars=_int(env, "YEARLY_PRICE_STARS", 2000),
        monthly_days=_int(env, "MONTHLY_DAYS", 30),
        yearly_days=_int(env, "YEARLY_DAYS", 365),
        queue_timeout_seconds=_int(env, "QUEUE_TIMEOUT_SECONDS", 120),
        conversation_idle_minutes=_int(env, "CONVERSATION_IDLE_MINUTES", 60),
        refund_timed_out_searches=_bool(env, "REFUND_TIMED_OUT_SEARCHES"),
        rate_limit_events=_int(env, "RATE_LIMIT_EVENTS", 30),
        rate_limit_window_seconds=_int(env, "RATE_LIMIT_WINDOW_SECONDS", 10),
        protect_content=_bool(env, "PROTECT_CONTENT"),
        metadata_ttl_days=_int(env, "METADATA_TTL_DAYS", 7),
        report_window_minutes=_int(env, "REPORT_WINDOW_MINUTES", 30),
        run_mode=env.get("RUN_MODE", "polling").strip().lower(),
        webhook_base_url=base_url,
        webhook_secret=env.get("WEBHOOK_SECRET", "").strip(),
        port=_int(env, "PORT", 8080),
        broadcast_rate_per_second=float(env.get("BROADCAST_RATE_PER_SECOND", "20") or 20),
    )
    validate_settings(settings)
    return settings


def validate_settings(s: Settings) -> None:
    if s.environment not in {"development", "production"}:
        raise ConfigError("ENVIRONMENT must be 'development' or 'production'.")
    if s.min_age < 1 or s.min_age >= s.max_age:
        raise ConfigError("MIN_AGE must be >= 1 and lower than MAX_AGE.")
    if s.is_production and not s.min_age_confirmed:
        raise ConfigError(
            "Production start refused: set MIN_AGE_POLICY_CONFIRMED=true to confirm that the "
            "minimum-age policy was explicitly decided (and reviewed against local law)."
        )
    if s.run_mode not in {"polling", "webhook"}:
        raise ConfigError("RUN_MODE must be 'polling' or 'webhook'.")
    if s.run_mode == "webhook":
        if not s.webhook_base_url.startswith("https://"):
            raise ConfigError("Webhook mode needs WEBHOOK_BASE_URL (https://...) or RAILWAY_PUBLIC_DOMAIN.")
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,256}", s.webhook_secret):
            raise ConfigError("WEBHOOK_SECRET must be 16-256 chars of A-Z a-z 0-9 _ - .")
    if s.free_daily_limit < 1 or s.premium_daily_limit < 1:
        raise ConfigError("Daily limits must be at least 1.")
    if s.monthly_price_stars < 1 or s.yearly_price_stars < 1:
        raise ConfigError("Premium prices must be at least 1 Star.")
    if s.is_production and not s.staff_ids:
        raise ConfigError("Set OWNER_IDS (and/or ADMIN_IDS) with at least one Telegram ID in production.")
    if not 0.5 <= s.broadcast_rate_per_second <= 30:
        raise ConfigError("BROADCAST_RATE_PER_SECOND must be between 0.5 and 30 (Telegram allows ~30 msg/s).")
