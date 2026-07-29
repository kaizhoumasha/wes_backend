"""WMS 库存域 Q14–Q15/E01–E06 typed contracts 与静态 Definitions。"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from src.app.wms_integration.operation_contract import (
    WmsCompletionMode,
    WmsExecutionLane,
    effect_operation,
    query_operation,
)
from src.app.wms_integration.ports.operation_common import (
    CursorRequest,
    EffectRequest,
    EffectResult,
    NonNegativeDecimal,
    PositiveDecimal,
    StableText,
    StrictWmsModel,
)


class InventoryRecord(StrictWmsModel):
    material_code: StableText = Field(max_length=120)
    available_quantity: NonNegativeDecimal
    total_quantity: NonNegativeDecimal
    reserved_quantity: NonNegativeDecimal
    location_code: StableText | None = Field(default=None, max_length=120)
    lot_no: StableText | None = Field(default=None, max_length=120)


class InventorySnapshotQueryRequest(CursorRequest):
    material_code: StableText = Field(max_length=120)
    warehouse_code: StableText | None = Field(default=None, max_length=120)
    owner_code: StableText | None = Field(default=None, max_length=120)
    lot_no: StableText | None = Field(default=None, max_length=120)


class InventorySnapshotQueryResult(StrictWmsModel):
    items: tuple[InventoryRecord, ...]
    next_cursor: StableText | None = Field(default=None, max_length=500)
    source_version: StableText = Field(max_length=160)


class GetReservationRequest(StrictWmsModel):
    reservation_id: StableText = Field(max_length=160)


class GetReservationResult(StrictWmsModel):
    reservation_id: StableText = Field(max_length=160)
    status: Literal["ACTIVE", "RELEASED", "CONSUMED", "EXPIRED"]
    material_code: StableText = Field(max_length=120)
    quantity: PositiveDecimal
    expires_at: StableText | None = Field(default=None, max_length=80)
    source_version: StableText = Field(max_length=160)


class ReserveInventoryRequest(EffectRequest):
    material_code: StableText = Field(max_length=120)
    quantity: PositiveDecimal
    warehouse_code: StableText = Field(max_length=120)
    owner_code: StableText | None = Field(default=None, max_length=120)
    lot_no: StableText | None = Field(default=None, max_length=120)


class ReserveInventoryResult(EffectResult):
    material_code: StableText = Field(max_length=120)
    reservation_id: StableText = Field(max_length=160)
    reserved_quantity: PositiveDecimal
    expires_at: StableText = Field(max_length=80)


class ReleaseReservationRequest(EffectRequest):
    reservation_id: StableText = Field(max_length=160)
    release_reason: StableText = Field(max_length=120)


class ReleaseReservationResult(EffectResult):
    reservation_id: StableText = Field(max_length=160)
    release_reference: StableText = Field(max_length=160)
    reservation_status: Literal["RELEASED", "ALREADY_RELEASED", "CONSUMED", "EXPIRED"]


class ConfirmInboundRequest(EffectRequest):
    inbound_key: StableText = Field(max_length=160)
    material_code: StableText = Field(max_length=120)
    quantity: PositiveDecimal
    pkg_id: StableText = Field(max_length=160)
    location_code: StableText = Field(max_length=120)


class ConfirmInboundResult(EffectResult):
    inbound_key: StableText = Field(max_length=160)
    wms_document_no: StableText = Field(max_length=160)
    inventory_source_version: StableText = Field(max_length=160)


class ConfirmOutboundRequest(EffectRequest):
    outbound_key: StableText = Field(max_length=160)
    material_code: StableText = Field(max_length=120)
    quantity: PositiveDecimal
    pkg_id: StableText | None = Field(default=None, max_length=160)
    reservation_id: StableText | None = Field(default=None, max_length=160)


class ConfirmOutboundResult(EffectResult):
    outbound_key: StableText = Field(max_length=160)
    issue_reference: StableText = Field(max_length=160)
    inventory_source_version: StableText = Field(max_length=160)


class TransferInventoryRequest(EffectRequest):
    transfer_key: StableText = Field(max_length=160)
    material_code: StableText = Field(max_length=120)
    quantity: PositiveDecimal
    source_location_code: StableText = Field(max_length=120)
    destination_location_code: StableText = Field(max_length=120)


class TransferInventoryResult(EffectResult):
    transfer_key: StableText = Field(max_length=160)
    transfer_reference: StableText = Field(max_length=160)
    inventory_source_version: StableText = Field(max_length=160)


class ConfirmReturnPutawayRequest(EffectRequest):
    return_key: StableText = Field(max_length=160)
    original_pkg_id: StableText = Field(max_length=160)
    material_code: StableText = Field(max_length=120)
    quantity: PositiveDecimal
    destination_location_code: StableText = Field(max_length=120)


class ConfirmReturnPutawayResult(EffectResult):
    return_key: StableText = Field(max_length=160)
    return_reference: StableText = Field(max_length=160)
    new_pkg_id: StableText = Field(max_length=160)
    inventory_source_version: StableText = Field(max_length=160)


def validate_reserve_inventory_terminal_identity(
    request: ReserveInventoryRequest,
    result: ReserveInventoryResult,
) -> None:
    if request.material_code != result.material_code or request.quantity != result.reserved_quantity:
        raise ValueError("reserve inventory terminal identity differs from request")


def validate_release_reservation_terminal_identity(
    request: ReleaseReservationRequest,
    result: ReleaseReservationResult,
) -> None:
    if request.reservation_id != result.reservation_id:
        raise ValueError("release reservation terminal identity differs from request")


def validate_confirm_inbound_terminal_identity(
    request: ConfirmInboundRequest,
    result: ConfirmInboundResult,
) -> None:
    if request.inbound_key != result.inbound_key:
        raise ValueError("confirm inbound terminal identity differs from request")


def validate_confirm_outbound_terminal_identity(
    request: ConfirmOutboundRequest,
    result: ConfirmOutboundResult,
) -> None:
    if request.outbound_key != result.outbound_key:
        raise ValueError("confirm outbound terminal identity differs from request")


def validate_transfer_inventory_terminal_identity(
    request: TransferInventoryRequest,
    result: TransferInventoryResult,
) -> None:
    if request.transfer_key != result.transfer_key:
        raise ValueError("transfer inventory terminal identity differs from request")


def validate_confirm_return_putaway_terminal_identity(
    request: ConfirmReturnPutawayRequest,
    result: ConfirmReturnPutawayResult,
) -> None:
    if request.return_key != result.return_key:
        raise ValueError("confirm return putaway terminal identity differs from request")


QUERY_INVENTORY = query_operation(
    identity="wms.inventory.query_inventory@v1",
    request_model=InventorySnapshotQueryRequest,
    result_model=InventorySnapshotQueryResult,
    path_template="/inventory/query",
    target_code="WMS_INVENTORY_QUERY",
    reject_codes=("INVALID_INVENTORY_FILTER",),
    list_result=True,
)
GET_RESERVATION = query_operation(
    identity="wms.inventory.get_reservation@v1",
    request_model=GetReservationRequest,
    result_model=GetReservationResult,
    path_template="/inventory/reservations/{reservation_id}",
    target_code="WMS_INVENTORY_GET_RESERVATION",
    reject_codes=("RESERVATION_NOT_FOUND",),
)
RESERVE_INVENTORY = effect_operation(
    identity="wms.inventory.reserve_inventory@v1",
    request_model=ReserveInventoryRequest,
    result_model=ReserveInventoryResult,
    path_template="/inventory/reservations",
    target_code="WMS_INVENTORY_RESERVE",
    reject_codes=("INSUFFICIENT_STOCK", "MATERIAL_BLOCKED", "LOCATION_BLOCKED"),
    completion_mode=WmsCompletionMode.SYNC_RESULT,
    execution_lane=WmsExecutionLane.WMS_DATA,
    terminal_identity_validator=validate_reserve_inventory_terminal_identity,
)
RELEASE_RESERVATION = effect_operation(
    identity="wms.inventory.release_reservation@v1",
    request_model=ReleaseReservationRequest,
    result_model=ReleaseReservationResult,
    path_template="/inventory/reservations/release",
    target_code="WMS_INVENTORY_RELEASE_RESERVATION",
    reject_codes=("RESERVATION_NOT_FOUND", "RESERVATION_OWNER_MISMATCH"),
    completion_mode=WmsCompletionMode.SYNC_RESULT,
    execution_lane=WmsExecutionLane.WMS_DATA,
    terminal_identity_validator=validate_release_reservation_terminal_identity,
)
CONFIRM_INBOUND = effect_operation(
    identity="wms.inventory.confirm_inbound@v1",
    request_model=ConfirmInboundRequest,
    result_model=ConfirmInboundResult,
    path_template="/inventory/confirm-inbound",
    target_code="WMS_INVENTORY_CONFIRM_INBOUND",
    reject_codes=("MATERIAL_BLOCKED", "PACKAGE_NOT_FOUND", "LOCATION_BLOCKED"),
    completion_mode=WmsCompletionMode.SYNC_RESULT,
    execution_lane=WmsExecutionLane.WMS_DATA,
    terminal_identity_validator=validate_confirm_inbound_terminal_identity,
)
CONFIRM_OUTBOUND = effect_operation(
    identity="wms.inventory.confirm_outbound@v1",
    request_model=ConfirmOutboundRequest,
    result_model=ConfirmOutboundResult,
    path_template="/inventory/confirm-outbound",
    target_code="WMS_INVENTORY_CONFIRM_OUTBOUND",
    reject_codes=("INSUFFICIENT_STOCK", "RESERVATION_NOT_FOUND", "PACKAGE_NOT_FOUND"),
    completion_mode=WmsCompletionMode.SYNC_RESULT,
    execution_lane=WmsExecutionLane.WMS_DATA,
    terminal_identity_validator=validate_confirm_outbound_terminal_identity,
)
TRANSFER_INVENTORY = effect_operation(
    identity="wms.inventory.transfer_inventory@v1",
    request_model=TransferInventoryRequest,
    result_model=TransferInventoryResult,
    path_template="/inventory/transfers",
    target_code="WMS_INVENTORY_TRANSFER",
    reject_codes=("INSUFFICIENT_STOCK", "SOURCE_LOCATION_MISMATCH", "DESTINATION_BLOCKED"),
    completion_mode=WmsCompletionMode.SYNC_RESULT,
    execution_lane=WmsExecutionLane.WMS_DATA,
    terminal_identity_validator=validate_transfer_inventory_terminal_identity,
)
CONFIRM_RETURN_PUTAWAY = effect_operation(
    identity="wms.inventory.confirm_return_putaway@v1",
    request_model=ConfirmReturnPutawayRequest,
    result_model=ConfirmReturnPutawayResult,
    path_template="/inventory/confirm-return-putaway",
    target_code="WMS_INVENTORY_CONFIRM_RETURN_PUTAWAY",
    reject_codes=("PACKAGE_NOT_FOUND", "MATERIAL_MISMATCH", "DESTINATION_BLOCKED"),
    completion_mode=WmsCompletionMode.SYNC_RESULT,
    execution_lane=WmsExecutionLane.WMS_DATA,
    terminal_identity_validator=validate_confirm_return_putaway_terminal_identity,
)

QUERY_OPERATIONS = (QUERY_INVENTORY, GET_RESERVATION)
EFFECT_OPERATIONS = (
    RESERVE_INVENTORY,
    RELEASE_RESERVATION,
    CONFIRM_INBOUND,
    CONFIRM_OUTBOUND,
    TRANSFER_INVENTORY,
    CONFIRM_RETURN_PUTAWAY,
)
OPERATIONS = (*QUERY_OPERATIONS, *EFFECT_OPERATIONS)

__all__ = ["EFFECT_OPERATIONS", "OPERATIONS", "QUERY_OPERATIONS"]
