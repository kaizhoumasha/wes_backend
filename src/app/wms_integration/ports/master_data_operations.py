"""WMS 主数据域 Q01–Q07 typed contracts 与静态 Definitions。"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from src.app.wms_integration.operation_contract import query_operation
from src.app.wms_integration.ports.operation_common import CursorRequest, StableText, StrictWmsModel


class MaterialRecord(StrictWmsModel):
    material_code: StableText = Field(max_length=120)
    material_name: StableText = Field(max_length=240)
    uom: StableText = Field(max_length=30)
    batch_managed: bool
    serial_managed: bool
    high_value: bool
    msd_level: StableText | None = Field(default=None, max_length=30)


class GetMaterialRequest(StrictWmsModel):
    material_code: StableText = Field(max_length=120)


class GetMaterialResult(MaterialRecord):
    pass


class ListMaterialsRequest(CursorRequest):
    material_codes: tuple[StableText, ...] = ()
    batch_managed: bool | None = None


class ListMaterialsResult(StrictWmsModel):
    items: tuple[MaterialRecord, ...]
    next_cursor: StableText | None = Field(default=None, max_length=500)


class ZoneRecord(StrictWmsModel):
    zone_code: StableText = Field(max_length=120)
    zone_name: StableText = Field(max_length=240)
    status: Literal["ACTIVE", "INACTIVE"]


class ListZonesRequest(CursorRequest):
    status: Literal["ACTIVE", "INACTIVE"] | None = None


class ListZonesResult(StrictWmsModel):
    items: tuple[ZoneRecord, ...]
    next_cursor: StableText | None = Field(default=None, max_length=500)


class LocationRecord(StrictWmsModel):
    location_code: StableText = Field(max_length=120)
    zone_code: StableText = Field(max_length=120)
    location_type: StableText = Field(max_length=80)
    status: Literal["AVAILABLE", "OCCUPIED", "BLOCKED"]


class ListLocationsRequest(CursorRequest):
    zone_code: StableText | None = Field(default=None, max_length=120)
    status: Literal["AVAILABLE", "OCCUPIED", "BLOCKED"] | None = None


class ListLocationsResult(StrictWmsModel):
    items: tuple[LocationRecord, ...]
    next_cursor: StableText | None = Field(default=None, max_length=500)


class RackRecord(StrictWmsModel):
    rack_id: StableText = Field(max_length=120)
    rack_type: StableText = Field(max_length=80)
    location_code: StableText = Field(max_length=120)
    rack_face: Literal["A", "B"]
    capacity: int = Field(ge=0)
    status: StableText = Field(max_length=80)


class GetRackRequest(StrictWmsModel):
    rack_id: StableText = Field(max_length=120)


class GetRackResult(RackRecord):
    pass


class ListRacksRequest(CursorRequest):
    rack_type: StableText | None = Field(default=None, max_length=80)
    status: StableText | None = Field(default=None, max_length=80)


class ListRacksResult(StrictWmsModel):
    items: tuple[RackRecord, ...]
    next_cursor: StableText | None = Field(default=None, max_length=500)


class BinSlotRecord(StrictWmsModel):
    slot_id: StableText = Field(max_length=120)
    material_code: StableText | None = Field(default=None, max_length=120)
    pkg_id: StableText | None = Field(default=None, max_length=120)


class GetBinRequest(StrictWmsModel):
    bin_id: StableText = Field(max_length=120)


class GetBinResult(StrictWmsModel):
    bin_id: StableText = Field(max_length=120)
    rack_id: StableText | None = Field(default=None, max_length=120)
    location_code: StableText = Field(max_length=120)
    status: StableText = Field(max_length=80)
    slots: tuple[BinSlotRecord, ...]


GET_MATERIAL = query_operation(
    identity="wms.master_data.get_material@v1",
    request_model=GetMaterialRequest,
    result_model=GetMaterialResult,
    path_template="/master-data/materials/{material_code}",
    target_code="WMS_MASTER_DATA_GET_MATERIAL",
    reject_codes=("MATERIAL_NOT_FOUND",),
)
LIST_MATERIALS = query_operation(
    identity="wms.master_data.list_materials@v1",
    request_model=ListMaterialsRequest,
    result_model=ListMaterialsResult,
    path_template="/master-data/materials",
    target_code="WMS_MASTER_DATA_LIST_MATERIALS",
    reject_codes=("INVALID_MATERIAL_FILTER",),
    list_result=True,
)
LIST_ZONES = query_operation(
    identity="wms.master_data.list_zones@v1",
    request_model=ListZonesRequest,
    result_model=ListZonesResult,
    path_template="/master-data/zones",
    target_code="WMS_MASTER_DATA_LIST_ZONES",
    reject_codes=("INVALID_ZONE_FILTER",),
    list_result=True,
)
LIST_LOCATIONS = query_operation(
    identity="wms.master_data.list_locations@v1",
    request_model=ListLocationsRequest,
    result_model=ListLocationsResult,
    path_template="/master-data/locations",
    target_code="WMS_MASTER_DATA_LIST_LOCATIONS",
    reject_codes=("ZONE_NOT_FOUND",),
    list_result=True,
)
GET_RACK = query_operation(
    identity="wms.master_data.get_rack@v1",
    request_model=GetRackRequest,
    result_model=GetRackResult,
    path_template="/master-data/racks/{rack_id}",
    target_code="WMS_MASTER_DATA_GET_RACK",
    reject_codes=("RACK_NOT_FOUND",),
)
LIST_RACKS = query_operation(
    identity="wms.master_data.list_racks@v1",
    request_model=ListRacksRequest,
    result_model=ListRacksResult,
    path_template="/master-data/racks",
    target_code="WMS_MASTER_DATA_LIST_RACKS",
    reject_codes=("INVALID_RACK_FILTER",),
    list_result=True,
)
GET_BIN = query_operation(
    identity="wms.master_data.get_bin@v1",
    request_model=GetBinRequest,
    result_model=GetBinResult,
    path_template="/master-data/bins/{bin_id}",
    target_code="WMS_MASTER_DATA_GET_BIN",
    reject_codes=("BIN_NOT_FOUND",),
)

OPERATIONS = (GET_MATERIAL, LIST_MATERIALS, LIST_ZONES, LIST_LOCATIONS, GET_RACK, LIST_RACKS, GET_BIN)

__all__ = ["OPERATIONS"]
