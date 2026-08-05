from __future__ import annotations

import math
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from src.core.outbound_http.contracts import (
    OutboundHttpDeliveryState,
    OutboundHttpFailureKind,
    OutboundHttpMethod,
    OutboundHttpRequest,
    OutboundHttpRequestError,
    OutboundHttpResponseLimits,
    OutboundHttpResult,
)


def test_request_is_immutable_and_preserves_ordered_inputs() -> None:
    request = OutboundHttpRequest(
        method=OutboundHttpMethod.POST,
        path="/v1/items",
        query=(("tag", "first"), ("tag", "second")),
        headers=(("X-Trace", "one"),),
        body=b"payload",
    )

    assert request.query == (("tag", "first"), ("tag", "second"))
    assert request.headers == (("X-Trace", "one"),)
    assert request.body == b"payload"
    with pytest.raises(FrozenInstanceError):
        request.path = "/other"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("path", "reason"),
    [
        ("https://example.test/items", "absolute URL"),
        ("//example.test/items", "network-path URL"),
        ("/items?tag=one", "query in path"),
        ("/items#fragment", "fragment in path"),
        ("/items\r\n", "control character"),
    ],
)
def test_request_rejects_non_relative_or_ambiguous_path(path: str, reason: str) -> None:
    with pytest.raises(OutboundHttpRequestError, match="path"):
        OutboundHttpRequest(method=OutboundHttpMethod.GET, path=path)


def test_request_rejects_method_outside_the_closed_enum() -> None:
    with pytest.raises(TypeError, match="method"):
        OutboundHttpRequest(method="PUT", path="/items")  # type: ignore[arg-type]


def test_request_rejects_response_limits_outside_the_contract_type() -> None:
    with pytest.raises(TypeError, match="response limit"):
        OutboundHttpRequest(
            method=OutboundHttpMethod.GET,
            path="/items",
            response_limits=object(),  # type: ignore[arg-type]
        )


def test_request_rejects_body_outside_the_declared_bytes_type() -> None:
    with pytest.raises(TypeError, match="body"):
        OutboundHttpRequest(
            method=OutboundHttpMethod.POST,
            path="/items",
            body=5,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "headers",
    [
        (("X-Trace", "one\r\ntwo"),),
        (("X-Trace", "one"), ("x-trace", "two")),
    ],
)
def test_request_rejects_multiline_or_case_insensitive_duplicate_headers(
    headers: tuple[tuple[str, str], ...],
) -> None:
    with pytest.raises(OutboundHttpRequestError, match="header"):
        OutboundHttpRequest(method=OutboundHttpMethod.GET, path="/items", headers=headers)


@pytest.mark.parametrize(
    "limits",
    [
        {"max_response_header_count": 0},
        {"max_response_header_wire_bytes": 0},
        {"max_chunk_bytes": 0},
        {"max_wire_bytes": 0},
        {"max_decoded_bytes": 0},
        {"max_compression_ratio": 0},
        {"max_response_header_count": 65},
        {"max_response_header_wire_bytes": 16_385},
    ],
)
def test_response_limits_reject_non_positive_or_header_cap_expansion(limits: dict[str, int]) -> None:
    with pytest.raises(OutboundHttpRequestError, match="response limit"):
        OutboundHttpResponseLimits(**limits)


@pytest.mark.parametrize(
    "limits",
    [
        {"max_response_header_count": math.nan},
        {"max_response_header_wire_bytes": math.nan},
        {"max_chunk_bytes": math.nan},
        {"max_wire_bytes": math.inf},
        {"max_decoded_bytes": math.nan},
        {"max_compression_ratio": math.inf},
        {"max_compression_ratio": math.nan},
    ],
)
def test_response_limits_reject_non_finite_values(limits: dict[str, float]) -> None:
    with pytest.raises(OutboundHttpRequestError, match="response limit"):
        OutboundHttpResponseLimits(**limits)


def test_response_limits_default_header_caps_are_fixed() -> None:
    limits = OutboundHttpResponseLimits()

    assert limits.max_response_header_count == 64
    assert limits.max_response_header_wire_bytes == 16_384


def test_failure_enum_is_exactly_the_frozen_contract() -> None:
    assert {item.value for item in OutboundHttpFailureKind} == {
        "POOL_TIMEOUT",
        "CONNECT_TIMEOUT",
        "CONNECT_ERROR",
        "WRITE_TIMEOUT",
        "WRITE_ERROR",
        "READ_TIMEOUT",
        "READ_ERROR",
        "REMOTE_PROTOCOL_ERROR",
        "TOTAL_TIMEOUT",
        "RESPONSE_HEADER_LIMIT_EXCEEDED",
        "RESPONSE_METADATA_INVALID",
        "RESPONSE_CHUNK_LIMIT_EXCEEDED",
        "RESPONSE_WIRE_LIMIT_EXCEEDED",
        "RESPONSE_DECODED_LIMIT_EXCEEDED",
        "RESPONSE_COMPRESSION_RATIO_EXCEEDED",
        "RESPONSE_CONTENT_ENCODING_UNSUPPORTED",
        "RESPONSE_CONTENT_ENCODING_INVALID",
        "RESPONSE_CLEANUP_FAILED",
    }


@pytest.mark.parametrize(
    "result",
    [
        OutboundHttpResult(
            delivery_state=OutboundHttpDeliveryState.NOT_SENT,
            failure_kind=OutboundHttpFailureKind.CONNECT_ERROR,
        ),
        OutboundHttpResult(
            delivery_state=OutboundHttpDeliveryState.DELIVERY_UNKNOWN,
            failure_kind=OutboundHttpFailureKind.WRITE_ERROR,
        ),
        OutboundHttpResult(
            delivery_state=OutboundHttpDeliveryState.RESPONSE_RECEIVED,
            status_code=503,
            response_headers=(("Set-Cookie", "one"), ("Set-Cookie", "two")),
            decoded_body=b"unavailable",
        ),
        OutboundHttpResult(
            delivery_state=OutboundHttpDeliveryState.RESPONSE_RECEIVED,
            status_code=200,
            failure_kind=OutboundHttpFailureKind.RESPONSE_WIRE_LIMIT_EXCEEDED,
        ),
    ],
)
def test_result_accepts_only_frozen_delivery_shapes(result: OutboundHttpResult) -> None:
    assert result.delivery_state in OutboundHttpDeliveryState


def test_result_rejects_delivery_state_outside_the_closed_enum() -> None:
    with pytest.raises(TypeError, match="delivery state"):
        OutboundHttpResult(
            delivery_state="SENT",  # type: ignore[arg-type]
            failure_kind=OutboundHttpFailureKind.CONNECT_ERROR,
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "delivery_state": OutboundHttpDeliveryState.RESPONSE_RECEIVED,
            "status_code": "200",
            "decoded_body": b"body",
        },
        {
            "delivery_state": OutboundHttpDeliveryState.NOT_SENT,
            "failure_kind": "CONNECT_ERROR",
        },
    ],
)
def test_result_rejects_values_outside_declared_field_types(kwargs: dict[str, object]) -> None:
    with pytest.raises(TypeError, match="result"):
        OutboundHttpResult(**kwargs)  # type: ignore[arg-type]


def test_result_rejects_decoded_body_outside_the_declared_bytes_type() -> None:
    with pytest.raises(TypeError, match="decoded body"):
        OutboundHttpResult(
            delivery_state=OutboundHttpDeliveryState.RESPONSE_RECEIVED,
            status_code=200,
            decoded_body=5,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "delivery_state": OutboundHttpDeliveryState.NOT_SENT,
            "status_code": 200,
            "failure_kind": OutboundHttpFailureKind.CONNECT_ERROR,
        },
        {
            "delivery_state": OutboundHttpDeliveryState.DELIVERY_UNKNOWN,
            "failure_kind": OutboundHttpFailureKind.CONNECT_ERROR,
        },
        {
            "delivery_state": OutboundHttpDeliveryState.RESPONSE_RECEIVED,
            "status_code": 200,
        },
        {
            "delivery_state": OutboundHttpDeliveryState.RESPONSE_RECEIVED,
            "status_code": 200,
            "decoded_body": b"body",
            "failure_kind": OutboundHttpFailureKind.READ_ERROR,
        },
        {
            "delivery_state": OutboundHttpDeliveryState.RESPONSE_RECEIVED,
            "status_code": 200,
            "failure_kind": OutboundHttpFailureKind.RESPONSE_HEADER_LIMIT_EXCEEDED,
            "response_headers": (("X-Partial", "present"),),
        },
    ],
)
def test_result_rejects_invalid_delivery_shapes(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="result"):
        OutboundHttpResult(**kwargs)  # type: ignore[arg-type]


def test_request_and_result_repr_redact_headers_and_body() -> None:
    request = OutboundHttpRequest(
        method=OutboundHttpMethod.POST,
        path="/items",
        headers=(("Authorization", "secret-header"),),
        body=b"secret-body",
    )
    result = OutboundHttpResult(
        delivery_state=OutboundHttpDeliveryState.RESPONSE_RECEIVED,
        status_code=200,
        response_headers=(("Set-Cookie", "secret-cookie"),),
        decoded_body=b"secret-response",
    )

    assert "secret-header" not in repr(request)
    assert "secret-body" not in repr(request)
    assert "secret-cookie" not in repr(result)
    assert "secret-response" not in repr(result)


def test_contract_module_does_not_import_httpx() -> None:
    source = Path("src/core/outbound_http/contracts.py").read_text(encoding="utf-8")

    assert "import httpx" not in source
