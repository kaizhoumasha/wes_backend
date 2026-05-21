# pyright: reportIncompatibleVariableOverride=false
"""WES 运行时资源底座模型。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - SQLModel 字段解析需要运行时类型
from enum import Enum
from typing import Any, ClassVar, Literal, cast

from pydantic import BaseModel
from pydantic import Field as PydanticField
from sqlalchemy import JSON, Column, Index
from sqlalchemy import Enum as SQLAEnum
from sqlmodel import Field

from src.core.mixins import BaseMixin, DataTableMixin
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType


class ResourceType(str, Enum):
    """WES 运行时资源类型。"""

    RACK = "RACK"
    BIN = "BIN"
    MATERIAL = "MATERIAL"


class ResourceSourceSystem(str, Enum):
    """资源事实来源系统。"""

    WMS = "WMS"
    RCS = "RCS"
    ECS = "ECS"
    WES_RUNTIME = "WES_RUNTIME"
    MANUAL_IMPORT = "MANUAL_IMPORT"
    MANUAL = "MANUAL"


class ResourceRef(BaseModel):
    """跨模块运行时资源引用，不复制 WorkLine/Device 主数据。"""

    resource_type: ResourceType = PydanticField(description="资源类型")
    resource_code: str = PydanticField(min_length=1, max_length=100, description="资源业务编码")
    source_system: ResourceSourceSystem | None = PydanticField(default=None, description="来源系统")
    source_version: str | None = PydanticField(default=None, max_length=100, description="来源版本")
    display_name: str | None = PydanticField(default=None, max_length=200, description="展示名称")


class ResourceMasterStatus(str, Enum):
    """资源主数据启停状态。"""

    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class RackKind(str, Enum):
    """货架物理结构类型。"""

    SINGLE_LAYER = "SINGLE_LAYER"
    FIVE_LAYER = "FIVE_LAYER"
    RETURN = "RETURN"
    TRANSFER = "TRANSFER"
    PRODUCTION = "PRODUCTION"


class RackSlotKind(str, Enum):
    """货架槽位承载对象类型。"""

    BIN_SLOT = "BIN_SLOT"
    MATERIAL_SLOT = "MATERIAL_SLOT"


class RackSlotSide(str, Enum):
    """货架槽位面。"""

    A = "A"
    B = "B"
    NONE = "NONE"


class BinSlotSize(str, Enum):
    """料箱内部槽位尺寸。"""

    SEVEN_INCH = "7INCH"
    THIRTEEN_INCH = "13INCH"
    FIFTEEN_INCH = "15INCH"
    LARGE = "LARGE"


class ResourceStateEventType(str, Enum):
    """资源事实事件类型。"""

    RACK_ARRIVED = "RACK_ARRIVED"
    RACK_DEPARTED = "RACK_DEPARTED"
    BIN_MOUNTED = "BIN_MOUNTED"
    BIN_UNMOUNTED = "BIN_UNMOUNTED"
    MATERIAL_MOUNTED = "MATERIAL_MOUNTED"
    MATERIAL_UNMOUNTED = "MATERIAL_UNMOUNTED"
    EXCHANGE_STATUS_UPDATED = "EXCHANGE_STATUS_UPDATED"
    RESOURCE_RECONCILED = "RESOURCE_RECONCILED"


class RackPlacementStatus(str, Enum):
    """货架位置投影状态。"""

    ARRIVED = "ARRIVED"
    IN_TRANSIT = "IN_TRANSIT"
    DEPARTED = "DEPARTED"
    UNKNOWN = "UNKNOWN"


class RackBinMountStatus(str, Enum):
    """料箱挂载投影状态。"""

    MOUNTED = "MOUNTED"
    UNMOUNTED = "UNMOUNTED"
    EXCHANGING = "EXCHANGING"
    UNKNOWN = "UNKNOWN"


class BinMaterialMountStatus(str, Enum):
    """物料料箱格位投影状态。"""

    OCCUPIED = "OCCUPIED"
    REMOVED = "REMOVED"
    LOCKED = "LOCKED"
    UNKNOWN = "UNKNOWN"


class BinCellOccupancyStatus(str, Enum):
    """料箱格位聚合占用状态。"""

    OCCUPIED = "OCCUPIED"
    FULL = "FULL"
    REMOVED = "REMOVED"
    UNKNOWN = "UNKNOWN"


class WmsConfirmationStatus(str, Enum):
    """WMS 确认状态。"""

    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    NOT_REQUIRED = "NOT_REQUIRED"


class BinContentSnapshotStatus(str, Enum):
    """料箱内容快照完整性。"""

    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"


class RackTypeBase(BaseMixin):
    """货架类型基础字段。"""

    rack_type_code: str = Field(min_length=1, max_length=50, index=True, description="货架类型编码")
    rack_type_name: str = Field(min_length=1, max_length=100, description="货架类型名称")
    rack_kind: RackKind = Field(
        sa_type=cast("Any", SQLAEnum(RackKind, native_enum=False, create_constraint=True, length=50)),
        description="货架物理结构类型",
    )
    slot_count: int = Field(ge=1, description="标准槽位数量")
    has_side: bool = Field(default=False, description="是否区分 A/B 面")
    description: str | None = Field(default=None, max_length=500, description="说明")
    active: bool = Field(default=True, description="是否启用")
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON), description="扩展属性")


class RackType(RackTypeBase, DataTableMixin, table=True):
    """货架物理结构定义。"""

    __tablename__: ClassVar[Literal["resource_rack_types"]] = "resource_rack_types"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (Index("ux_resource_rack_types_code", "rack_type_code", unique=True),)


class RackSlotTemplateBase(BaseMixin):
    """货架槽位模板基础字段。"""

    rack_type_code: str = Field(min_length=1, max_length=50, index=True, description="所属货架类型编码")
    slot_code: str = Field(min_length=1, max_length=50, description="货架槽位编码")
    side: RackSlotSide = Field(
        default=RackSlotSide.NONE,
        sa_type=cast("Any", SQLAEnum(RackSlotSide, native_enum=False, create_constraint=True, length=20)),
        description="槽位面",
    )
    layer_no: int = Field(default=1, ge=1, description="层号")
    position_no: int = Field(default=1, ge=1, description="同层序号")
    slot_kind: RackSlotKind = Field(
        sa_type=cast("Any", SQLAEnum(RackSlotKind, native_enum=False, create_constraint=True, length=50)),
        description="槽位承载对象类型",
    )
    allowed_bin_types: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSON),
        description="允许的料箱类型",
    )
    allowed_material_carrier_types: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSON),
        description="允许的物料承载形态",
    )
    active: bool = Field(default=True, description="是否启用")


class RackSlotTemplate(RackSlotTemplateBase, DataTableMixin, table=True):
    """货架槽位模板。"""

    __tablename__: ClassVar[Literal["resource_rack_slot_templates"]] = "resource_rack_slot_templates"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index(
            "ux_resource_rack_slot_templates_type_slot",
            "rack_type_code",
            "slot_code",
            unique=True,
        ),
    )


class RackBase(BaseMixin):
    """货架实例基础字段。"""

    rack_code: str = Field(min_length=1, max_length=80, index=True, description="WES 货架编码")
    wms_rack_id: str | None = Field(default=None, max_length=100, description="WMS 货架 ID")
    rack_type_code: str = Field(min_length=1, max_length=50, index=True, description="货架类型编码")
    status: ResourceMasterStatus = Field(
        default=ResourceMasterStatus.ACTIVE,
        sa_type=cast("Any", SQLAEnum(ResourceMasterStatus, native_enum=False, create_constraint=True, length=50)),
        description="货架主数据状态",
    )
    source_system: ResourceSourceSystem = Field(
        default=ResourceSourceSystem.MANUAL_IMPORT,
        sa_type=cast("Any", SQLAEnum(ResourceSourceSystem, native_enum=False, create_constraint=True, length=50)),
        description="来源系统",
    )
    source_version: str | None = Field(default=None, max_length=100, description="来源版本")
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON), description="扩展属性")


class Rack(RackBase, DataTableMixin, table=True):
    """物理货架实例。"""

    __tablename__: ClassVar[Literal["resource_racks"]] = "resource_racks"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (Index("ux_resource_racks_code", "rack_code", unique=True),)


class BinTypeBase(BaseMixin):
    """料箱类型基础字段。"""

    bin_type_code: str = Field(min_length=1, max_length=50, index=True, description="料箱类型编码")
    bin_type_name: str = Field(min_length=1, max_length=100, description="料箱类型名称")
    description: str | None = Field(default=None, max_length=500, description="说明")
    active: bool = Field(default=True, description="是否启用")
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON), description="扩展属性")


class BinType(BinTypeBase, DataTableMixin, table=True):
    """料箱内部结构定义。"""

    __tablename__: ClassVar[Literal["resource_bin_types"]] = "resource_bin_types"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (Index("ux_resource_bin_types_code", "bin_type_code", unique=True),)


class BinSlotTemplateBase(BaseMixin):
    """料箱内部槽位模板基础字段。"""

    bin_type_code: str = Field(min_length=1, max_length=50, index=True, description="所属料箱类型编码")
    bin_slot_code: str = Field(min_length=1, max_length=50, description="料箱内槽位编码")
    slot_size: BinSlotSize = Field(
        sa_type=cast("Any", SQLAEnum(BinSlotSize, native_enum=False, create_constraint=True, length=20)),
        description="槽位尺寸",
    )
    max_depth_mm: int | None = Field(default=None, ge=1, description="最大深度")
    max_weight_g: int | None = Field(default=None, ge=1, description="最大重量")
    active: bool = Field(default=True, description="是否启用")
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON), description="扩展属性")


class BinSlotTemplate(BinSlotTemplateBase, DataTableMixin, table=True):
    """料箱内部槽位模板。"""

    __tablename__: ClassVar[Literal["resource_bin_slot_templates"]] = "resource_bin_slot_templates"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index(
            "ux_resource_bin_slot_templates_type_slot",
            "bin_type_code",
            "bin_slot_code",
            unique=True,
        ),
    )


class BinBase(BaseMixin):
    """料箱实例基础字段。"""

    bin_code: str = Field(min_length=1, max_length=80, index=True, description="WES 料箱编码")
    wms_bin_id: str | None = Field(default=None, max_length=100, description="WMS 料箱 ID")
    bin_type_code: str = Field(min_length=1, max_length=50, index=True, description="料箱类型编码")
    status: ResourceMasterStatus = Field(
        default=ResourceMasterStatus.ACTIVE,
        sa_type=cast("Any", SQLAEnum(ResourceMasterStatus, native_enum=False, create_constraint=True, length=50)),
        description="料箱主数据状态",
    )
    source_system: ResourceSourceSystem = Field(
        default=ResourceSourceSystem.MANUAL_IMPORT,
        sa_type=cast("Any", SQLAEnum(ResourceSourceSystem, native_enum=False, create_constraint=True, length=50)),
        description="来源系统",
    )
    source_version: str | None = Field(default=None, max_length=100, description="来源版本")
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON), description="扩展属性")


class Bin(BinBase, DataTableMixin, table=True):
    """物理料箱实例。"""

    __tablename__: ClassVar[Literal["resource_bins"]] = "resource_bins"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (Index("ux_resource_bins_code", "bin_code", unique=True),)


class ResourceStateEventBase(BaseMixin):
    """资源 append-only 事实基础字段。"""

    event_code: str = Field(min_length=1, max_length=160, index=True, description="资源事件唯一编码")
    idempotency_key: str | None = Field(default=None, max_length=240, index=True, description="资源事实幂等键")
    event_type: ResourceStateEventType = Field(
        sa_type=cast("Any", SQLAEnum(ResourceStateEventType, native_enum=False, create_constraint=True, length=80)),
        description="资源事件类型",
    )
    resource_type: ResourceType = Field(
        sa_type=cast("Any", SQLAEnum(ResourceType, native_enum=False, create_constraint=True, length=50)),
        description="资源类型",
    )
    resource_code: str = Field(min_length=1, max_length=120, index=True, description="资源编码")
    source_system: ResourceSourceSystem = Field(
        sa_type=cast("Any", SQLAEnum(ResourceSourceSystem, native_enum=False, create_constraint=True, length=50)),
        description="来源系统",
    )
    source_event_id: str = Field(min_length=1, max_length=200, index=True, description="来源事件 ID")
    source_version: str | None = Field(default=None, max_length=100, description="来源版本")
    trace_id: str | None = Field(default=None, max_length=100, index=True, description="WorkLine trace")
    session_id: str | None = Field(default=None, max_length=100, index=True, description="WorkLine Session")
    workline_id: int | None = Field(default=None, index=True, description="关联 WorkLine.id")
    workline_code: str | None = Field(default=None, max_length=50, index=True, description="工作线编码")
    position_code: str | None = Field(default=None, max_length=80, index=True, description="工作线停靠位编码")
    logic_location_code: str | None = Field(default=None, max_length=120, index=True, description="WES 逻辑位置")
    external_location_code: str | None = Field(default=None, max_length=120, index=True, description="外部地码证据")
    payload_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON), description="事件事实")
    occurred_at: datetime = Field(description="事实发生时间")
    received_at: datetime = Field(description="WES 接收时间")


class ResourceStateEvent(ResourceStateEventBase, DataTableMixin, table=True):
    """资源 append-only 事实账本。"""

    __tablename__: ClassVar[Literal["resource_state_events"]] = "resource_state_events"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index("ux_resource_state_events_event_code", "event_code", unique=True),
        Index(
            "ux_resource_state_events_idempotency",
            "idempotency_key",
            unique=True,
            postgresql_where="idempotency_key IS NOT NULL",
        ),
        Index("ix_resource_state_events_source_event", "source_system", "source_event_id"),
        Index("ix_resource_state_events_resource_time", "resource_type", "resource_code", "occurred_at"),
    )


class RackPlacementBase(BaseMixin):
    """货架当前工作线停靠位投影基础字段。"""

    rack_code: str = Field(min_length=1, max_length=80, index=True, description="货架编码")
    rack_kind: RackKind | None = Field(
        default=None,
        sa_type=cast("Any", SQLAEnum(RackKind, native_enum=False, create_constraint=True, length=50)),
        description="货架类型",
    )
    location_code: str | None = Field(default=None, max_length=80, index=True, description="兼容地码或逻辑位置")
    workline_id: int | None = Field(default=None, index=True, description="关联 WorkLine.id")
    workline_code: str | None = Field(default=None, max_length=50, index=True, description="工作线编码")
    position_code: str | None = Field(default=None, max_length=80, index=True, description="工作线停靠位编码")
    position_role: str | None = Field(default=None, max_length=80, index=True, description="工作线停靠位角色")
    logic_location_code: str | None = Field(default=None, max_length=120, index=True, description="WES 逻辑位置")
    external_location_code: str | None = Field(default=None, max_length=120, index=True, description="外部地码证据")
    placement_status: RackPlacementStatus = Field(
        default=RackPlacementStatus.UNKNOWN,
        sa_type=cast("Any", SQLAEnum(RackPlacementStatus, native_enum=False, create_constraint=True, length=50)),
        description="位置投影状态",
    )
    source_system: ResourceSourceSystem = Field(
        sa_type=cast("Any", SQLAEnum(ResourceSourceSystem, native_enum=False, create_constraint=True, length=50)),
        description="来源系统",
    )
    source_task_id: str | None = Field(default=None, max_length=120, description="WMS/RCS 搬运任务 ID")
    source_event_id: str = Field(min_length=1, max_length=200, index=True, description="来源事件 ID")
    source_version: str | None = Field(default=None, max_length=100, description="来源版本")
    trace_id: str | None = Field(default=None, max_length=100, index=True, description="WorkLine trace")
    session_id: str | None = Field(default=None, max_length=100, index=True, description="WorkLine Session")
    started_at: datetime = Field(description="进入该关系的时间")
    ended_at: datetime | None = Field(default=None, index=True, description="离开该关系的时间")


class RackPlacement(RackPlacementBase, DataTableMixin, table=True):
    """货架处于哪个工作线停靠位的当前投影与历史。"""

    __tablename__: ClassVar[Literal["resource_rack_placements"]] = "resource_rack_placements"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index(
            "ux_resource_rack_placements_active_rack",
            "rack_code",
            unique=True,
            postgresql_where="ended_at IS NULL",
        ),
        Index(
            "ix_resource_rack_placements_workline_position_active",
            "workline_code",
            "position_code",
            "ended_at",
        ),
        Index("ix_resource_rack_placements_location_active", "location_code", "ended_at"),
    )


class RackBinMountBase(BaseMixin):
    """料箱挂载投影基础字段。"""

    rack_code: str = Field(min_length=1, max_length=80, index=True, description="货架编码")
    rack_slot_code: str = Field(min_length=1, max_length=50, index=True, description="货架槽位编码")
    bin_code: str = Field(min_length=1, max_length=80, index=True, description="料箱编码")
    mount_status: RackBinMountStatus = Field(
        default=RackBinMountStatus.UNKNOWN,
        sa_type=cast("Any", SQLAEnum(RackBinMountStatus, native_enum=False, create_constraint=True, length=50)),
        description="料箱挂载状态",
    )
    source_system: ResourceSourceSystem = Field(
        sa_type=cast("Any", SQLAEnum(ResourceSourceSystem, native_enum=False, create_constraint=True, length=50)),
        description="来源系统",
    )
    source_event_id: str = Field(min_length=1, max_length=200, index=True, description="来源事件 ID")
    source_version: str | None = Field(default=None, max_length=100, description="来源版本")
    trace_id: str | None = Field(default=None, max_length=100, index=True, description="WorkLine trace")
    session_id: str | None = Field(default=None, max_length=100, index=True, description="WorkLine Session")
    started_at: datetime = Field(description="挂载确认时间")
    ended_at: datetime | None = Field(default=None, index=True, description="解除挂载时间")


class RackBinMount(RackBinMountBase, DataTableMixin, table=True):
    """料箱挂载在哪个货架槽位的当前投影与历史。"""

    __tablename__: ClassVar[Literal["resource_rack_bin_mounts"]] = "resource_rack_bin_mounts"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index(
            "ux_resource_rack_bin_mounts_active_slot",
            "rack_code",
            "rack_slot_code",
            unique=True,
            postgresql_where="ended_at IS NULL",
        ),
        Index(
            "ux_resource_rack_bin_mounts_active_bin",
            "bin_code",
            unique=True,
            postgresql_where="ended_at IS NULL",
        ),
    )


class BinMaterialMountBase(BaseMixin):
    """料盘/PKG 料箱格位明细基础字段。"""

    bin_cell_occupancy_id: int | None = Field(default=None, index=True, description="关联料箱格位聚合占用 ID")
    cell_stack_position: int = Field(default=1, ge=1, index=True, description="同一料格内入格顺序，1 为最早入格")
    bin_code: str = Field(min_length=1, max_length=80, index=True, description="料箱编码")
    bin_cell_code: str | None = Field(default=None, max_length=80, index=True, description="料箱内部格位编码")
    bin_cell_index: str = Field(min_length=1, max_length=20, index=True, description="料箱内部格位序号")
    material_identity_key: str = Field(min_length=1, max_length=300, index=True, description="物料属性身份键")
    pkg_code: str | None = Field(default=None, max_length=200, index=True, description="PKG 展示字段")
    material_code: str | None = Field(default=None, max_length=120, index=True, description="物料编码引用")
    lot_code: str | None = Field(default=None, max_length=120, description="批次展示字段")
    date_code: str | None = Field(default=None, max_length=80, description="Date Code")
    qty_snapshot: float | None = Field(default=None, ge=0, description="当时执行过程看到的数量")
    reel_diameter: str | None = Field(default=None, max_length=80, description="料盘直径")
    reel_thickness: str | None = Field(default=None, max_length=80, description="料盘厚度")
    wms_inventory_id: str | None = Field(default=None, max_length=120, index=True, description="WMS 库存记录引用")
    wms_inventory_version: str | None = Field(default=None, max_length=120, description="WMS 库存或分拆版本引用")
    wms_confirmation_status: WmsConfirmationStatus = Field(
        default=WmsConfirmationStatus.PENDING,
        sa_type=cast("Any", SQLAEnum(WmsConfirmationStatus, native_enum=False, create_constraint=True, length=50)),
        description="WMS 确认状态",
    )
    writeback_evidence_id: int | None = Field(default=None, description="关联 WMS 回写证据")
    mount_status: BinMaterialMountStatus = Field(
        default=BinMaterialMountStatus.UNKNOWN,
        sa_type=cast("Any", SQLAEnum(BinMaterialMountStatus, native_enum=False, create_constraint=True, length=50)),
        description="物料占用状态",
    )
    source_system: ResourceSourceSystem = Field(
        sa_type=cast("Any", SQLAEnum(ResourceSourceSystem, native_enum=False, create_constraint=True, length=50)),
        description="来源系统",
    )
    source_event_id: str = Field(min_length=1, max_length=200, index=True, description="来源事件 ID")
    source_version: str | None = Field(default=None, max_length=100, description="来源版本")
    trace_id: str | None = Field(default=None, max_length=100, index=True, description="WorkLine trace")
    session_id: str | None = Field(default=None, max_length=100, index=True, description="WorkLine Session")
    started_at: datetime = Field(description="占用确认时间")
    ended_at: datetime | None = Field(default=None, index=True, description="离开料箱格位时间")


class BinCellOccupancyBase(BaseMixin):
    """料箱格位当前聚合占用基础字段。"""

    bin_code: str = Field(min_length=1, max_length=80, index=True, description="料箱编码")
    bin_cell_code: str | None = Field(default=None, max_length=80, index=True, description="料箱内部格位编码")
    bin_cell_index: str = Field(min_length=1, max_length=20, index=True, description="料箱内部格位序号")
    material_identity_key: str = Field(min_length=1, max_length=300, index=True, description="物料属性身份键")
    material_code: str | None = Field(default=None, max_length=120, index=True, description="物料编码引用")
    lot_code: str | None = Field(default=None, max_length=120, description="批次展示字段")
    date_code: str | None = Field(default=None, max_length=80, description="Date Code")
    reel_count: int = Field(default=0, ge=0, description="当前格位内 active 料盘数量")
    used_depth_mm: float = Field(default=0.0, ge=0, description="当前格位已使用深度")
    capacity_depth_mm: float | None = Field(default=None, ge=0, description="当前格位可用总深度")
    remaining_depth_mm: float | None = Field(default=None, ge=0, description="当前格位剩余深度")
    occupancy_status: BinCellOccupancyStatus = Field(
        default=BinCellOccupancyStatus.UNKNOWN,
        sa_type=cast("Any", SQLAEnum(BinCellOccupancyStatus, native_enum=False, create_constraint=True, length=50)),
        description="格位聚合占用状态",
    )
    source_system: ResourceSourceSystem = Field(
        sa_type=cast("Any", SQLAEnum(ResourceSourceSystem, native_enum=False, create_constraint=True, length=50)),
        description="来源系统",
    )
    source_event_id: str = Field(min_length=1, max_length=200, index=True, description="最近来源事件 ID")
    source_version: str | None = Field(default=None, max_length=100, description="来源版本")
    trace_id: str | None = Field(default=None, max_length=100, index=True, description="WorkLine trace")
    session_id: str | None = Field(default=None, max_length=100, index=True, description="最近 WorkLine Session")
    started_at: datetime = Field(description="首次占用确认时间")
    ended_at: datetime | None = Field(default=None, index=True, description="格位占用结束时间")
    metadata_json: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False), description="扩展属性"
    )


class BinCellOccupancy(BinCellOccupancyBase, DataTableMixin, table=True):
    """料箱格位当前聚合占用。"""

    __tablename__: ClassVar[Literal["resource_bin_cell_occupancies"]] = "resource_bin_cell_occupancies"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index(
            "ux_resource_bin_cell_occupancies_active_cell",
            "bin_code",
            "bin_cell_index",
            unique=True,
            postgresql_where="ended_at IS NULL",
        ),
        Index("ix_resource_bin_cell_occupancies_identity_active", "material_identity_key", "ended_at"),
    )


class BinMaterialMount(BinMaterialMountBase, DataTableMixin, table=True):
    """物料/PKG/料盘占用哪个料箱内部格位的明细与历史。"""

    __tablename__: ClassVar[Literal["resource_bin_material_mounts"]] = "resource_bin_material_mounts"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index(
            "ux_resource_bin_material_mounts_active_pkg",
            "pkg_code",
            unique=True,
            postgresql_where="ended_at IS NULL AND pkg_code IS NOT NULL",
        ),
        Index(
            "ux_resource_bin_material_mounts_active_wms_inventory",
            "wms_inventory_id",
            unique=True,
            postgresql_where="ended_at IS NULL AND wms_inventory_id IS NOT NULL",
        ),
        Index("ix_resource_bin_material_mounts_identity_active", "material_identity_key", "ended_at"),
        Index("ix_resource_bin_material_mounts_occupancy_active", "bin_cell_occupancy_id", "ended_at"),
        Index(
            "ux_resource_bin_material_mounts_active_stack_position",
            "bin_cell_occupancy_id",
            "cell_stack_position",
            unique=True,
            postgresql_where="ended_at IS NULL AND bin_cell_occupancy_id IS NOT NULL",
        ),
        Index(
            "ix_resource_bin_material_mounts_cell_stack_active",
            "bin_code",
            "bin_cell_index",
            "cell_stack_position",
            "ended_at",
        ),
    )


class BinContentSnapshotBase(BaseMixin):
    """料箱内部过程内容快照头基础字段。"""

    snapshot_id: str = Field(min_length=1, max_length=160, index=True, description="快照业务 ID")
    bin_code: str = Field(min_length=1, max_length=80, index=True, description="料箱编码")
    source_session_id: int | None = Field(default=None, index=True, description="产生快照的 WorklineSession")
    source_event_id: str | None = Field(default=None, max_length=200, index=True, description="来源事件或命令结果")
    captured_at: datetime = Field(index=True, description="快照时间")
    snapshot_status: BinContentSnapshotStatus = Field(
        default=BinContentSnapshotStatus.UNKNOWN,
        sa_type=cast("Any", SQLAEnum(BinContentSnapshotStatus, native_enum=False, create_constraint=True, length=50)),
        description="快照完整性",
    )
    snapshot_reason: str | None = Field(default=None, max_length=80, index=True, description="快照原因")
    snapshot_group_key: str | None = Field(default=None, max_length=160, index=True, description="快照分组键")
    snapshot_hash: str = Field(min_length=1, max_length=128, description="快照头和明细稳定摘要")
    wms_snapshot_version: str | None = Field(default=None, max_length=160, description="WMS 查询版本或时间")


class BinContentSnapshot(BinContentSnapshotBase, DataTableMixin, table=True):
    """料箱内部过程内容快照头。"""

    __tablename__: ClassVar[Literal["resource_bin_content_snapshots"]] = "resource_bin_content_snapshots"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index("ux_resource_bin_content_snapshots_snapshot_id", "snapshot_id", unique=True),
        Index("ix_resource_bin_content_snapshots_bin_time", "bin_code", "captured_at"),
    )


class BinContentSnapshotItemBase(BaseMixin):
    """料箱内部过程内容快照明细基础字段。"""

    snapshot_id: str = Field(min_length=1, max_length=160, index=True, description="所属快照业务 ID")
    bin_cell_code: str | None = Field(default=None, max_length=80, index=True, description="料箱内部格位编码")
    bin_cell_index: str | None = Field(default=None, max_length=20, index=True, description="料箱内部格位序号")
    pkg_code: str | None = Field(default=None, max_length=200, index=True, description="PKG 展示字段")
    material_code: str | None = Field(default=None, max_length=120, index=True, description="物料编码引用")
    vendor_code: str | None = Field(default=None, max_length=120, description="供应商引用")
    lot_code: str | None = Field(default=None, max_length=120, description="批次展示字段")
    date_code: str | None = Field(default=None, max_length=80, description="Date Code")
    qty_snapshot: float | None = Field(default=None, ge=0, description="当时执行过程看到的数量")
    thickness_mm: float | None = Field(default=None, ge=0, description="厚度")
    dims_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False), description="尺寸")
    wms_inventory_id: str | None = Field(default=None, max_length=160, index=True, description="WMS 库存记录引用")


class BinContentSnapshotItem(BinContentSnapshotItemBase, DataTableMixin, table=True):
    """料箱内部过程内容快照明细。"""

    __tablename__: ClassVar[Literal["resource_bin_content_snapshot_items"]] = "resource_bin_content_snapshot_items"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index("ix_resource_bin_content_snapshot_items_snapshot", "snapshot_id"),
        Index("ix_resource_bin_content_snapshot_items_pkg", "pkg_code"),
    )


class RackTypeCreate(ModelFactory(RackTypeBase).for_create()):
    """货架类型创建 Schema。"""


class RackTypeUpdate(ModelFactory(RackTypeBase).for_update()):
    """货架类型更新 Schema。"""


class RackTypeResponse(RackTypeBase):
    """货架类型响应 Schema。"""

    id: int


class RackSlotTemplateCreate(ModelFactory(RackSlotTemplateBase).for_create()):
    """货架槽位模板创建 Schema。"""


class RackSlotTemplateUpdate(ModelFactory(RackSlotTemplateBase).for_update()):
    """货架槽位模板更新 Schema。"""


class RackSlotTemplateResponse(RackSlotTemplateBase):
    """货架槽位模板响应 Schema。"""

    id: int


class RackCreate(ModelFactory(RackBase).for_create()):
    """货架实例创建 Schema。"""


class RackUpdate(ModelFactory(RackBase).for_update()):
    """货架实例更新 Schema。"""


class RackResponse(RackBase):
    """货架实例响应 Schema。"""

    id: int


class BinTypeCreate(ModelFactory(BinTypeBase).for_create()):
    """料箱类型创建 Schema。"""


class BinTypeUpdate(ModelFactory(BinTypeBase).for_update()):
    """料箱类型更新 Schema。"""


class BinTypeResponse(BinTypeBase):
    """料箱类型响应 Schema。"""

    id: int


class BinSlotTemplateCreate(ModelFactory(BinSlotTemplateBase).for_create()):
    """料箱槽位模板创建 Schema。"""


class BinSlotTemplateUpdate(ModelFactory(BinSlotTemplateBase).for_update()):
    """料箱槽位模板更新 Schema。"""


class BinSlotTemplateResponse(BinSlotTemplateBase):
    """料箱槽位模板响应 Schema。"""

    id: int


class BinCreate(ModelFactory(BinBase).for_create()):
    """料箱实例创建 Schema。"""


class BinUpdate(ModelFactory(BinBase).for_update()):
    """料箱实例更新 Schema。"""


class BinResponse(BinBase):
    """料箱实例响应 Schema。"""

    id: int


class ResourceStateEventCreate(ModelFactory(ResourceStateEventBase).for_create()):
    """资源事实创建 Schema。"""


class ResourceStateEventUpdate(ModelFactory(ResourceStateEventBase).for_update()):
    """资源事实更新 Schema。"""


class ResourceStateEventResponse(ResourceStateEventBase):
    """资源事实响应 Schema。"""

    id: int


class RackPlacementCreate(ModelFactory(RackPlacementBase).for_create()):
    """货架位置投影创建 Schema。"""


class RackPlacementUpdate(ModelFactory(RackPlacementBase).for_update()):
    """货架位置投影更新 Schema。"""


class RackPlacementResponse(RackPlacementBase):
    """货架位置投影响应 Schema。"""

    id: int


class RackBinMountCreate(ModelFactory(RackBinMountBase).for_create()):
    """料箱挂载投影创建 Schema。"""


class RackBinMountUpdate(ModelFactory(RackBinMountBase).for_update()):
    """料箱挂载投影更新 Schema。"""


class RackBinMountResponse(RackBinMountBase):
    """料箱挂载投影响应 Schema。"""

    id: int


class BinMaterialMountCreate(ModelFactory(BinMaterialMountBase).for_create()):
    """物料料箱格位投影创建 Schema。"""


class BinMaterialMountUpdate(ModelFactory(BinMaterialMountBase).for_update()):
    """物料料箱格位投影更新 Schema。"""


class BinMaterialMountResponse(BinMaterialMountBase):
    """物料料箱格位投影响应 Schema。"""

    id: int


class BinCellOccupancyCreate(ModelFactory(BinCellOccupancyBase).for_create()):
    """料箱格位聚合占用创建 Schema。"""


class BinCellOccupancyUpdate(ModelFactory(BinCellOccupancyBase).for_update()):
    """料箱格位聚合占用更新 Schema。"""


class BinCellOccupancyResponse(BinCellOccupancyBase):
    """料箱格位聚合占用响应 Schema。"""

    id: int


class BinContentSnapshotCreate(ModelFactory(BinContentSnapshotBase).for_create()):
    """料箱内容快照头创建 Schema。"""


class BinContentSnapshotUpdate(ModelFactory(BinContentSnapshotBase).for_update()):
    """料箱内容快照头更新 Schema。"""


class BinContentSnapshotResponse(BinContentSnapshotBase):
    """料箱内容快照头响应 Schema。"""

    id: int


class BinContentSnapshotItemCreate(ModelFactory(BinContentSnapshotItemBase).for_create()):
    """料箱内容快照明细创建 Schema。"""


class BinContentSnapshotItemUpdate(ModelFactory(BinContentSnapshotItemBase).for_update()):
    """料箱内容快照明细更新 Schema。"""


class BinContentSnapshotItemResponse(BinContentSnapshotItemBase):
    """料箱内容快照明细响应 Schema。"""

    id: int


__all__ = [
    "Bin",
    "BinBase",
    "BinCellOccupancy",
    "BinCellOccupancyBase",
    "BinCellOccupancyCreate",
    "BinCellOccupancyResponse",
    "BinCellOccupancyStatus",
    "BinCellOccupancyUpdate",
    "BinContentSnapshot",
    "BinContentSnapshotBase",
    "BinContentSnapshotCreate",
    "BinContentSnapshotItem",
    "BinContentSnapshotItemBase",
    "BinContentSnapshotItemCreate",
    "BinContentSnapshotItemResponse",
    "BinContentSnapshotItemUpdate",
    "BinContentSnapshotResponse",
    "BinContentSnapshotStatus",
    "BinContentSnapshotUpdate",
    "BinCreate",
    "BinMaterialMount",
    "BinMaterialMountBase",
    "BinMaterialMountCreate",
    "BinMaterialMountResponse",
    "BinMaterialMountStatus",
    "BinMaterialMountUpdate",
    "BinResponse",
    "BinSlotSize",
    "BinSlotTemplate",
    "BinSlotTemplateBase",
    "BinSlotTemplateCreate",
    "BinSlotTemplateResponse",
    "BinSlotTemplateUpdate",
    "BinType",
    "BinTypeBase",
    "BinTypeCreate",
    "BinTypeResponse",
    "BinTypeUpdate",
    "BinUpdate",
    "Rack",
    "RackBase",
    "RackBinMount",
    "RackBinMountBase",
    "RackBinMountCreate",
    "RackBinMountResponse",
    "RackBinMountStatus",
    "RackBinMountUpdate",
    "RackCreate",
    "RackKind",
    "RackPlacement",
    "RackPlacementBase",
    "RackPlacementCreate",
    "RackPlacementResponse",
    "RackPlacementStatus",
    "RackPlacementUpdate",
    "RackResponse",
    "RackSlotKind",
    "RackSlotSide",
    "RackSlotTemplate",
    "RackSlotTemplateBase",
    "RackSlotTemplateCreate",
    "RackSlotTemplateResponse",
    "RackSlotTemplateUpdate",
    "RackType",
    "RackTypeBase",
    "RackTypeCreate",
    "RackTypeResponse",
    "RackTypeUpdate",
    "RackUpdate",
    "ResourceMasterStatus",
    "ResourceRef",
    "ResourceSourceSystem",
    "ResourceStateEvent",
    "ResourceStateEventBase",
    "ResourceStateEventCreate",
    "ResourceStateEventResponse",
    "ResourceStateEventType",
    "ResourceStateEventUpdate",
    "ResourceType",
    "WmsConfirmationStatus",
]
