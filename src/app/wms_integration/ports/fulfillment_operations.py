"""WMS 履约域 E07–E16 typed contracts 与静态 Definitions。"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import Field, model_validator

from src.app.wms_integration.operation_contract import (
    WmsCompletionMode,
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


class FullBoxExchangeRequest(EffectRequest):
    exchange_request_key: StableText = Field(max_length=160)
    station_code: StableText = Field(max_length=120)
    rack_id: StableText = Field(max_length=120)
    rack_face: Literal["A", "B"]
    full_box_id: StableText = Field(max_length=120)
    source_slot_id: StableText = Field(max_length=120)
    occupancies: tuple[FrozenBinCellOccupancy, ...] = Field(min_length=1)


class RackBinSlotRelation(StrictWmsModel):
    rack_id: StableText = Field(max_length=120)
    bin_id: StableText = Field(max_length=120)
    slot_id: StableText = Field(max_length=120)


class FullBoxExchangeResult(EffectResult):
    exchange_request_key: StableText = Field(max_length=160)
    full_box_id: StableText = Field(max_length=120)
    selected_empty_box_id: StableText = Field(max_length=120)
    full_box_destination: RackBinSlotRelation
    empty_box_destination: RackBinSlotRelation
    final_relations: tuple[RackBinSlotRelation, ...] = Field(min_length=2)
    task_outcome: Literal["SUCCESS", "PARTIAL_FAILURE", "FAILED_AFTER_EXECUTION"]
    inventory_source_version: StableText = Field(max_length=160)

    @model_validator(mode="after")
    def validate_final_relations(self) -> FullBoxExchangeResult:
        if self.full_box_destination.bin_id != self.full_box_id:
            raise ValueError("full_box_destination must identify full_box_id")
        if self.empty_box_destination.bin_id != self.selected_empty_box_id:
            raise ValueError("empty_box_destination must identify selected_empty_box_id")
        expected = (self.full_box_destination, self.empty_box_destination)
        if len(self.final_relations) != 2 or self.final_relations != expected or len(set(self.final_relations)) != 2:
            raise ValueError("final_relations must uniquely equal the two authored destinations")
        return self


class ConveyorBatchItem(StrictWmsModel):
    sequence_no: int = Field(ge=1)
    route_instance_id: StableText = Field(max_length=160)
    bin_id: StableText = Field(max_length=120)
    source_rack_id: StableText = Field(max_length=120)
    source_slot_id: StableText = Field(max_length=120)
    reserved_queue_position: int = Field(ge=0)


def _require_unique_batch_members(items: tuple[ConveyorBatchItem | ConveyorExitCandidate, ...]) -> None:
    for field_name in ("sequence_no", "route_instance_id", "bin_id"):
        values = tuple(getattr(item, field_name) for item in items)
        if len(values) != len(set(values)):
            raise ValueError(f"batch contains duplicate {field_name}")


class MoveBinsToConveyorEntryRequest(EffectRequest):
    batch_id: StableText = Field(max_length=160)
    direction: Literal["TO_CONVEYOR_ENTRY"]
    source_station_code: StableText = Field(max_length=120)
    destination_station_code: StableText = Field(max_length=120)
    capacity_snapshot_version: StableText = Field(max_length=160)
    items: tuple[ConveyorBatchItem, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_frozen_members(self) -> MoveBinsToConveyorEntryRequest:
        _require_unique_batch_members(self.items)
        if tuple(item.sequence_no for item in self.items) != tuple(range(1, len(self.items) + 1)):
            raise ValueError("E12 sequence_no must be contiguous and ordered")
        queue_positions = tuple(item.reserved_queue_position for item in self.items)
        if len(queue_positions) != len(set(queue_positions)):
            raise ValueError("E12 reserved_queue_position must be unique")
        return self


class BatchItemResult(StrictWmsModel):
    sequence_no: int = Field(ge=1)
    route_instance_id: StableText = Field(max_length=160)
    bin_id: StableText = Field(max_length=120)
    item_outcome: Literal["SUCCESS", "FAILED", "UNKNOWN"]
    final_rack_id: StableText | None = Field(default=None, max_length=120)
    final_slot_id: StableText | None = Field(default=None, max_length=120)
    final_queue_position: int | None = Field(default=None, ge=0)


class MoveBinsToConveyorEntryResult(EffectResult):
    batch_id: StableText = Field(max_length=160)
    accepted_object_keys: tuple[StableText, ...] = Field(min_length=1)
    items: tuple[BatchItemResult, ...] = Field(min_length=1)
    task_outcome: Literal["SUCCESS", "PARTIAL_FAILURE", "FAILED_AFTER_EXECUTION"]

    @model_validator(mode="after")
    def validate_member_correspondence(self) -> MoveBinsToConveyorEntryResult:
        _require_unique_batch_result_members(self.items)
        if tuple(item.bin_id for item in self.items) != self.accepted_object_keys:
            raise ValueError("terminal items must match accepted_object_keys in order")
        return self


class ConveyorExitCandidate(StrictWmsModel):
    sequence_no: int = Field(ge=1)
    route_instance_id: StableText = Field(max_length=160)
    bin_id: StableText = Field(max_length=120)
    scan3_enqueued_at: StableText = Field(max_length=80)
    queue_position: int = Field(ge=0)


def frozen_candidate_digest(
    *,
    workline_id: int,
    queue_code: str,
    candidate_items: tuple[ConveyorExitCandidate, ...],
) -> str:
    """绑定 E13 来源队列及有序候选身份，任一成员或顺序变化都会改变 digest。"""

    canonical = {
        "workline_id": workline_id,
        "queue_code": queue_code,
        "candidate_items": [
            {
                "sequence_no": item.sequence_no,
                "route_instance_id": item.route_instance_id,
                "bin_id": item.bin_id,
                "scan3_enqueued_at": item.scan3_enqueued_at,
                "queue_position": item.queue_position,
            }
            for item in candidate_items
        ],
    }
    encoded = json.dumps(canonical, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


class MoveBinsFromConveyorExitRequest(EffectRequest):
    batch_id: StableText = Field(max_length=160)
    direction: Literal["FROM_CONVEYOR_EXIT"]
    workline_id: int = Field(gt=0)
    queue_code: StableText = Field(max_length=120)
    candidate_digest: StableText = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_items: tuple[ConveyorExitCandidate, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_frozen_candidates(self) -> MoveBinsFromConveyorExitRequest:
        max_candidate_count = MOVE_BINS_FROM_CONVEYOR_EXIT.max_candidate_count
        if max_candidate_count is None or len(self.candidate_items) > max_candidate_count:
            raise ValueError("E13 candidate_items exceeds max_candidate_count")
        _require_unique_batch_members(self.candidate_items)
        expected_digest = frozen_candidate_digest(
            workline_id=self.workline_id,
            queue_code=self.queue_code,
            candidate_items=self.candidate_items,
        )
        if self.candidate_digest != expected_digest:
            raise ValueError("candidate_digest does not match ordered frozen candidates")
        fifo_order = tuple(
            sorted(
                self.candidate_items,
                key=lambda item: (item.scan3_enqueued_at, item.queue_position, item.bin_id),
            )
        )
        if self.candidate_items != fifo_order:
            raise ValueError("E13 candidate_items must preserve strict SCAN3 FIFO order")
        return self


class MoveBinsFromConveyorExitResult(EffectResult):
    batch_id: StableText = Field(max_length=160)
    accepted_object_keys: tuple[StableText, ...] = Field(min_length=1)
    candidate_digest: StableText = Field(pattern=r"^[0-9a-f]{64}$")
    items: tuple[BatchItemResult, ...] = Field(min_length=1)
    task_outcome: Literal["SUCCESS", "PARTIAL_FAILURE", "FAILED_AFTER_EXECUTION"]

    @model_validator(mode="after")
    def validate_member_correspondence(self) -> MoveBinsFromConveyorExitResult:
        _require_unique_batch_result_members(self.items)
        if tuple(item.bin_id for item in self.items) != self.accepted_object_keys:
            raise ValueError("terminal items must match accepted_object_keys in order")
        return self


def _require_unique_batch_result_members(items: tuple[BatchItemResult, ...]) -> None:
    for field_name in ("sequence_no", "route_instance_id", "bin_id"):
        values = tuple(getattr(item, field_name) for item in items)
        if len(values) != len(set(values)):
            raise ValueError(f"terminal result contains duplicate {field_name}")


def accepted_scope_digest(object_keys: tuple[str, ...]) -> str:
    """对有序接纳成员生成 canonical SHA-256；顺序属于 ACK 合同。"""

    canonical = json.dumps(object_keys, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class WmsAcceptedScope(StrictWmsModel):
    """异步批次 ACK 冻结的非空、有序、不可重复成员。"""

    object_keys: tuple[StableText, ...] = Field(min_length=1)
    scope_digest: StableText = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_unique_members(self) -> WmsAcceptedScope:
        if len(self.object_keys) != len(set(self.object_keys)):
            raise ValueError("accepted scope contains duplicate object_keys")
        return self


type AsyncFulfillmentOperationIdentity = StableText

ASYNC_FULFILLMENT_OPERATION_IDENTITIES: frozenset[str]
BATCH_FULFILLMENT_OPERATION_IDENTITIES: frozenset[str]
BATCH_FULFILLMENT_IDENTITY_BY_REQUEST_MODEL: dict[type[EffectRequest], str]


class WmsEffectAck(StrictWmsModel):
    """E08–E14 共用 ACK；批次项用 accepted_scope 冻结实际成员。"""

    operation_identity: AsyncFulfillmentOperationIdentity
    idempotency_key: StableText = Field(max_length=160)
    provider_reference: StableText = Field(max_length=160)
    submission_state: Literal["ACCEPTED", "IN_PROGRESS_REPLAY", "REPLAY"]
    accepted_scope: WmsAcceptedScope | None = None

    @model_validator(mode="after")
    def validate_scope_presence(self) -> WmsEffectAck:
        if self.operation_identity not in ASYNC_FULFILLMENT_OPERATION_IDENTITIES:
            raise ValueError("ACK operation_identity is not an authored async fulfillment operation")
        is_batch = self.operation_identity in BATCH_FULFILLMENT_OPERATION_IDENTITIES
        if is_batch and self.accepted_scope is None:
            raise ValueError("batch ACK requires accepted_scope")
        if not is_batch and self.accepted_scope is not None:
            raise ValueError("single-object ACK forbids accepted_scope")
        return self


def validate_effect_ack(
    *,
    operation_identity: str,
    idempotency_key: str,
    ack: WmsEffectAck,
) -> WmsEffectAck:
    """校验 E08–E14 status 查询与冻结 ACK 的公共关联身份。"""

    if ack.operation_identity != operation_identity:
        raise ValueError("ACK operation_identity differs from status request")
    if ack.idempotency_key != idempotency_key:
        raise ValueError("ACK idempotency_key differs from status request")
    return ack


def validate_effect_provider_reference(
    ack: WmsEffectAck,
    provider_reference: str,
    *,
    evidence_kind: Literal["status", "terminal"],
) -> None:
    """用 ACK 唯一冻结的 provider reference 拒绝 status/terminal 串单。"""

    if provider_reference != ack.provider_reference:
        raise ValueError(f"{evidence_kind} provider_reference does not match ACK")


type BatchFulfillmentRequest = MoveBinsToConveyorEntryRequest | MoveBinsFromConveyorExitRequest
type BatchFulfillmentResult = MoveBinsToConveyorEntryResult | MoveBinsFromConveyorExitResult


def validate_fulfillment_ack(request: BatchFulfillmentRequest, ack: WmsEffectAck) -> WmsEffectAck:
    """校验 E12 整批 ACK 或 E13 有序前缀 ACK。"""

    if ack.accepted_scope is None:
        raise ValueError("batch ACK requires accepted_scope")
    expected_identity = BATCH_FULFILLMENT_IDENTITY_BY_REQUEST_MODEL[type(request)]
    if isinstance(request, MoveBinsToConveyorEntryRequest):
        frozen_keys = tuple(item.bin_id for item in request.items)
        if ack.accepted_scope.object_keys != frozen_keys:
            raise ValueError("E12 ACK must accept the entire frozen batch")
    else:
        candidate_keys = tuple(item.bin_id for item in request.candidate_items)
        accepted_count = len(ack.accepted_scope.object_keys)
        if ack.accepted_scope.object_keys != candidate_keys[:accepted_count]:
            raise ValueError("E13 accepted_scope must be an ordered prefix")
    if ack.operation_identity != expected_identity:
        raise ValueError("ACK operation_identity does not match batch request")
    if ack.accepted_scope.scope_digest != accepted_scope_digest(ack.accepted_scope.object_keys):
        raise ValueError("accepted scope digest does not match canonical members")
    return ack


def validate_batch_terminal_result(
    request: BatchFulfillmentRequest,
    ack: WmsEffectAck,
    result: BatchFulfillmentResult,
) -> BatchFulfillmentResult:
    """校验 terminal items 与 ACK 冻结成员、原请求身份一一对应。"""

    validate_fulfillment_ack(request, ack)
    validate_effect_provider_reference(ack, result.provider_reference, evidence_kind="terminal")
    accepted_scope = ack.accepted_scope
    if accepted_scope is None:
        raise ValueError("batch terminal result requires accepted_scope")
    if result.accepted_object_keys != accepted_scope.object_keys:
        raise ValueError("terminal accepted members do not match ACK")
    request_items = request.items if isinstance(request, MoveBinsToConveyorEntryRequest) else request.candidate_items
    frozen_items = request_items[: len(accepted_scope.object_keys)]
    expected_identities = tuple((item.sequence_no, item.route_instance_id, item.bin_id) for item in frozen_items)
    result_identities = tuple((item.sequence_no, item.route_instance_id, item.bin_id) for item in result.items)
    if result_identities != expected_identities:
        raise ValueError("terminal items do not match frozen request members")
    expected_task_outcome = (
        "SUCCESS"
        if all(item.item_outcome == "SUCCESS" for item in result.items)
        else "PARTIAL_FAILURE"
        if any(item.item_outcome == "SUCCESS" for item in result.items)
        else "FAILED_AFTER_EXECUTION"
    )
    if result.task_outcome != expected_task_outcome:
        raise ValueError("task_outcome does not match member outcomes")
    is_entry = isinstance(request, MoveBinsToConveyorEntryRequest)
    for item in result.items:
        if item.item_outcome == "UNKNOWN":
            if any(value is not None for value in (item.final_rack_id, item.final_slot_id, item.final_queue_position)):
                raise ValueError("UNKNOWN member must not claim final facts")
            continue
        if is_entry:
            if item.final_queue_position is None or item.final_rack_id is not None or item.final_slot_id is not None:
                raise ValueError("E12 known member requires only final_queue_position")
        elif item.final_rack_id is None or item.final_slot_id is None or item.final_queue_position is not None:
            raise ValueError("E13 known member requires only final rack and slot")
    if isinstance(request, MoveBinsFromConveyorExitRequest):
        if not isinstance(result, MoveBinsFromConveyorExitResult):
            raise TypeError("E13 request requires E13 terminal result")
        if result.candidate_digest != request.candidate_digest:
            raise ValueError("E13 terminal candidate_digest does not match request")
    elif not isinstance(result, MoveBinsToConveyorEntryResult):
        raise TypeError("E12 request requires E12 terminal result")
    return result


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
FULL_BOX_EXCHANGE = effect_operation(
    identity="wms.fulfillment.full_box_exchange@v1",
    request_model=FullBoxExchangeRequest,
    result_model=FullBoxExchangeResult,
    path_template="/fulfillment/full-box-exchange",
    target_code="WMS_FULFILLMENT_FULL_BOX_EXCHANGE",
    reject_codes=("RACK_NOT_AT_EXCHANGE_STATION", "FULL_BOX_NOT_FOUND", "NO_EMPTY_BOX_AVAILABLE"),
    completion_mode=WmsCompletionMode.ASYNC_TASK,
    execution_lane=WmsExecutionLane.WMS_FULFILLMENT,
)
MOVE_BINS_TO_CONVEYOR_ENTRY = effect_operation(
    identity="wms.fulfillment.move_bins_to_conveyor_entry@v1",
    request_model=MoveBinsToConveyorEntryRequest,
    result_model=MoveBinsToConveyorEntryResult,
    path_template="/fulfillment/conveyor-entry-batches",
    target_code="WMS_FULFILLMENT_MOVE_BINS_TO_CONVEYOR_ENTRY",
    reject_codes=("BATCH_MEMBER_INVALID", "CONVEYOR_ENTRY_CAPACITY_CHANGED", "CTU_CAPACITY_EXCEEDED"),
    completion_mode=WmsCompletionMode.ASYNC_TASK,
    execution_lane=WmsExecutionLane.WMS_FULFILLMENT,
)
MOVE_BINS_FROM_CONVEYOR_EXIT = effect_operation(
    identity="wms.fulfillment.move_bins_from_conveyor_exit@v1",
    request_model=MoveBinsFromConveyorExitRequest,
    result_model=MoveBinsFromConveyorExitResult,
    path_template="/fulfillment/conveyor-exit-batches",
    target_code="WMS_FULFILLMENT_MOVE_BINS_FROM_CONVEYOR_EXIT",
    reject_codes=("NO_DESTINATION_CAPACITY", "CANDIDATE_DIGEST_MISMATCH", "CANDIDATE_NOT_AVAILABLE"),
    completion_mode=WmsCompletionMode.ASYNC_TASK,
    execution_lane=WmsExecutionLane.WMS_FULFILLMENT,
    max_candidate_count=12,
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
    FULL_BOX_EXCHANGE,
    MOVE_BINS_TO_CONVEYOR_ENTRY,
    MOVE_BINS_FROM_CONVEYOR_EXIT,
    REQUEST_LOAD_UNIT_TRANSPORT,
    PUBLISH_MANUAL_TASK,
    CANCEL_REQUEST,
)

ASYNC_FULFILLMENT_OPERATION_IDENTITIES = frozenset(
    operation.identity for operation in OPERATIONS if operation.supports_status_query
)
BATCH_FULFILLMENT_IDENTITY_BY_REQUEST_MODEL = {
    operation.request_model: operation.identity
    for operation in OPERATIONS
    if operation.request_model in {MoveBinsToConveyorEntryRequest, MoveBinsFromConveyorExitRequest}
}
BATCH_FULFILLMENT_OPERATION_IDENTITIES = frozenset(BATCH_FULFILLMENT_IDENTITY_BY_REQUEST_MODEL.values())

__all__ = [
    "ASYNC_FULFILLMENT_OPERATION_IDENTITIES",
    "BATCH_FULFILLMENT_OPERATION_IDENTITIES",
    "OPERATIONS",
    "frozen_candidate_digest",
]
