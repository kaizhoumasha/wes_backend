"""WMS QUERY response 边界分支覆盖。"""

from __future__ import annotations

import gzip
import json
import zlib

import pytest

from src.app.wms_integration.operation_registry import QUERY_OPERATIONS
from src.app.wms_integration.ports.query_outcome import QueryBusinessReject, QueryTechnicalFailure
from src.app.wms_integration.query_response import (
    QueryBudgetViolation,
    _parse_retry_after,
    _payload_text,
    classify_http_failure,
    parse_bounded_json,
    parse_optional_failure_body,
)
from src.core.bounded_http_response import (
    HttpContentEncodingFailure,
    HttpDecodedBudgetViolation,
    decode_bounded_http_body,
)


def _decode(raw_body: bytes, *, encoding: str = "identity", decoded_limit: int = 128, ratio: float = 100.0) -> bytes:
    return decode_bounded_http_body(
        raw_body,
        content_encoding=encoding,
        allowed_content_encodings=("identity", "gzip", "deflate"),
        max_decoded_bytes=decoded_limit,
        max_compression_ratio=ratio,
    )


def test_budget_violation_retains_reason_and_message() -> None:
    error = QueryBudgetViolation("BUDGET", "too large")

    assert str(error) == "too large"
    assert error.reason_code == "BUDGET"
    assert error.message == "too large"


def test_decode_rejects_unsupported_encoding_and_non_positive_budget() -> None:
    with pytest.raises(HttpDecodedBudgetViolation, match="unsupported") as unsupported:
        _decode(b"{}", encoding="br")
    assert unsupported.value.reason_code == "WMS_UNSUPPORTED_CONTENT_ENCODING"

    with pytest.raises(HttpDecodedBudgetViolation) as exhausted:
        _decode(b"{}", decoded_limit=0)
    assert exhausted.value.reason_code == "WMS_DECODED_BUDGET_EXCEEDED"


def test_decode_identity_enforces_decoded_and_ratio_budgets() -> None:
    assert _decode(b"{}", encoding="  ") == b"{}"

    with pytest.raises(HttpDecodedBudgetViolation) as decoded:
        _decode(b"large", decoded_limit=2)
    assert decoded.value.reason_code == "WMS_DECODED_BUDGET_EXCEEDED"

    with pytest.raises(HttpDecodedBudgetViolation) as ratio:
        _decode(b"1234", ratio=0.5)
    assert ratio.value.reason_code == "WMS_COMPRESSION_RATIO_EXCEEDED"


@pytest.mark.parametrize(
    ("encoding", "compressed"),
    [
        ("gzip", gzip.compress(b'{"ok":true}')),
        ("deflate", zlib.compress(b'{"ok":true}')),
    ],
)
def test_decode_supported_compression(encoding: str, compressed: bytes) -> None:
    assert _decode(compressed, encoding=encoding) == b'{"ok":true}'


def test_decode_rejects_corrupt_truncated_trailing_and_oversized_compression() -> None:
    with pytest.raises(HttpContentEncodingFailure):
        _decode(b"not-compressed", encoding="gzip")

    compressed = gzip.compress(b'{"ok":true}')
    with pytest.raises(HttpContentEncodingFailure):
        _decode(compressed[:-2], encoding="gzip")
    with pytest.raises(HttpContentEncodingFailure):
        _decode(compressed + b"trailing", encoding="gzip")
    with pytest.raises(HttpDecodedBudgetViolation) as oversized:
        _decode(gzip.compress(b"x" * 512), encoding="gzip", decoded_limit=16)
    assert oversized.value.reason_code == "WMS_DECODED_BUDGET_EXCEEDED"


def test_parse_json_enforces_depth_string_and_key_lengths() -> None:
    assert parse_bounded_json(b'{"items":[1]}', max_depth=4, max_field_length=8) == {"items": [1]}

    with pytest.raises(QueryBudgetViolation) as depth:
        parse_bounded_json(b'{"a":{"b":1}}', max_depth=2, max_field_length=8)
    assert depth.value.reason_code == "WMS_JSON_DEPTH_EXCEEDED"

    with pytest.raises(QueryBudgetViolation) as value:
        parse_bounded_json(b'{"a":"long"}', max_depth=3, max_field_length=3)
    assert value.value.reason_code == "WMS_JSON_FIELD_LENGTH_EXCEEDED"

    with pytest.raises(QueryBudgetViolation) as key:
        parse_bounded_json(b'{"long":1}', max_depth=3, max_field_length=3)
    assert key.value.reason_code == "WMS_JSON_FIELD_LENGTH_EXCEEDED"


@pytest.mark.parametrize(
    ("status_code", "expected_type", "reason_code", "retryable"),
    [
        (200, type(None), None, None),
        (408, QueryTechnicalFailure, "WMS_PROVIDER_TIMEOUT", True),
        (429, QueryTechnicalFailure, "WMS_RATE_LIMITED", True),
        (401, QueryTechnicalFailure, "WMS_AUTHENTICATION_FAILED", False),
        (403, QueryTechnicalFailure, "WMS_AUTHORIZATION_FAILED", False),
        (400, QueryTechnicalFailure, "WMS_PROVIDER_CLIENT_ERROR", False),
    ],
)
def test_http_failure_fixed_status_matrix(
    status_code: int,
    expected_type: type,
    reason_code: str | None,
    retryable: bool | None,
) -> None:
    outcome = classify_http_failure(status_code, None, {"retry-after": "2"})

    assert isinstance(outcome, expected_type)
    if isinstance(outcome, QueryTechnicalFailure):
        assert outcome.reason_code == reason_code
        assert outcome.retryable is retryable


def test_http_failure_business_reject_requires_non_blank_declared_reason() -> None:
    rejected = classify_http_failure(
        422,
        {"classification": "BUSINESS_REJECT", "reason_code": "INVALID", "message": " rejected "},
        {},
    )
    missing_reason = classify_http_failure(
        422,
        {"classification": "BUSINESS_REJECT", "reason_code": "   "},
        {},
    )

    assert rejected == QueryBusinessReject(reason_code="INVALID", message="rejected")
    assert isinstance(missing_reason, QueryTechnicalFailure)


def test_optional_failure_body_handles_empty_valid_and_invalid_payloads() -> None:
    budget = QUERY_OPERATIONS[0].budget
    payload = {"classification": "BUSINESS_REJECT", "reason_code": "INVALID"}

    assert parse_optional_failure_body(b"", content_encoding="identity", budget=budget) is None
    assert (
        parse_optional_failure_body(
            json.dumps(payload).encode(),
            content_encoding="identity",
            budget=budget,
        )
        == payload
    )
    assert parse_optional_failure_body(b"not-json", content_encoding="identity", budget=budget) is None


def test_payload_text_and_retry_after_helpers_fail_closed() -> None:
    assert _payload_text([], "message", "default") == "default"
    assert _payload_text({"message": "  "}, "message", "default") == "default"
    assert _parse_retry_after(None) is None
    assert _parse_retry_after("invalid") is None
    assert _parse_retry_after("-1") is None
    assert _parse_retry_after("1.5") == 1.5
