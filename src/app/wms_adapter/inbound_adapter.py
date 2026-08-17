"""粗分机 WMS 请求适配器。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any, cast

from pydantic import ValidationError

from src.app.execution.services.wms_confirmation_service import (
    WmsBusinessWaitFollowUp,
    WmsConfirmationFollowUpPlan,
)
from src.app.wms_adapter.client import (
    OutboundHttpClosedError,
    WmsClient,
    WmsRequestBodyTooLargeError,
)
from src.app.wms_adapter.inbound_wire import (
    DECISION_OPERATIONS,
    DECISION_PATH,
    FACT_OPERATIONS,
    FACT_PATH,
    MAX_INBOUND_BODY_BYTES,
    parse_outbound_request,
    parse_outbound_response,
)
from src.app.wms_adapter.strict_json import is_json_utf8_media_type
from src.core.outbound_http import OutboundHttpDeliveryState
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import Callable

    from src.app.execution.models.wms_confirmation import WmsConfirmation


class InboundDispatchCode(str, Enum):
    DETERMINATE = "DETERMINATE"
    RETRY = "RETRY"
    NOT_SENT = "NOT_SENT"
    DELIVERY_UNKNOWN = "DELIVERY_UNKNOWN"
    RECONCILING = "RECONCILING"


@dataclass(frozen=True, slots=True)
class InboundDispatchResult:
    code: InboundDispatchCode
    normalized_response: dict[str, Any] | None = None
    response_result: str | None = None
    retry_after_ms: int | None = None
    follow_up_plan: WmsConfirmationFollowUpPlan | None = None


class WmsInboundAdapter:
    """校验不可变请求，调用共享 WmsClient，并解释粗分响应。"""

    def __init__(self, client: WmsClient) -> None:
        self._client = client

    async def dispatch(  # noqa: PLR0911 - 每个 fail-closed 分支保留明确传输语义。
        self,
        *,
        operation: str,
        operation_id: str,
        request_payload: dict[str, Any],
        request_digest: str,
    ) -> InboundDispatchResult:
        try:
            request = parse_outbound_request(request_payload)
        except (ValidationError, ValueError, TypeError):
            return InboundDispatchResult(InboundDispatchCode.RECONCILING)
        if (
            request.operation != operation
            or request.operation_id != operation_id
            or _canonical_digest(request_payload) != request_digest
        ):
            return InboundDispatchResult(InboundDispatchCode.RECONCILING)
        if operation in DECISION_OPERATIONS:
            path = DECISION_PATH
        elif operation in FACT_OPERATIONS:
            path = FACT_PATH
        else:
            return InboundDispatchResult(InboundDispatchCode.RECONCILING)

        try:
            access = await self._client.post(
                path,
                json=request.model_dump(mode="json", exclude_none=True),
                max_request_body_bytes=MAX_INBOUND_BODY_BYTES,
                max_response_body_bytes=MAX_INBOUND_BODY_BYTES,
            )
        except WmsRequestBodyTooLargeError:
            return InboundDispatchResult(InboundDispatchCode.RECONCILING)
        except OutboundHttpClosedError:
            return InboundDispatchResult(InboundDispatchCode.NOT_SENT)

        if access.delivery_state is OutboundHttpDeliveryState.NOT_SENT:
            return InboundDispatchResult(InboundDispatchCode.NOT_SENT)
        if access.delivery_state is not OutboundHttpDeliveryState.RESPONSE_RECEIVED:
            return InboundDispatchResult(InboundDispatchCode.DELIVERY_UNKNOWN)
        received_json = dict(access.json_body) if isinstance(access.json_body, dict) else None
        if access.status_code in {400, 413} and not access.body_present:
            return InboundDispatchResult(InboundDispatchCode.RECONCILING)
        if access.failure_kind is not None or not _valid_json_headers(access.response_headers):
            return InboundDispatchResult(InboundDispatchCode.RECONCILING, normalized_response=received_json)
        if access.json_failure is not None or not access.body_present or not isinstance(access.json_body, dict):
            return InboundDispatchResult(InboundDispatchCode.RECONCILING)
        try:
            response = parse_outbound_response(operation, access.status_code or 0, access.json_body)
        except (ValidationError, ValueError, TypeError):
            return InboundDispatchResult(InboundDispatchCode.RECONCILING, normalized_response=received_json)
        normalized = response.model_dump(mode="json")
        if response.operation_id != operation_id:
            return InboundDispatchResult(InboundDispatchCode.RECONCILING, normalized_response=normalized)

        if response.code in {"DECIDED", "RECORDED", "DUPLICATE"}:
            response_result = response.data.result if response.code == "DECIDED" else response.code
            return InboundDispatchResult(
                InboundDispatchCode.DETERMINATE,
                normalized_response=normalized,
                response_result=response_result,
                follow_up_plan=(
                    WmsConfirmationFollowUpPlan(retry_after_ms=response.data.retry_after_ms)
                    if response_result == "WAIT"
                    else None
                ),
            )
        if response.code == "BUSY":
            return InboundDispatchResult(
                InboundDispatchCode.RETRY,
                normalized_response=normalized,
                retry_after_ms=response.data.retry_after_ms,
            )
        if response.code == "UNAVAILABLE":
            return InboundDispatchResult(InboundDispatchCode.RETRY, normalized_response=normalized)
        return InboundDispatchResult(InboundDispatchCode.RECONCILING, normalized_response=normalized)


class WmsInboundBusinessWaitPlanner:
    """把已验证的粗分 WAIT 响应转换为新的可靠请求身份。"""

    def __init__(
        self,
        *,
        operation_id_factory: Callable[[], str] = new_uuid7,
    ) -> None:
        self._operation_id_factory = operation_id_factory

    def plan(
        self,
        confirmation: WmsConfirmation,
        planning: WmsConfirmationFollowUpPlan,
    ) -> WmsBusinessWaitFollowUp | None:
        retry_after_ms = planning.retry_after_ms
        if not isinstance(retry_after_ms, int) or isinstance(retry_after_ms, bool) or retry_after_ms <= 0:
            return None
        received_at = confirmation.completed_at
        if received_at is None:
            return None
        operation_id = self._operation_id_factory()
        request_payload = cast(
            "dict[str, object]",
            json.loads(json.dumps(confirmation.request_payload, ensure_ascii=False, separators=(",", ":"))),
        )
        request_payload["operation_id"] = operation_id
        request_payload["timestamp"] = int(timezone.to_utc(received_at).timestamp() * 1000)
        return WmsBusinessWaitFollowUp(
            operation=confirmation.operation,
            operation_id=operation_id,
            request_payload=request_payload,
            next_attempt_at=received_at + timedelta(milliseconds=retry_after_ms),
        )


def _canonical_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _valid_json_headers(headers: tuple[tuple[str, str], ...]) -> bool:
    content_types = [value for name, value in headers if name.casefold() == "content-type"]
    if len(content_types) != 1 or not is_json_utf8_media_type(content_types[0]):
        return False
    encodings = [value for name, value in headers if name.casefold() == "content-encoding"]
    return len(encodings) <= 1 and (not encodings or encodings[0].strip().casefold() == "identity")


__all__ = ["InboundDispatchCode", "InboundDispatchResult", "WmsInboundAdapter", "WmsInboundBusinessWaitPlanner"]
