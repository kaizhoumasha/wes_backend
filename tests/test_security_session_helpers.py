import pytest

from src.core.security import _load_session_data


def test_load_session_data_supports_bytes_payload() -> None:
    payload = b'{"jti":"access-jti","iat":1710000000,"extra":{"username":"testuser"}}'

    result = _load_session_data(payload, context="test")

    assert result == {
        "jti": "access-jti",
        "iat": 1710000000,
        "extra": {"username": "testuser"},
    }


def test_load_session_data_returns_none_for_non_dict_json() -> None:
    payload = b'["not-a-dict"]'

    result = _load_session_data(payload, context="test")

    assert result is None


def test_load_session_data_returns_none_for_invalid_json() -> None:
    payload = b"{invalid-json"

    result = _load_session_data(payload, context="test")

    assert result is None
