"""WMS 履约域 E07–E16 typed contracts 与静态 Definitions。"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from src.app.wms_integration.operation_contract import (
    WmsCompletionMode,
    WmsDomainProjectionKind,
    WmsExecutionLane,
    effect_operation,
)
from src.app.wms_integration.ports.operation_common import (
    EffectRequest,
    EffectResult,
    NonNegativeDecimal,
    StableText,
    StrictWmsModel,
)


class NotifyPkgBindingRequest(EffectRequest):
    pkg_id: StableText = Field(max_length=160)
    bin_id: StableText = Field(max_length=120)
    slot_id: StableText = Field(max_length=120)
    rack_id: StableText = Field(max_length=120)
    station_code: StableText = Field(max_length=120)


class NotifyPkgBindingResult(EffectResult):
    pkg_id: StableText = Field(max_length=160)
    binding_reference: StableText = Field(max_length=160)


def validate_notify_pkg_binding_terminal_identity(
    request: NotifyPkgBindingRequest,
    result: NotifyPkgBindingResult,
) -> None:
    if request.pkg_id != result.pkg_id:
        raise ValueError("package binding terminal identity differs from request")


class RequestRackSupplyRequest(EffectRequest):
    station_code: StableText = Field(max_length=120)
    rack_type: StableText = Field(max_length=80)
    demand_generation: int = Field(ge=1)


class RequestRackSupplyResult(EffectResult):
    station_code: StableText = Field(max_length=120)
    rack_type: StableText = Field(max_length=80)
    demand_generation: int = Field(ge=1)
    rack_id: StableText = Field(max_length=120)
    final_station_code: StableText = Field(max_length=120)
    arrival_relation: StableText = Field(max_length=120)
    task_outcome: Literal["SUCCESS", "FAILED_AFTER_EXECUTION"]


class RequestRackTransportRequest(EffectRequest):
    rack_id: StableText = Field(max_length=120)
    source_location_code: StableText = Field(max_length=120)
    destination_station_code: StableText = Field(max_length=120)


class RequestRackTransportResult(EffectResult):
    rack_id: StableText = Field(max_length=120)
    source_location_code: StableText = Field(max_length=120)
    destination_station_code: StableText = Field(max_length=120)
    final_location_code: StableText = Field(max_length=120)
    task_outcome: Literal["SUCCESS", "FAILED_AFTER_EXECUTION"]


class ChangeRackFaceRequest(EffectRequest):
    rack_id: StableText = Field(max_length=120)
    station_code: StableText = Field(max_length=120)
    requested_face: Literal["A", "B"]


class ChangeRackFaceResult(EffectResult):
    rack_id: StableText = Field(max_length=120)
    authorized_face: Literal["A", "B"]
    final_face: Literal["A", "B"]
    task_outcome: Literal["SUCCESS", "FAILED_AFTER_EXECUTION"]


class FrozenBinCellOccupancy(StrictWmsModel):
    occupancy_id: StableText = Field(max_length=160)
    pkg_id: StableText = Field(max_length=160)
    material_code: StableText = Field(max_length=120)
    quantity: NonNegativeDecimal


type AsyncFulfillmentOperationIdentity = StableText

ASYNC_FULFILLMENT_OPERATION_IDENTITIES: frozenset[str]


class WmsEffectAck(StrictWmsModel):
    """异步单对象履约的 ACK。"""

    operation_identity: AsyncFulfillmentOperationIdentity
    idempotency_key: StableText = Field(max_length=160)
    provider_reference: StableText = Field(max_length=160)
    submission_state: Literal["ACCEPTED", "IN_PROGRESS_REPLAY", "REPLAY"]

    @model_validator(mode="after")
    def validate_operation_identity(self) -> WmsEffectAck:
        if self.operation_identity not in ASYNC_FULFILLMENT_OPERATION_IDENTITIES:
            raise ValueError("ACK operation_identity is not an authored async fulfillment operation")
        return self


class WmsAsyncSubmitReject(StrictWmsModel):
    """异步履约在远端任务创建前返回的共享业务拒绝信封。"""

    operation_identity: AsyncFulfillmentOperationIdentity
    idempotency_key: StableText = Field(max_length=160)
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason_code: StableText = Field(max_length=120)
    message: StableText = Field(max_length=500)


def validate_effect_ack(*, operation_identity: str, idempotency_key: str, ack: WmsEffectAck) -> WmsEffectAck:
    """校验 status 查询与冻结 ACK 的公共关联身份。"""

    if ack.operation_identity != operation_identity:
        raise ValueError("ACK operation_identity differs from status request")
    if ack.idempotency_key != idempotency_key:
        raise ValueError("ACK idempotency_key differs from status request")
    return ack


def validate_effect_provider_reference(
    ack: WmsEffectAck, provider_reference: str, *, evidence_kind: Literal["status", "terminal"]
) -> None:
    """用 ACK 唯一冻结的 provider reference 拒绝 status/terminal 串单。"""

    if provider_reference != ack.provider_reference:
        raise ValueError(f"{evidence_kind} provider_reference does not match ACK")


class RequestLoadUnitTransportRequest(EffectRequest):
    load_unit_id: StableText = Field(max_length=160)
    load_unit_type: Literal["PALLET", "MAGAZINE", "OTHER"]
    source_location_code: StableText = Field(max_length=120)
    destination_location_code: StableText = Field(max_length=120)


class RequestLoadUnitTransportResult(EffectResult):
    load_unit_id: StableText = Field(max_length=160)
    load_unit_type: Literal["PALLET", "MAGAZINE", "OTHER"]
    final_location_code: StableText = Field(max_length=120)
    task_outcome: Literal["SUCCESS", "FAILED_AFTER_EXECUTION"]


class PublishManualTaskRequest(EffectRequest):
    manual_task_key: StableText = Field(max_length=160)
    task_type: StableText = Field(max_length=120)
    object_keys: tuple[StableText, ...] = Field(min_length=1)
    station_code: StableText = Field(max_length=120)
    instructions: StableText = Field(max_length=2_000)


class PublishManualTaskResult(EffectResult):
    manual_task_key: StableText = Field(max_length=160)
    manual_task_reference: StableText = Field(max_length=160)
    publish_status: Literal["PUBLISHED"]


def validate_publish_manual_task_terminal_identity(
    request: PublishManualTaskRequest,
    result: PublishManualTaskResult,
) -> None:
    if request.manual_task_key != result.manual_task_key:
        raise ValueError("manual task terminal identity differs from request")


class CancelRequestRequest(EffectRequest):
    target_operation_identity: StableText = Field(max_length=160)
    target_idempotency_key: StableText = Field(max_length=160)
    target_provider_reference: StableText = Field(max_length=160)
    cancellation_reason: StableText = Field(max_length=240)


class CancelRequestResult(EffectResult):
    target_operation_identity: StableText = Field(max_length=160)
    target_idempotency_key: StableText = Field(max_length=160)
    target_provider_reference: StableText = Field(max_length=160)
    disposition: Literal["CANCELLED", "ALREADY_TERMINAL", "TOO_LATE"]


def validate_cancel_terminal_result(
    request: CancelRequestRequest,
    result: CancelRequestResult,
) -> CancelRequestResult:
    """E16 裁决必须回显同一取消目标，不得跨 provider task 串单。"""

    if not isinstance(request, CancelRequestRequest) or not isinstance(result, CancelRequestResult):
        raise TypeError("E16 request requires E16 terminal result")
    for field_name in (
        "target_operation_identity",
        "target_idempotency_key",
        "target_provider_reference",
    ):
        if getattr(request, field_name) != getattr(result, field_name):
            raise ValueError(f"E16 terminal {field_name} differs from request")
    return result


NOTIFY_PKG_BINDING = effect_operation(
    identity="wms.fulfillment.notify_pkg_binding@v1",
    request_model=NotifyPkgBindingRequest,
    result_model=NotifyPkgBindingResult,
    path_template="/fulfillment/pkg-bindings",
    target_code="WMS_FULFILLMENT_NOTIFY_PKG_BINDING",
    reject_codes=("PACKAGE_NOT_FOUND", "BIN_NOT_FOUND", "SLOT_OCCUPIED"),
    completion_mode=WmsCompletionMode.SYNC_RESULT,
    execution_lane=WmsExecutionLane.WMS_DATA,
    terminal_identity_validator=validate_notify_pkg_binding_terminal_identity,
)
REQUEST_RACK_SUPPLY = effect_operation(
    identity="wms.fulfillment.request_rack_supply@v1",
    request_model=RequestRackSupplyRequest,
    result_model=RequestRackSupplyResult,
    path_template="/fulfillment/rack-supply",
    target_code="WMS_FULFILLMENT_REQUEST_RACK_SUPPLY",
    reject_codes=("NO_RACK_AVAILABLE", "STATION_BLOCKED", "DEMAND_CONFLICT"),
    completion_mode=WmsCompletionMode.ASYNC_TASK,
    execution_lane=WmsExecutionLane.WMS_FULFILLMENT,
    domain_projection_kind=WmsDomainProjectionKind.RACK_SUPPLY_DEMAND,
)
REQUEST_RACK_TRANSPORT = effect_operation(
    identity="wms.fulfillment.request_rack_transport@v1",
    request_model=RequestRackTransportRequest,
    result_model=RequestRackTransportResult,
    path_template="/fulfillment/rack-transport",
    target_code="WMS_FULFILLMENT_REQUEST_RACK_TRANSPORT",
    reject_codes=("RACK_NOT_FOUND", "RACK_LOCKED", "DESTINATION_BLOCKED"),
    completion_mode=WmsCompletionMode.ASYNC_TASK,
    execution_lane=WmsExecutionLane.WMS_FULFILLMENT,
    domain_projection_kind=WmsDomainProjectionKind.RACK_TRANSPORT_DEMAND,
)
CHANGE_RACK_FACE = effect_operation(
    identity="wms.fulfillment.change_rack_face@v1",
    request_model=ChangeRackFaceRequest,
    result_model=ChangeRackFaceResult,
    path_template="/fulfillment/rack-face-change",
    target_code="WMS_FULFILLMENT_CHANGE_RACK_FACE",
    reject_codes=("RACK_NOT_FOUND", "RACK_NOT_AT_STATION", "FACE_CHANGE_BLOCKED"),
    completion_mode=WmsCompletionMode.ASYNC_TASK,
    execution_lane=WmsExecutionLane.WMS_FULFILLMENT,
)
REQUEST_LOAD_UNIT_TRANSPORT = effect_operation(
    identity="wms.fulfillment.request_load_unit_transport@v1",
    request_model=RequestLoadUnitTransportRequest,
    result_model=RequestLoadUnitTransportResult,
    path_template="/fulfillment/load-unit-transport",
    target_code="WMS_FULFILLMENT_REQUEST_LOAD_UNIT_TRANSPORT",
    reject_codes=("LOAD_UNIT_NOT_FOUND", "LOAD_UNIT_LOCKED", "DESTINATION_BLOCKED"),
    completion_mode=WmsCompletionMode.ASYNC_TASK,
    execution_lane=WmsExecutionLane.WMS_FULFILLMENT,
)
PUBLISH_MANUAL_TASK = effect_operation(
    identity="wms.fulfillment.publish_manual_task@v1",
    request_model=PublishManualTaskRequest,
    result_model=PublishManualTaskResult,
    path_template="/fulfillment/manual-tasks",
    target_code="WMS_FULFILLMENT_PUBLISH_MANUAL_TASK",
    reject_codes=("MANUAL_TASK_NOT_SUPPORTED", "STATION_NOT_FOUND", "OBJECT_NOT_FOUND"),
    completion_mode=WmsCompletionMode.SYNC_RESULT,
    execution_lane=WmsExecutionLane.WMS_DATA,
    terminal_identity_validator=validate_publish_manual_task_terminal_identity,
)
CANCEL_REQUEST = effect_operation(
    identity="wms.fulfillment.cancel_request@v1",
    request_model=CancelRequestRequest,
    result_model=CancelRequestResult,
    path_template="/fulfillment/requests/cancel",
    target_code="WMS_FULFILLMENT_CANCEL_REQUEST",
    reject_codes=("TARGET_REQUEST_NOT_FOUND", "TARGET_IDENTITY_MISMATCH"),
    completion_mode=WmsCompletionMode.SYNC_RESULT,
    execution_lane=WmsExecutionLane.WMS_FULFILLMENT,
    terminal_identity_validator=validate_cancel_terminal_result,
)

OPERATIONS = (
    NOTIFY_PKG_BINDING,
    REQUEST_RACK_SUPPLY,
    REQUEST_RACK_TRANSPORT,
    CHANGE_RACK_FACE,
    REQUEST_LOAD_UNIT_TRANSPORT,
    PUBLISH_MANUAL_TASK,
    CANCEL_REQUEST,
)

ASYNC_FULFILLMENT_OPERATION_IDENTITIES = frozenset(
    operation.identity for operation in OPERATIONS if operation.supports_status_query
)

__all__ = [
    "ASYNC_FULFILLMENT_OPERATION_IDENTITIES",
    "OPERATIONS",
    "WmsAsyncSubmitReject",
]
