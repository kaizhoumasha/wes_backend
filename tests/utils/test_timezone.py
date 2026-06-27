from datetime import UTC, datetime

from src.utils.timezone import timezone


def test_to_db_datetime_normalizes_aware_datetime_to_naive_utc() -> None:
    aware = datetime(2026, 5, 8, 9, 30, 0, tzinfo=UTC)

    normalized = timezone.to_db_datetime(aware)

    assert normalized == datetime(2026, 5, 8, 9, 30, 0)
    assert normalized.tzinfo is None


def test_to_db_datetime_parses_iso_z_string_as_naive_utc() -> None:
    normalized = timezone.to_db_datetime("2026-05-08T09:30:00Z")

    assert normalized == datetime(2026, 5, 8, 9, 30, 0)
    assert normalized.tzinfo is None


def test_to_db_datetime_converts_unix_seconds_to_naive_utc() -> None:
    source = datetime(2026, 5, 8, 9, 30, 0, 123000, tzinfo=UTC)

    normalized = timezone.to_db_datetime(source.timestamp())

    assert normalized == datetime(2026, 5, 8, 9, 30, 0, 123000)
    assert normalized.tzinfo is None


def test_parse_datetime_preserves_iso_timezone_offset() -> None:
    parsed = timezone.parse_datetime("2026-05-08T17:30:00+08:00")

    assert parsed is not None
    offset = parsed.utcoffset()
    assert offset is not None
    assert parsed == datetime(2026, 5, 8, 17, 30, 0, tzinfo=parsed.tzinfo)
    assert offset.total_seconds() == 8 * 3600


def test_to_db_datetime_returns_none_for_invalid_input() -> None:
    assert timezone.to_db_datetime("not-a-datetime") is None
    assert timezone.to_db_datetime(None) is None
