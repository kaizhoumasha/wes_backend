from datetime import UTC, datetime, timedelta

from pytest import MonkeyPatch

from src.app.auth.models import auth as auth_models


def test_seconds_until_treats_naive_expire_time_as_utc(monkeypatch: MonkeyPatch) -> None:
    now = datetime(2026, 5, 8, 9, 0, 0, tzinfo=UTC)
    expire_time = datetime(2026, 5, 8, 9, 1, 30)

    monkeypatch.setattr(auth_models.timezone, "now_utc", lambda: now)

    assert auth_models._seconds_until(expire_time) == 90


def test_seconds_until_normalizes_aware_expire_time(monkeypatch: MonkeyPatch) -> None:
    now = datetime(2026, 5, 8, 9, 0, 0, tzinfo=UTC)
    expire_time = now + timedelta(seconds=45)

    monkeypatch.setattr(auth_models.timezone, "now_utc", lambda: now)

    assert auth_models._seconds_until(expire_time) == 45
