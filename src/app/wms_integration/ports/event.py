"""WMS 普通业务事件的公开 typed 合同。"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

from src.app.contracts.wms_inbound import WMS_BUSINESS_EVENT_TYPES

StableText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class _WmsEventData(BaseModel):
    """普通事件 data 的共同约束。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


class WmsGrnReceivedData(_WmsEventData):
    """PO 行级 GRN 收货事实。"""

    grn_id: StableText = Field(max_length=80)
    po_number: StableText = Field(max_length=120)
    po_item: StableText = Field(max_length=120)
    material_code: StableText = Field(max_length=120)
    received_quantity: float = Field(gt=0)
    warehouse_code: StableText = Field(max_length=80)


class WmsPalletArrivedData(_WmsEventData):
    """WMS 主导流程中的栈板到达事实。"""

    pallet_id: StableText = Field(max_length=80)
    arrived_station: StableText = Field(max_length=80)


class WmsInventoryUpdatedData(_WmsEventData):
    """触发按需重读的库存变更提示。"""

    inventory_reference: StableText = Field(max_length=120)
    material_code: StableText | None = Field(default=None, max_length=120)


class WmsPdaOperationRecordedData(_WmsEventData):
    """人工/PDA 操作结果与证据。"""

    operation_record_id: StableText = Field(max_length=120)
    operation_type: StableText = Field(max_length=80)
    operator_code: StableText | None = Field(default=None, max_length=80)


class InboundEventEnvelope(BaseModel):
    """所有外部普通事件共享的稳定顶层身份。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_system: StableText
    event_type: StableText
    source_event_id: StableText = Field(max_length=120)
    source_version: StableText = Field(max_length=80)
    occurred_at: AwareDatetime
    request_id: StableText = Field(max_length=120)
    correlation_id: StableText | None = Field(default=None, max_length=120)


class WmsBusinessEvent(InboundEventEnvelope):
    """WMS 普通事件共享顶层包络。"""

    source_system: Literal["WMS"]


class WmsGrnReceivedEvent(WmsBusinessEvent):
    """PO 行级 GRN 事件。"""

    event_type: Literal["WMS_GRN_RECEIVED"]
    data: WmsGrnReceivedData


class WmsPalletArrivedEvent(WmsBusinessEvent):
    """栈板到达事件。"""

    event_type: Literal["WMS_PALLET_ARRIVED"]
    data: WmsPalletArrivedData


class WmsInventoryUpdatedEvent(WmsBusinessEvent):
    """库存更新提示事件。"""

    event_type: Literal["WMS_INVENTORY_UPDATED"]
    data: WmsInventoryUpdatedData


class WmsPdaOperationRecordedEvent(WmsBusinessEvent):
    """PDA 操作证据事件。"""

    event_type: Literal["WMS_PDA_OPERATION_RECORDED"]
    data: WmsPdaOperationRecordedData


type WmsTypedBusinessEvent = (
    WmsGrnReceivedEvent | WmsPalletArrivedEvent | WmsInventoryUpdatedEvent | WmsPdaOperationRecordedEvent
)


__all__ = [
    "WMS_BUSINESS_EVENT_TYPES",
    "InboundEventEnvelope",
    "WmsBusinessEvent",
    "WmsGrnReceivedData",
    "WmsGrnReceivedEvent",
    "WmsInventoryUpdatedData",
    "WmsInventoryUpdatedEvent",
    "WmsPalletArrivedData",
    "WmsPalletArrivedEvent",
    "WmsPdaOperationRecordedData",
    "WmsPdaOperationRecordedEvent",
    "WmsTypedBusinessEvent",
]
