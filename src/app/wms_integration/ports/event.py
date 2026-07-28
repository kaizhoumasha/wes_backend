"""WmsEventPort + InboundEventPort — @deferred to 全量联调。

本 Port 定义 WMS 事件接收能力合同。当前里程碑的粗分机/分拣机流程
通过 RuntimeInbox + callback normalizer 处理外部事件，
不需要独立的 WMS 事件 Port。

激活条件: WMS 全量集成或 WMS 主动推送事件需求明确。

主计划 §5.1 7 port 之一: 入站事件 normalizer (WMS_GRN_RECEIVED /
WMS_PALLET_ARRIVED / WMS_RACK_ARRIVED / WMS_TRANSPORT_COMPLETED 等回调)。

设计:
- InboundEventPort 是所有入站 normalizer 的基协议, 不导出到业务 capability
  (主计划 §3.5 I3 + H2 黑名单)。
- WmsEventPort 是 WMS 回调的 4 个 normalizer, 走 InboundNormalizerRegistry
  路径 (Task 7), 业务 capability 不可注入。

normalizer 职责: 把 WMS 原始回调 JSON 转 typed envelope + 解析 correlation_id
(manual / auto / hybrid 策略由 InboundNormalizerProfile.correlation_resolution
声明)。转换后投递到 RuntimeInbox, 不直接调用业务 capability (主计划 §3.5.1)。
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class InboundEventEnvelope(BaseModel):
    """入站事件标准化 envelope (所有 normalizer 输出基类)。"""

    model_config = ConfigDict(extra="forbid")

    source_event_id: str = Field(min_length=1, max_length=120, description="WMS/ECS 源事件 ID (幂等键)")
    provider_code: str = Field(min_length=1, max_length=60, description="来源 provider 编码")
    occurred_at: str = Field(description="事件发生时间 ISO 8601")
    correlation_id: str = Field(min_length=1, max_length=80, description="与 ExecutionCorrelation 的关联 ID")
    raw_payload: dict = Field(default_factory=dict, description="原始回调 payload (保留供审计)")


class WmsGrnReceivedEvent(BaseModel):
    """WMS PO 行级 GRN 收货事件 (normalizer 输出)。"""

    model_config = ConfigDict(extra="forbid")

    envelope: InboundEventEnvelope = Field(description="共享 envelope")
    grn_id: str = Field(min_length=1, max_length=80, description="GRN 编号")
    po_number: str = Field(min_length=1, max_length=120, description="采购订单号")
    po_item: str = Field(min_length=1, max_length=120, description="采购订单行")
    material_code: str = Field(min_length=1, max_length=120, description="物料编码")
    received_quantity: float = Field(gt=0, description="本次收货数量")
    warehouse_code: str = Field(min_length=1, max_length=80, description="仓库编码")


class WmsPalletArrivedEvent(BaseModel):
    """WMS 料盘到达回调事件 (normalizer 输出)。"""

    model_config = ConfigDict(extra="forbid")

    envelope: InboundEventEnvelope = Field(description="共享 envelope")
    pallet_id: str = Field(min_length=1, max_length=80, description="料盘 ID")
    arrived_station: str = Field(min_length=1, max_length=80, description="到达工位编码")


class WmsRackArrivedEvent(BaseModel):
    """WMS 货架到达回调事件 (normalizer 输出)。"""

    model_config = ConfigDict(extra="forbid")

    envelope: InboundEventEnvelope = Field(description="共享 envelope")
    rack_id: str = Field(min_length=1, max_length=80, description="货架 ID")
    station_code: str = Field(min_length=1, max_length=80, description="到达工位编码")


class WmsTransportCompletedEvent(BaseModel):
    """WMS 搬运完成回调事件 (normalizer 输出)。"""

    model_config = ConfigDict(extra="forbid")

    envelope: InboundEventEnvelope = Field(description="共享 envelope")
    request_id: str = Field(min_length=1, max_length=80, description="WMS 履约请求号")
    completed_at: str = Field(description="完成时间 ISO 8601")
    result_code: str = Field(description="SUCCESS / FAILED / PARTIAL")


class InboundEventPort(Protocol):
    """所有入站 normalizer 的基协议。

    不导出到业务 capability (主计划 §3.5 I3 + H2 黑名单)。
    实际 normalizer (WmsEventPort 等) 继承此协议。
    """

    def normalize(self, raw_payload: dict) -> InboundEventEnvelope:
        """把原始回调 payload 标准化为 InboundEventEnvelope。"""
        ...


class WmsEventPort(Protocol):
    """WMS 回调 normalizer。

    4 个 normalizer 覆盖 WMS 主回调事件类型。normalizer 输出投递到
    RuntimeInbox；持久化后的 canonical payload 由 RuntimeInboxProcessorBridge 处理，
    normalizer 不直接调用业务 capability
    (主计划 §3.5.1 + H2 黑名单)。
    """

    def normalize_wms_grn_received(self, raw_payload: dict) -> WmsGrnReceivedEvent:
        """标准化 WMS_GRN_RECEIVED 回调 → typed event + correlation_id。"""
        ...

    def normalize_wms_pallet_arrived(self, raw_payload: dict) -> WmsPalletArrivedEvent:
        """标准化 WMS_PALLET_ARRIVED 回调 → typed event + correlation_id。"""
        ...

    def normalize_wms_rack_arrived(self, raw_payload: dict) -> WmsRackArrivedEvent:
        """标准化 WMS_RACK_ARRIVED 回调 → typed event + correlation_id。"""
        ...

    def normalize_wms_transport_completed(self, raw_payload: dict) -> WmsTransportCompletedEvent:
        """标准化 WMS_TRANSPORT_COMPLETED 回调 → typed event + correlation_id。"""
        ...
