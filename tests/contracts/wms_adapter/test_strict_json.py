from __future__ import annotations

import pytest

from src.app.wms_adapter.strict_json import valid_json_response_headers


@pytest.mark.parametrize(
    "headers",
    [
        [("Content-Type", "application/json"), ("Content-Encoding",)],
        [(None, "application/json")],
        [("Content-Type", object())],
    ],
)
def test_valid_json_response_headers_rejects_malformed_pairs(headers: object) -> None:
    assert valid_json_response_headers(headers) is False


@pytest.mark.parametrize(
    "headers",
    [
        (("Content-Type", "application/json"),),
        (
            ("content-type", "application/json; charset=utf-8"),
            ("CONTENT-ENCODING", " identity "),
        ),
    ],
)
def test_valid_json_response_headers_accepts_utf8_json_with_identity_encoding(
    headers: tuple[tuple[str, str], ...],
) -> None:
    assert valid_json_response_headers(headers) is True


@pytest.mark.parametrize(
    "headers",
    [
        (("Content-Type", "application/json"), ("Content-Type", "application/json")),
        (("Content-Type", "application/json"), ("Content-Encoding", "identity"), ("Content-Encoding", "identity")),
        (("Content-Type", "application/json"), ("Content-Encoding", "gzip")),
    ],
)
def test_valid_json_response_headers_rejects_ambiguous_or_encoded_headers(
    headers: tuple[tuple[str, str], ...],
) -> None:
    assert valid_json_response_headers(headers) is False
