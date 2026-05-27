from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pytest

from src.app.wms_integration.services import (
    WmsBusinessRejectedError,
    WmsCircuitOpenError,
    WmsEvidencePersistenceError,
    WmsIntegrationError,
    WmsTimeoutError,
    WmsUnavailableError,
)

CallerProtocol = Literal["runtime_hold", "business_rejected", "system_diagnostic"]


@dataclass(frozen=True)
class FakeCallerResult:
    protocol: CallerProtocol
    user_visible: bool
    runtime_hold_required: bool
    evidence: dict[str, object]


class FakeWmsCaller:
    """只验证业务 caller 的异常分类契约，不接入真实业务服务。"""

    def __init__(self, error: WmsIntegrationError) -> None:
        self.error = error

    def execute(self, *, request_id: str, trace_id: str) -> FakeCallerResult:
        try:
            raise self.error
        except WmsUnavailableError as exc:
            return FakeCallerResult(
                protocol="runtime_hold",
                user_visible=False,
                runtime_hold_required=True,
                evidence=_caller_evidence(exc, request_id=request_id, trace_id=trace_id),
            )
        except WmsBusinessRejectedError as exc:
            return FakeCallerResult(
                protocol="business_rejected",
                user_visible=True,
                runtime_hold_required=False,
                evidence=_caller_evidence(exc, request_id=request_id, trace_id=trace_id),
            )
        except WmsEvidencePersistenceError as exc:
            return FakeCallerResult(
                protocol="system_diagnostic",
                user_visible=False,
                runtime_hold_required=False,
                evidence=_caller_evidence(exc, request_id=request_id, trace_id=trace_id),
            )


def _caller_evidence(error: WmsIntegrationError, *, request_id: str, trace_id: str) -> dict[str, object]:
    return {
        "operation_name": error.operation_name,
        "evidence_key": error.evidence_key,
        "reason_code": error.reason_code,
        "http_status": error.http_status,
        "trace_id": trace_id,
        "request_id": request_id,
    }


@pytest.mark.parametrize(
    "error",
    [
        WmsTimeoutError(
            "WMS HTTP 调用超时",
            operation_name="query_inventory",
            evidence_key="ev:query_inventory:REQ-TIMEOUT",
            reason_code="WMS_TIMEOUT",
            retryable=True,
            target_code="WMS_INVENTORY",
        ),
        WmsCircuitOpenError(
            "WMS 熔断器已打开",
            operation_name="reserve_inventory",
            evidence_key="ev:reserve_inventory:REQ-CIRCUIT",
            reason_code="WMS_CIRCUIT_OPEN",
            retryable=True,
            target_code="WMS_INVENTORY",
        ),
        WmsUnavailableError(
            "WMS 依赖不可用",
            operation_name="confirm_outbound",
            evidence_key="ev:confirm_outbound:REQ-503",
            http_status=503,
            reason_code="WMS_DOWN",
            retryable=True,
            target_code="WMS_OUTBOUND",
        ),
    ],
)
def test_caller_contract_maps_unavailable_errors_to_runtime_hold_or_diagnostic_pause(
    error: WmsUnavailableError,
) -> None:
    result = FakeWmsCaller(error).execute(request_id="REQ-CALLER-001", trace_id="TRACE-CALLER-001")

    assert result.protocol == "runtime_hold"
    assert result.runtime_hold_required is True
    assert result.user_visible is False
    assert result.evidence == {
        "operation_name": error.operation_name,
        "evidence_key": error.evidence_key,
        "reason_code": error.reason_code,
        "http_status": error.http_status,
        "trace_id": "TRACE-CALLER-001",
        "request_id": "REQ-CALLER-001",
    }


def test_caller_contract_maps_business_rejected_to_user_visible_error_without_runtime_hold() -> None:
    error = WmsBusinessRejectedError(
        "库存不足",
        operation_name="reserve_inventory",
        evidence_key="ev:reserve_inventory:REQ-409",
        http_status=409,
        reason_code="WMS_INVENTORY_SHORTAGE",
        retryable=False,
        target_code="WMS_INVENTORY",
    )

    result = FakeWmsCaller(error).execute(request_id="REQ-CALLER-409", trace_id="TRACE-CALLER-409")

    assert result.protocol == "business_rejected"
    assert result.runtime_hold_required is False
    assert result.user_visible is True
    assert result.evidence == {
        "operation_name": "reserve_inventory",
        "evidence_key": "ev:reserve_inventory:REQ-409",
        "reason_code": "WMS_INVENTORY_SHORTAGE",
        "http_status": 409,
        "trace_id": "TRACE-CALLER-409",
        "request_id": "REQ-CALLER-409",
    }


def test_caller_contract_maps_evidence_persistence_error_to_system_diagnostic_not_wms_hold() -> None:
    error = WmsEvidencePersistenceError(
        "WMS 已返回成功，但本地 evidence/breaker 成功留痕失败",
        operation_name="confirm_inbound",
        evidence_key="ev:confirm_inbound:REQ-PERSIST",
        http_status=200,
        reason_code="WMS_EVIDENCE_PERSISTENCE_FAILED",
        retryable=False,
        target_code="WMS_INBOUND",
    )

    result = FakeWmsCaller(error).execute(request_id="REQ-CALLER-PERSIST", trace_id="TRACE-CALLER-PERSIST")

    assert result.protocol == "system_diagnostic"
    assert result.runtime_hold_required is False
    assert result.user_visible is False
    assert result.evidence == {
        "operation_name": "confirm_inbound",
        "evidence_key": "ev:confirm_inbound:REQ-PERSIST",
        "reason_code": "WMS_EVIDENCE_PERSISTENCE_FAILED",
        "http_status": 200,
        "trace_id": "TRACE-CALLER-PERSIST",
        "request_id": "REQ-CALLER-PERSIST",
    }
