"""WMS 对账域 Q16–Q18 typed contracts 与静态 Definitions。"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from src.app.wms_integration.operation_contract import query_operation
from src.app.wms_integration.ports.operation_common import CursorRequest, StableText, StrictWmsModel


class DriftRecord(StrictWmsModel):
    object_type: Literal["BIN", "RACK", "LOCATION", "INVENTORY"]
    object_key: StableText = Field(max_length=240)
    drift_kind: StableText = Field(max_length=120)
    wes_value_hash: StableText | None = Field(default=None, max_length=128)
    wms_value_hash: StableText | None = Field(default=None, max_length=128)
    source_version: StableText = Field(max_length=160)


class CheckBinDriftRequest(CursorRequest):
    warehouse_code: StableText = Field(max_length=120)
    zone_code: StableText | None = Field(default=None, max_length=120)


class CheckBinDriftResult(StrictWmsModel):
    items: tuple[DriftRecord, ...]
    next_cursor: StableText | None = Field(default=None, max_length=500)
    source_version: StableText = Field(max_length=160)


class CheckRackDriftRequest(CursorRequest):
    warehouse_code: StableText = Field(max_length=120)
    station_code: StableText | None = Field(default=None, max_length=120)


class CheckRackDriftResult(StrictWmsModel):
    items: tuple[DriftRecord, ...]
    next_cursor: StableText | None = Field(default=None, max_length=500)
    source_version: StableText = Field(max_length=160)


class CheckFullDriftRequest(CursorRequest):
    warehouse_code: StableText = Field(max_length=120)


class CheckFullDriftResult(StrictWmsModel):
    items: tuple[DriftRecord, ...]
    next_cursor: StableText | None = Field(default=None, max_length=500)
    source_version: StableText = Field(max_length=160)


CHECK_BIN_DRIFT = query_operation(
    identity="wms.reconciliation.check_bin_drift@v1",
    request_model=CheckBinDriftRequest,
    result_model=CheckBinDriftResult,
    path_template="/reconciliation/bin-drift",
    target_code="WMS_RECONCILIATION_CHECK_BIN_DRIFT",
    reject_codes=("WAREHOUSE_NOT_FOUND",),
    list_result=True,
)
CHECK_RACK_DRIFT = query_operation(
    identity="wms.reconciliation.check_rack_drift@v1",
    request_model=CheckRackDriftRequest,
    result_model=CheckRackDriftResult,
    path_template="/reconciliation/rack-drift",
    target_code="WMS_RECONCILIATION_CHECK_RACK_DRIFT",
    reject_codes=("WAREHOUSE_NOT_FOUND",),
    list_result=True,
)
CHECK_FULL_DRIFT = query_operation(
    identity="wms.reconciliation.check_full_drift@v1",
    request_model=CheckFullDriftRequest,
    result_model=CheckFullDriftResult,
    path_template="/reconciliation/full-drift",
    target_code="WMS_RECONCILIATION_CHECK_FULL_DRIFT",
    reject_codes=("WAREHOUSE_NOT_FOUND",),
    list_result=True,
)

OPERATIONS = (CHECK_BIN_DRIFT, CHECK_RACK_DRIFT, CHECK_FULL_DRIFT)

__all__ = ["OPERATIONS"]
