"""E03/E07 静态 typed WMS adapter。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel, ValidationError

from src.app.wms_adapter.client import OutboundHttpClosedError, WmsClient, WmsRequestBodyTooLargeError
from src.app.wms_adapter.strict_json import is_json_utf8_media_type
from src.app.wms_integration.ports.fulfillment_operations import (
    NotifyPkgBindingRequest,
    NotifyPkgBindingResult,
    validate_notify_pkg_binding_terminal_identity,
)
from src.app.wms_integration.ports.inventory_operations import (
    ConfirmInboundRequest,
    ConfirmInboundResult,
    validate_confirm_inbound_terminal_identity,
)
from src.app.wms_integration.ports.operation_common import validate_json_payload
from src.core.outbound_http import OutboundHttpDeliveryState

MAX_CONFIRMATION_BODY_BYTES = 256 * 1024
E03_CONFIRM_INBOUND = "wms.inventory.confirm_inbound@v1"
E07_NOTIFY_PKG_BINDING = "wms.fulfillment.notify_pkg_binding@v1"


class ExecutionConfirmationDispatchCode(str, Enum):
    DETERMINATE = "DETERMINATE"
    RETRY = "RETRY"
    NOT_SENT = "NOT_SENT"
    DELIVERY_UNKNOWN = "DELIVERY_UNKNOWN"
    RECONCILING = "RECONCILING"


@dataclass(frozen=True, slots=True)
class ExecutionConfirmationDispatchResult:
    code: ExecutionConfirmationDispatchCode
    normalized_response: dict[str, Any] | None = None
    response_result: str | None = None
    retry_after_ms: int | None = None
    follow_up_plan: None = None


@dataclass(frozen=True, slots=True)
class _OperationContract:
    path: str
    request_model: type[BaseModel]
    result_model: type[BaseModel]
    terminal_validator: Any


_CONTRACTS = {
    E03_CONFIRM_INBOUND: _OperationContract(
        path="/inventory/confirm-inbound",
        request_model=ConfirmInboundRequest,
        result_model=ConfirmInboundResult,
        terminal_validator=validate_confirm_inbound_terminal_identity,
    ),
    E07_NOTIFY_PKG_BINDING: _OperationContract(
        path="/fulfillment/pkg-bindings",
        request_model=NotifyPkgBindingRequest,
        result_model=NotifyPkgBindingResult,
        terminal_validator=validate_notify_pkg_binding_terminal_identity,
    ),
}


class WmsExecutionConfirmationAdapter:
    """只派发 E03/E07；endpoint 与 DTO 不从 profile 或 registry 发现。"""

    def __init__(self, client: WmsClient) -> None:
        self._client = client

    async def dispatch(  # noqa: PLR0911 - 每个传输事实都必须映射到显式可靠状态。
        self,
        *,
        operation: str,
        operation_id: str,
        request_payload: dict[str, Any],
        request_digest: str,
    ) -> ExecutionConfirmationDispatchResult:
        contract = _CONTRACTS.get(operation)
        if contract is None or _canonical_digest(request_payload) != request_digest:
            return ExecutionConfirmationDispatchResult(ExecutionConfirmationDispatchCode.RECONCILING)
        try:
            request = validate_json_payload(contract.request_model, request_payload)
        except (ValidationError, ValueError, TypeError):
            return ExecutionConfirmationDispatchResult(ExecutionConfirmationDispatchCode.RECONCILING)
        if getattr(request, "dispatch_key", None) != operation_id:
            return ExecutionConfirmationDispatchResult(ExecutionConfirmationDispatchCode.RECONCILING)

        try:
            access = await self._client.post(
                contract.path,
                json=request.model_dump(mode="json"),
                max_request_body_bytes=MAX_CONFIRMATION_BODY_BYTES,
                max_response_body_bytes=MAX_CONFIRMATION_BODY_BYTES,
            )
        except WmsRequestBodyTooLargeError:
            return ExecutionConfirmationDispatchResult(ExecutionConfirmationDispatchCode.RECONCILING)
        except OutboundHttpClosedError:
            return ExecutionConfirmationDispatchResult(ExecutionConfirmationDispatchCode.NOT_SENT)

        if access.delivery_state is OutboundHttpDeliveryState.NOT_SENT:
            return ExecutionConfirmationDispatchResult(ExecutionConfirmationDispatchCode.NOT_SENT)
        if access.delivery_state is not OutboundHttpDeliveryState.RESPONSE_RECEIVED:
            return ExecutionConfirmationDispatchResult(ExecutionConfirmationDispatchCode.DELIVERY_UNKNOWN)
        normalized = dict(access.json_body) if isinstance(access.json_body, dict) else None
        if access.status_code in {429, 503}:
            return ExecutionConfirmationDispatchResult(
                ExecutionConfirmationDispatchCode.RETRY,
                normalized_response=normalized,
            )
        if (
            access.status_code != 200
            or access.failure_kind is not None
            or access.json_failure is not None
            or not access.body_present
            or normalized is None
            or not _valid_json_headers(access.response_headers)
        ):
            return ExecutionConfirmationDispatchResult(
                ExecutionConfirmationDispatchCode.RECONCILING,
                normalized_response=normalized,
            )
        try:
            result = validate_json_payload(contract.result_model, normalized)
            if getattr(result, "dispatch_key", None) != operation_id:
                raise ValueError("response dispatch_key differs from request")
            contract.terminal_validator(request, result)
        except (ValidationError, ValueError, TypeError):
            return ExecutionConfirmationDispatchResult(
                ExecutionConfirmationDispatchCode.RECONCILING,
                normalized_response=normalized,
            )
        return ExecutionConfirmationDispatchResult(
            ExecutionConfirmationDispatchCode.DETERMINATE,
            normalized_response=result.model_dump(mode="json"),
            response_result="RECORDED",
        )


class WmsConfirmationTypedRouter:
    """显式区分 E03/E07 与粗分业务 wire；不做动态 operation 发现。"""

    def __init__(self, *, execution_adapter: WmsExecutionConfirmationAdapter, rough_sorter_adapter: Any) -> None:
        self._execution_adapter = execution_adapter
        self._rough_sorter_adapter = rough_sorter_adapter

    async def dispatch(self, **values: Any) -> Any:
        if values.get("operation") in _CONTRACTS:
            return await self._execution_adapter.dispatch(**values)
        return await self._rough_sorter_adapter.dispatch(**values)


def _canonical_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _valid_json_headers(headers: tuple[tuple[str, str], ...]) -> bool:
    content_types = [value for name, value in headers if name.casefold() == "content-type"]
    if len(content_types) != 1 or not is_json_utf8_media_type(content_types[0]):
        return False
    encodings = [value for name, value in headers if name.casefold() == "content-encoding"]
    return len(encodings) <= 1 and (not encodings or encodings[0].strip().casefold() == "identity")


__all__ = [
    "E03_CONFIRM_INBOUND",
    "E07_NOTIFY_PKG_BINDING",
    "MAX_CONFIRMATION_BODY_BYTES",
    "ExecutionConfirmationDispatchCode",
    "ExecutionConfirmationDispatchResult",
    "WmsConfirmationTypedRouter",
    "WmsExecutionConfirmationAdapter",
]
