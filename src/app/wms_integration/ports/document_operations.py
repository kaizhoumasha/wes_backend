"""WMS 单据域 Q08–Q13/Q19 typed contracts 与静态 Definitions。"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from src.app.wms_integration.operation_contract import WmsHttpMethod, query_operation
from src.app.wms_integration.ports.operation_common import (
    CursorRequest,
    NonNegativeDecimal,
    PositiveDecimal,
    StableText,
    StrictWmsModel,
)


class GetGrnRequest(StrictWmsModel):
    grn_id: StableText = Field(max_length=120)


class GetGrnResult(StrictWmsModel):
    grn_id: StableText = Field(max_length=120)
    po_number: StableText = Field(max_length=120)
    po_item: StableText = Field(max_length=120)
    material_code: StableText = Field(max_length=120)
    planned_quantity: NonNegativeDecimal
    received_quantity: NonNegativeDecimal
    remaining_quantity: NonNegativeDecimal
    batch_no: StableText | None = Field(default=None, max_length=120)
    quality_status: StableText = Field(max_length=80)


class GrnPackageRecord(StrictWmsModel):
    pkg_id: StableText = Field(max_length=120)
    grn_id: StableText = Field(max_length=120)
    material_code: StableText = Field(max_length=120)
    quantity: PositiveDecimal
    status: StableText = Field(max_length=80)


class ListGrnPackagesRequest(CursorRequest):
    grn_id: StableText = Field(max_length=120)


class ListGrnPackagesResult(StrictWmsModel):
    items: tuple[GrnPackageRecord, ...]
    next_cursor: StableText | None = Field(default=None, max_length=500)


class GetPickOrderRequest(StrictWmsModel):
    pick_order_id: StableText = Field(max_length=120)


class GetPickOrderResult(StrictWmsModel):
    pick_order_id: StableText = Field(max_length=120)
    wave_id: StableText = Field(max_length=120)
    status: StableText = Field(max_length=80)
    priority: int = Field(ge=0)
    line_count: int = Field(ge=0)


class GetOutboundOrderRequest(StrictWmsModel):
    outbound_order_id: StableText = Field(max_length=120)


class GetOutboundOrderResult(StrictWmsModel):
    outbound_order_id: StableText = Field(max_length=120)
    status: StableText = Field(max_length=80)
    destination_code: StableText = Field(max_length=120)
    line_count: int = Field(ge=0)


class GetWaveRequest(StrictWmsModel):
    wave_id: StableText = Field(max_length=120)


class GetWaveResult(StrictWmsModel):
    wave_id: StableText = Field(max_length=120)
    status: StableText = Field(max_length=80)
    pick_order_ids: tuple[StableText, ...]


class GetTaskSnapshotRequest(StrictWmsModel):
    task_id: StableText = Field(max_length=160)


class GetTaskSnapshotResult(StrictWmsModel):
    task_id: StableText = Field(max_length=160)
    task_type: StableText = Field(max_length=80)
    status: StableText = Field(max_length=80)
    provider_reference: StableText = Field(max_length=160)
    source_version: StableText = Field(max_length=160)


class SixInOneCode(StrictWmsModel):
    """Q19 canonical 六合一码；字段名与 WMS wire contract 一致。"""

    HHPN: StableText = Field(max_length=160)
    MfrPN: StableText = Field(max_length=160)
    Qty: PositiveDecimal
    DateCode: StableText = Field(max_length=80)
    LotCode: StableText = Field(max_length=120)
    PkgID: StableText = Field(max_length=160)


class ValidateRoughSorterAdmissionRequest(StrictWmsModel):
    raw_code: StableText = Field(max_length=2_000)
    six_in_one: SixInOneCode
    reel_diameter_mm: PositiveDecimal
    reel_thickness_mm: PositiveDecimal
    station_code: StableText = Field(max_length=120)
    workline_id: int = Field(gt=0)
    session_id: int = Field(gt=0)
    correlation_id: StableText = Field(max_length=160)


type RoughSorterAdmissionRejectReason = Literal[
    "GRN_NOT_FOUND",
    "PACKAGE_NOT_FOUND",
    "PACKAGE_GRN_MISMATCH",
    "MATERIAL_MISMATCH",
    "QUANTITY_MISMATCH",
    "MEASUREMENT_OUT_OF_TOLERANCE",
    "PACKAGE_NOT_ADMISSIBLE",
]


class ValidateRoughSorterAdmissionResult(StrictWmsModel):
    decision: Literal["ADMIT", "REJECT"]
    reason_code: RoughSorterAdmissionRejectReason | None = None
    grn_id: StableText | None = Field(default=None, max_length=120)
    po_number: StableText | None = Field(default=None, max_length=120)
    po_item: StableText | None = Field(default=None, max_length=120)
    material_code: StableText | None = Field(default=None, max_length=120)
    pkg_id: StableText | None = Field(default=None, max_length=160)
    measurement_decision: Literal["PASS", "REJECT"]
    standard_reel_diameter_mm: PositiveDecimal
    reel_diameter_tolerance_mm: NonNegativeDecimal
    standard_reel_thickness_mm: PositiveDecimal
    reel_thickness_tolerance_mm: NonNegativeDecimal
    rule_version: StableText = Field(max_length=160)
    source_version: StableText = Field(max_length=160)

    @model_validator(mode="after")
    def validate_decision(self) -> ValidateRoughSorterAdmissionResult:
        if self.decision == "ADMIT" and (self.reason_code is not None or self.measurement_decision != "PASS"):
            raise ValueError("ADMIT must have PASS measurement without reason_code")
        if self.decision == "REJECT" and self.reason_code is None:
            raise ValueError("REJECT requires a stable reason_code")
        return self


GET_GRN = query_operation(
    identity="wms.document.get_grn@v1",
    request_model=GetGrnRequest,
    result_model=GetGrnResult,
    path_template="/documents/grns/{grn_id}",
    target_code="WMS_DOCUMENT_GET_GRN",
    reject_codes=("GRN_NOT_FOUND",),
)
LIST_GRN_PACKAGES = query_operation(
    identity="wms.document.list_grn_packages@v1",
    request_model=ListGrnPackagesRequest,
    result_model=ListGrnPackagesResult,
    path_template="/documents/grns/{grn_id}/packages",
    target_code="WMS_DOCUMENT_LIST_GRN_PACKAGES",
    reject_codes=("GRN_NOT_FOUND",),
    list_result=True,
)
GET_PICK_ORDER = query_operation(
    identity="wms.document.get_pick_order@v1",
    request_model=GetPickOrderRequest,
    result_model=GetPickOrderResult,
    path_template="/documents/pick-orders/{pick_order_id}",
    target_code="WMS_DOCUMENT_GET_PICK_ORDER",
    reject_codes=("PICK_ORDER_NOT_FOUND",),
)
GET_OUTBOUND_ORDER = query_operation(
    identity="wms.document.get_outbound_order@v1",
    request_model=GetOutboundOrderRequest,
    result_model=GetOutboundOrderResult,
    path_template="/documents/outbound-orders/{outbound_order_id}",
    target_code="WMS_DOCUMENT_GET_OUTBOUND_ORDER",
    reject_codes=("OUTBOUND_ORDER_NOT_FOUND",),
)
GET_WAVE = query_operation(
    identity="wms.document.get_wave@v1",
    request_model=GetWaveRequest,
    result_model=GetWaveResult,
    path_template="/documents/waves/{wave_id}",
    target_code="WMS_DOCUMENT_GET_WAVE",
    reject_codes=("WAVE_NOT_FOUND",),
)
GET_TASK_SNAPSHOT = query_operation(
    identity="wms.document.get_task_snapshot@v1",
    request_model=GetTaskSnapshotRequest,
    result_model=GetTaskSnapshotResult,
    path_template="/documents/tasks/{task_id}",
    target_code="WMS_DOCUMENT_GET_TASK_SNAPSHOT",
    reject_codes=("TASK_NOT_FOUND",),
)
VALIDATE_ROUGH_SORTER_ADMISSION = query_operation(
    identity="wms.document.validate_rough_sorter_admission@v1",
    request_model=ValidateRoughSorterAdmissionRequest,
    result_model=ValidateRoughSorterAdmissionResult,
    path_template="/documents/rough-sorter-admission/validate",
    target_code="WMS_DOCUMENT_VALIDATE_ROUGH_SORTER_ADMISSION",
    reject_codes=(
        "GRN_NOT_FOUND",
        "PACKAGE_NOT_FOUND",
        "PACKAGE_GRN_MISMATCH",
        "MATERIAL_MISMATCH",
        "QUANTITY_MISMATCH",
        "MEASUREMENT_OUT_OF_TOLERANCE",
        "PACKAGE_NOT_ADMISSIBLE",
    ),
    http_method=WmsHttpMethod.POST,
)

OPERATIONS = (
    GET_GRN,
    LIST_GRN_PACKAGES,
    GET_PICK_ORDER,
    GET_OUTBOUND_ORDER,
    GET_WAVE,
    GET_TASK_SNAPSHOT,
    VALIDATE_ROUGH_SORTER_ADMISSION,
)
STANDARD_OPERATIONS = OPERATIONS[:-1]
ADMISSION_OPERATION = VALIDATE_ROUGH_SORTER_ADMISSION

__all__ = [
    "ADMISSION_OPERATION",
    "OPERATIONS",
    "STANDARD_OPERATIONS",
    "SixInOneCode",
    "ValidateRoughSorterAdmissionRequest",
    "ValidateRoughSorterAdmissionResult",
]
