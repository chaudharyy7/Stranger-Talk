import pytest

from app.config import ConfigError, load_settings
from app.utils.validators import ValidationError, validate_age, validate_name

BASE = {"BOT_TOKEN": "123456789:" + "A" * 35, "MIN_AGE": "18"}


@pytest.mark.parametrize("name", ["Al", "Alice", "Mary-Jane O'Neil", "Åsa", "Жанна", "Ravi Kumar"])
def test_valid_names(name):
    assert validate_name(name)


@pytest.mark.parametrize("name", ["", "A", "x" * 31, "@alice", "t.me/alice", "call 9876543210", "😀😀", "12", "a<b>"])
def test_invalid_names(name):
    with pytest.raises(ValidationError):
        validate_name(name)


def test_name_whitespace_is_normalised():
    assert validate_name("  Alice   Smith ") == "Alice Smith"


def test_valid_age():
    assert validate_age("25", 18, 100) == 25


@pytest.mark.parametrize("text", ["", "abc", "-5", "17", "1000", "25.5", None])
def test_invalid_age(text):
    with pytest.raises(ValidationError):
        validate_age(text, 18, 100)


def test_min_age_is_configurable():
    assert validate_age("13", 13, 100) == 13
    with pytest.raises(ValidationError):
        validate_age("13", 16, 100)


def test_min_age_is_never_assumed():
    with pytest.raises(ConfigError, match="MIN_AGE"):
        load_settings({"BOT_TOKEN": BASE["BOT_TOKEN"]})


def test_production_needs_explicit_age_confirmation():
    env = {**BASE, "ENVIRONMENT": "production", "ADMIN_IDS": "1"}
    with pytest.raises(ConfigError, match="MIN_AGE_POLICY_CONFIRMED"):
        load_settings(env)
    assert load_settings({**env, "MIN_AGE_POLICY_CONFIRMED": "true"}).is_production


def test_token_required_and_admin_ids_parsed():
    with pytest.raises(ConfigError):
        load_settings({"MIN_AGE": "18"})
    assert load_settings({**BASE, "ADMIN_IDS": "1, 2,3"}).admin_ids == frozenset({1, 2, 3})
    with pytest.raises(ConfigError):
        load_settings({**BASE, "ADMIN_IDS": "abc"})


def test_webhook_mode_validation():
    with pytest.raises(ConfigError):
        load_settings({**BASE, "RUN_MODE": "webhook"})
    s = load_settings({**BASE, "RUN_MODE": "webhook", "WEBHOOK_BASE_URL": "https://x.up.railway.app",
                       "WEBHOOK_SECRET": "a" * 32})
    assert s.webhook_url == "https://x.up.railway.app/webhook/" + "a" * 32


def test_limits_and_prices_configurable():
    s = load_settings({**BASE, "FREE_DAILY_LIMIT": "5", "PREMIUM_DAILY_LIMIT": "9", "MONTHLY_PRICE_STARS": "99"})
    assert (s.daily_limit("free"), s.daily_limit("premium"), s.plans["monthly"].stars) == (5, 9, 99)
