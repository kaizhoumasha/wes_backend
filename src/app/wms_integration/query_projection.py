"""WMS QUERY 的统一 typed request projection。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.app.wms_integration.operation_contract import WmsHttpMethod, WmsOperationMode

if TYPE_CHECKING:
    from pydantic import BaseModel

    from src.app.wms_integration.endpoint_compiler import CompiledWmsOperationEndpoint
    from src.app.wms_integration.operation_contract import WmsOperationDefinition


@dataclass(frozen=True, slots=True)
class WmsQueryRequestProjection:
    """单次 QUERY 的冻结 wire projection 与低敏 evidence 摘要。"""

    operation_identity: str
    method: str
    url: str
    path_field_names: tuple[str, ...]
    query_params: tuple[tuple[str, str], ...]
    json_body: dict[str, Any] | None
    request_canonical_hash: str
    evidence_snapshot: dict[str, Any]


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _query_scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str | int):
        return str(value)
    raise TypeError("GET QUERY projection only accepts typed scalar or tuple fields")


def _query_params(payload: dict[str, Any], *, excluded_fields: frozenset[str]) -> tuple[tuple[str, str], ...]:
    pairs: list[tuple[str, str]] = []
    for field_name, value in payload.items():
        if field_name in excluded_fields:
            continue
        if isinstance(value, list | tuple):
            pairs.extend((field_name, _query_scalar(item)) for item in value)
            continue
        pairs.append((field_name, _query_scalar(value)))
    return tuple(pairs)


def _presence_summary(value: object) -> dict[str, int | bool]:
    text = str(value)
    return {"present": True, "length": len(text)}


def _post_query_evidence(payload: dict[str, Any], request_canonical_hash: str) -> dict[str, Any]:
    six_in_one = payload.get("six_in_one")
    if not isinstance(six_in_one, dict):
        raise TypeError("POST QUERY six_in_one projection must be an object")
    return {
        "station_code": payload.get("station_code"),
        "workline_id": payload.get("workline_id"),
        "session_id": payload.get("session_id"),
        "correlation_id": payload.get("correlation_id"),
        "raw_code": _presence_summary(payload["raw_code"]),
        "six_in_one": {field_name: _presence_summary(value) for field_name, value in six_in_one.items()},
        "measurement": {
            "reel_diameter_mm_present": payload.get("reel_diameter_mm") is not None,
            "reel_thickness_mm_present": payload.get("reel_thickness_mm") is not None,
        },
        "request_canonical_hash": request_canonical_hash,
    }


def project_wms_query_request(
    *,
    operation: WmsOperationDefinition,
    endpoint: CompiledWmsOperationEndpoint,
    request: BaseModel,
) -> WmsQueryRequestProjection:
    """只按静态 Definition 与 compiled endpoint 投影 GET params 或 POST JSON body。"""

    if operation.mode is not WmsOperationMode.QUERY or endpoint.mode is not WmsOperationMode.QUERY:
        raise ValueError("QUERY projection requires QUERY operation semantics")
    if endpoint.identity != operation.identity:
        raise ValueError("operation and compiled endpoint identity mismatch")
    if (
        endpoint.request_model is not operation.request_model
        or endpoint.result_model is not operation.result_model
        or endpoint.http_method is not operation.http_method
    ):
        raise ValueError("compiled endpoint semantics differ from static QUERY definition")
    if not isinstance(request, operation.request_model):
        raise TypeError("QUERY projection requires its operation-specific typed request")

    payload = request.model_dump(mode="json", exclude_none=True)
    request_canonical_hash = _canonical_hash(payload)
    url = endpoint.render_endpoint(request)
    if operation.http_method is WmsHttpMethod.GET:
        query_params = _query_params(payload, excluded_fields=frozenset(endpoint.placeholder_names))
        json_body = None
        evidence_snapshot = {
            "request_canonical_hash": request_canonical_hash,
            "request": payload,
        }
    else:
        query_params = ()
        json_body = payload
        evidence_snapshot = _post_query_evidence(payload, request_canonical_hash)
    return WmsQueryRequestProjection(
        operation_identity=operation.identity,
        method=operation.http_method.value,
        url=url,
        path_field_names=endpoint.placeholder_names,
        query_params=query_params,
        json_body=json_body,
        request_canonical_hash=request_canonical_hash,
        evidence_snapshot=evidence_snapshot,
    )


__all__ = ["WmsQueryRequestProjection", "project_wms_query_request"]
