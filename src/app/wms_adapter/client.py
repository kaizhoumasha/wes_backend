"""WMS 北向访问客户端。"""

from __future__ import annotations

import json as json_module
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, cast

from src.app.wms_adapter.strict_json import StrictJsonError, loads_strict_json
from src.core.outbound_http import (
    OutboundHttpClosedError,
    OutboundHttpDeliveryState,
    OutboundHttpFailureKind,
    OutboundHttpMethod,
    OutboundHttpRequest,
    OutboundHttpRequestError,
    OutboundHttpResponseLimits,
    OutboundHttpResult,
    OutboundHttpTransport,
)

type _JsonValue = None | bool | int | float | str | list[_JsonValue] | dict[str, _JsonValue]
type _JsonFailure = Literal["INVALID_UTF8", "INVALID_JSON"]

_MISSING = object()
_CLIENT_OWNED_REQUEST_HEADERS = frozenset({"host", "content-length", "transfer-encoding", "content-encoding"})


class WmsRequestBodyTooLargeError(OutboundHttpRequestError):
    """WMS 请求在发送前因编码后请求体超限被拒绝。"""


@dataclass(frozen=True, slots=True)
class WmsAccessResult:
    """保留传输事实并提供最小 JSON 解码结果。"""

    delivery_state: OutboundHttpDeliveryState
    failure_kind: OutboundHttpFailureKind | None
    status_code: int | None
    response_headers: tuple[tuple[str, str], ...] = field(repr=False)
    body_present: bool
    json_body: _JsonValue = field(repr=False)
    json_failure: _JsonFailure | None


class WmsClient:
    """基于基础 Transport 的 WMS HTTP 薄客户端。"""

    def __init__(self, transport: OutboundHttpTransport) -> None:
        self._transport = transport

    async def request(
        self,
        method: OutboundHttpMethod,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        json: object = _MISSING,
        encoded_json: bytes | object = _MISSING,
        max_request_body_bytes: int | None = None,
        max_response_body_bytes: int | None = None,
    ) -> WmsAccessResult:
        """发送一次 GET/POST 请求，不解释任何 WMS 业务语义。"""

        request_headers = _as_pairs(headers, field_name="headers")
        if any(isinstance(name, str) and name.casefold() == "content-type" for name, _ in request_headers):
            raise OutboundHttpRequestError("Content-Type is owned by WmsClient")
        if any(
            isinstance(name, str) and name.casefold() in _CLIENT_OWNED_REQUEST_HEADERS for name, _ in request_headers
        ):
            raise OutboundHttpRequestError("request header is owned by WmsClient")

        if method is OutboundHttpMethod.GET:
            if json is not _MISSING or encoded_json is not _MISSING:
                raise OutboundHttpRequestError("GET must not contain JSON")
            body = b""
        elif method is OutboundHttpMethod.POST:
            if (json is _MISSING) == (encoded_json is _MISSING):
                raise OutboundHttpRequestError("POST requires exactly one JSON body source")
            if encoded_json is not _MISSING:
                if not isinstance(encoded_json, bytes):
                    raise TypeError("encoded_json must be bytes")
                body = encoded_json
            else:
                body = _encode_json(json)
            request_headers += (("Content-Type", "application/json"),)
        else:
            raise TypeError("method must be an OutboundHttpMethod")

        _validate_optional_byte_limit(max_request_body_bytes, "max_request_body_bytes")
        _validate_optional_byte_limit(max_response_body_bytes, "max_response_body_bytes")
        if max_request_body_bytes is not None and len(body) > max_request_body_bytes:
            raise WmsRequestBodyTooLargeError("request body limit exceeded")
        response_limits = (
            OutboundHttpResponseLimits(
                max_wire_bytes=max_response_body_bytes,
                max_decoded_bytes=max_response_body_bytes,
            )
            if max_response_body_bytes is not None
            else OutboundHttpResponseLimits()
        )

        result = await self._transport.send(
            OutboundHttpRequest(
                method=method,
                path=path,
                query=_as_pairs(query, field_name="query"),
                headers=request_headers,
                body=body,
                response_limits=response_limits,
            )
        )
        return _decode_result(result)

    async def get(
        self,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        max_response_body_bytes: int | None = None,
    ) -> WmsAccessResult:
        """发送一次无 JSON 请求体的 GET。"""

        return await self.request(
            method=OutboundHttpMethod.GET,
            path=path,
            query=query,
            headers=headers,
            max_response_body_bytes=max_response_body_bytes,
        )

    async def post(
        self,
        path: str,
        *,
        json: _JsonValue,
        query: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        max_request_body_bytes: int | None = None,
        max_response_body_bytes: int | None = None,
    ) -> WmsAccessResult:
        """发送一次由 Client 统一编码 JSON 的 POST。"""

        return await self.request(
            method=OutboundHttpMethod.POST,
            path=path,
            query=query,
            headers=headers,
            json=json,
            max_request_body_bytes=max_request_body_bytes,
            max_response_body_bytes=max_response_body_bytes,
        )

    async def post_json_bytes(
        self,
        path: str,
        *,
        body: bytes,
        query: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        max_request_body_bytes: int | None = None,
        max_response_body_bytes: int | None = None,
    ) -> WmsAccessResult:
        """发送已经冻结的 JSON 请求体，不重新序列化。"""

        return await self.request(
            method=OutboundHttpMethod.POST,
            path=path,
            query=query,
            headers=headers,
            encoded_json=body,
            max_request_body_bytes=max_request_body_bytes,
            max_response_body_bytes=max_response_body_bytes,
        )

    async def aclose(self) -> None:
        """释放底层 Transport 持有的资源。"""

        await self._transport.aclose()


def _as_pairs(values: Mapping[str, str] | None, *, field_name: str) -> tuple[tuple[str, str], ...]:
    if values is None:
        return ()
    if not isinstance(values, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    return tuple(values.items())


def _validate_optional_byte_limit(value: int | None, field_name: str) -> None:
    if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value <= 0):
        raise OutboundHttpRequestError(f"{field_name} must be a positive integer")


def _encode_json(value: object) -> bytes:
    try:
        _validate_json(value, active_container_ids=set())
        encoded = json_module.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    except RecursionError as error:
        raise ValueError("JSON value exceeds the supported nesting depth") from error
    return encoded.encode("utf-8")


def _validate_json(value: object, *, active_container_ids: set[int]) -> None:
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON number must be finite")
        return
    if not isinstance(value, (list, dict)):
        raise TypeError("JSON value must contain only null, booleans, numbers, strings, lists, and objects")

    container_id = id(value)
    if container_id in active_container_ids:
        raise ValueError("JSON value must not contain circular containers")
    active_container_ids.add(container_id)
    try:
        if isinstance(value, list):
            for item in value:
                _validate_json(item, active_container_ids=active_container_ids)
        else:
            for key, item in value.items():
                if not isinstance(key, str):
                    raise TypeError("JSON object keys must be strings")
                _validate_json(item, active_container_ids=active_container_ids)
    finally:
        active_container_ids.remove(container_id)


def _decode_result(result: OutboundHttpResult) -> WmsAccessResult:
    body = result.decoded_body
    if body is None or body == b"":
        return _as_access_result(result, body_present=False, json_body=None, json_failure=None)

    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return _as_access_result(result, body_present=True, json_body=None, json_failure="INVALID_UTF8")

    try:
        json_body = cast("_JsonValue", loads_strict_json(text))
    except StrictJsonError:
        return _as_access_result(result, body_present=True, json_body=None, json_failure="INVALID_JSON")
    return _as_access_result(result, body_present=True, json_body=json_body, json_failure=None)


def _as_access_result(
    result: OutboundHttpResult,
    *,
    body_present: bool,
    json_body: _JsonValue,
    json_failure: _JsonFailure | None,
) -> WmsAccessResult:
    return WmsAccessResult(
        delivery_state=result.delivery_state,
        failure_kind=result.failure_kind,
        status_code=result.status_code,
        response_headers=result.response_headers,
        body_present=body_present,
        json_body=json_body,
        json_failure=json_failure,
    )


__all__ = ["OutboundHttpClosedError", "WmsAccessResult", "WmsClient", "WmsRequestBodyTooLargeError"]
