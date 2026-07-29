"""WMS QUERY 共用的有界响应解码、JSON 校验与 HTTP 分类。"""

from __future__ import annotations

import json
import zlib
from typing import TYPE_CHECKING, Any

from src.app.wms_integration.ports.query_outcome import QueryBusinessReject, QueryTechnicalFailure

if TYPE_CHECKING:
    from collections.abc import Mapping

    from src.app.wms_integration.operation_contract import WmsOperationBudget


class QueryBudgetViolation(Exception):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message


class MalformedProviderResponse(Exception):
    pass


class ContentEncodingFailure(Exception):
    pass


def decode_bounded_body(
    raw_body: bytes,
    *,
    content_encoding: str,
    allowed_content_encodings: tuple[str, ...],
    max_decoded_bytes: int,
    max_compression_ratio: float,
) -> bytes:
    encoding = content_encoding.strip().lower() or "identity"
    if encoding not in allowed_content_encodings:
        raise QueryBudgetViolation(
            "WMS_UNSUPPORTED_CONTENT_ENCODING",
            f"unsupported WMS content encoding: {encoding}",
        )
    if max_decoded_bytes <= 0:
        raise QueryBudgetViolation("WMS_DECODED_BUDGET_EXCEEDED", "WMS QUERY decoded budget exceeded")
    if encoding == "identity":
        decoded = raw_body
    else:
        window_bits = 16 + zlib.MAX_WBITS if encoding == "gzip" else zlib.MAX_WBITS
        decoder = zlib.decompressobj(window_bits)
        try:
            decoded = decoder.decompress(raw_body, max_decoded_bytes + 1)
            if decoder.unconsumed_tail or len(decoded) > max_decoded_bytes:
                raise QueryBudgetViolation("WMS_DECODED_BUDGET_EXCEEDED", "WMS QUERY decoded budget exceeded")
            decoded += decoder.flush(max_decoded_bytes - len(decoded) + 1)
        except zlib.error as exc:
            raise ContentEncodingFailure from exc
        if not decoder.eof or decoder.unused_data:
            raise ContentEncodingFailure
    if len(decoded) > max_decoded_bytes:
        raise QueryBudgetViolation("WMS_DECODED_BUDGET_EXCEEDED", "WMS QUERY decoded budget exceeded")
    if raw_body and len(decoded) / len(raw_body) > max_compression_ratio:
        raise QueryBudgetViolation(
            "WMS_COMPRESSION_RATIO_EXCEEDED",
            "WMS QUERY compression ratio budget exceeded",
        )
    return decoded


def parse_bounded_json(decoded_body: bytes, *, max_depth: int, max_field_length: int) -> Any:
    parsed = json.loads(decoded_body)
    _validate_json_structure(parsed, max_depth=max_depth, max_field_length=max_field_length)
    return parsed


def _validate_json_structure(value: Any, *, max_depth: int, max_field_length: int, depth: int = 1) -> None:
    if depth > max_depth:
        raise QueryBudgetViolation("WMS_JSON_DEPTH_EXCEEDED", "WMS QUERY JSON depth budget exceeded")
    if isinstance(value, str):
        if len(value) > max_field_length:
            raise QueryBudgetViolation(
                "WMS_JSON_FIELD_LENGTH_EXCEEDED",
                "WMS QUERY JSON field length budget exceeded",
            )
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if len(str(key)) > max_field_length:
                raise QueryBudgetViolation(
                    "WMS_JSON_FIELD_LENGTH_EXCEEDED",
                    "WMS QUERY JSON field length budget exceeded",
                )
            _validate_json_structure(item, max_depth=max_depth, max_field_length=max_field_length, depth=depth + 1)
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_structure(item, max_depth=max_depth, max_field_length=max_field_length, depth=depth + 1)


def classify_http_failure(  # noqa: PLR0911 - HTTP 状态封闭矩阵保持逐分支可审计。
    status_code: int,
    payload: Any,
    headers: Mapping[str, str],
) -> QueryBusinessReject | QueryTechnicalFailure | None:
    if 200 <= status_code < 300:
        return None
    if status_code == 408:
        return QueryTechnicalFailure("WMS_PROVIDER_TIMEOUT", "WMS QUERY provider timed out the request", True)
    if status_code == 429:
        return QueryTechnicalFailure(
            "WMS_RATE_LIMITED",
            "WMS rate limited the QUERY request",
            True,
            _parse_retry_after(headers.get("retry-after")),
        )
    if status_code == 401:
        return QueryTechnicalFailure("WMS_AUTHENTICATION_FAILED", "WMS QUERY authentication failed", False)
    if status_code == 403:
        return QueryTechnicalFailure("WMS_AUTHORIZATION_FAILED", "WMS QUERY authorization failed", False)
    if 400 <= status_code < 500:
        if isinstance(payload, dict) and payload.get("classification") == "BUSINESS_REJECT":
            reason_code = _payload_text(payload, "reason_code", "")
            if reason_code:
                return QueryBusinessReject(
                    reason_code=reason_code,
                    message=_payload_text(payload, "message", "WMS rejected the QUERY request"),
                )
        return QueryTechnicalFailure(
            "WMS_PROVIDER_CLIENT_ERROR",
            "WMS QUERY provider returned a client error",
            False,
        )
    if status_code in {500, 502, 503, 504}:
        return QueryTechnicalFailure("WMS_UNAVAILABLE", "WMS QUERY service unavailable", True)
    return QueryTechnicalFailure(
        "WMS_UNEXPECTED_HTTP_STATUS",
        "WMS QUERY provider returned an unexpected HTTP status",
        False,
    )


def parse_optional_failure_body(
    raw_body: bytes,
    *,
    content_encoding: str,
    budget: WmsOperationBudget,
) -> Any | None:
    """4xx body 仅用于识别显式业务拒绝；任何解析失败都保持 technical。"""

    if not raw_body:
        return None
    try:
        decoded = decode_bounded_body(
            raw_body,
            content_encoding=content_encoding,
            allowed_content_encodings=budget.allowed_content_encodings,
            max_decoded_bytes=budget.max_decoded_bytes,
            max_compression_ratio=budget.max_compression_ratio,
        )
        return parse_bounded_json(
            decoded,
            max_depth=budget.max_json_depth,
            max_field_length=budget.max_field_length,
        )
    except (
        ContentEncodingFailure,
        MalformedProviderResponse,
        QueryBudgetViolation,
        UnicodeDecodeError,
        ValueError,
    ):
        return None


def _payload_text(payload: Any, key: str, default: str) -> str:
    if not isinstance(payload, dict):
        return default
    value = payload.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else default


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


__all__ = [
    "ContentEncodingFailure",
    "MalformedProviderResponse",
    "QueryBudgetViolation",
    "classify_http_failure",
    "decode_bounded_body",
    "parse_bounded_json",
    "parse_optional_failure_body",
]
