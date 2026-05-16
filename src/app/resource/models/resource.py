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

from src.core.mixins import BaseMixin, DataTableMixin, EnterpriseMixin, SoftDeleteMixin
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType


class ResourceType(str, Enum):
    """WES 运行时资源类型。"""

    WORKLINE = "WORKLINE"
    DEVICE = "DEVICE"
    RACK = "RACK"
    BIN = "BIN"
    MATERIAL = "MATERIAL"
    LOCATION = "LOCATION"
    EXCHANGE_TASK = "EXCHANGE_TASK"


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


class ExecutionLocationStatus(str, Enum):
    """执行地码当前可用性状态。"""

    AVAILABLE = "AVAILABLE"
    OCCUPIED = "OCCUPIED"
    LOCKED = "LOCKED"
    DISABLED = "DISABLED"
    UNKNOWN = "UNKNOWN"


class ExecutionZoneType(str, Enum):
    """执行区域类型。"""

    KITTING = "KITTING"
    SMT_STORAGE = "SMT_STORAGE"
    FULL_BOX_EXCHANGE = "FULL_BOX_EXCHANGE"
    RETURN = "RETURN"
    LINE_BUFFER = "LINE_BUFFER"


class ExecutionLocationType(str, Enum):
    """执行地码类型。"""

    WORK_STATION = "WORK_STATION"
    BUFFER = "BUFFER"
    STORAGE = "STORAGE"
    EXCHANGE_SLOT = "EXCHANGE_SLOT"
    QUEUE_SLOT = "QUEUE_SLOT"


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


class RackStatus(str, Enum):
    """货架执行状态，不代表库存状态。"""

    AVAILABLE = "AVAILABLE"
    LOCKED = "LOCKED"
    IN_TRANSIT = "IN_TRANSIT"
    AT_WORKLINE = "AT_WORKLINE"
    IN_EXCHANGE = "IN_EXCHANGE"
    EXCEPTION = "EXCEPTION"
    DISABLED = "DISABLED"
    UNKNOWN = "UNKNOWN"


class BinStatus(str, Enum):
    """料箱执行状态，不代表 WMS 库存状态。"""

    EMPTY_VERIFIED = "EMPTY_VERIFIED"
    IN_USE = "IN_USE"
    LOCKED = "LOCKED"
    FULL_SNAPSHOT = "FULL_SNAPSHOT"
    EXCEPTION = "EXCEPTION"
    DISABLED = "DISABLED"
    UNKNOWN = "UNKNOWN"


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


class RackMaterialMountStatus(str, Enum):
    """物料卡槽投影状态。"""

    OCCUPIED = "OCCUPIED"
    REMOVED = "REMOVED"
    LOCKED = "LOCKED"
    UNKNOWN = "UNKNOWN"


class ResourceRelationSourceSystem(str, Enum):
    """资源关系投影来源。"""

    ECS = "ECS"
    WMS_RCS = "WMS_RCS"
    WES_RUNTIME = "WES_RUNTIME"
    MANUAL_RECONCILIATION = "MANUAL_RECONCILIATION"


class WmsSplitPolicy(str, Enum):
    """WMS 物料拆分策略。"""

    NOT_SPLITTABLE = "NOT_SPLITTABLE"
    SPLITTABLE = "SPLITTABLE"
    UNKNOWN = "UNKNOWN"


class WmsConfirmationStatus(str, Enum):
    """WMS 确认状态。"""

    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    NOT_REQUIRED = "NOT_REQUIRED"


class RackReleaseStatus(str, Enum):
    """单层货架释放周期状态。"""

    CANDIDATE = "CANDIDATE"
    INBOX_CREATED = "INBOX_CREATED"
    SESSION_STARTED = "SESSION_STARTED"
    EXCHANGE_REQUESTED = "EXCHANGE_REQUESTED"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    RECONCILING = "RECONCILING"
    CANCELLED = "CANCELLED"


class BinContentSnapshotStatus(str, Enum):
    """料箱内容快照完整性。"""

    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"


class FullBoxExchangeStatus(str, Enum):
    """满箱交换过程状态。"""

    REQUESTED = "REQUESTED"
    ACCEPTED = "ACCEPTED"
    QUEUED = "QUEUED"
    IN_PROGRESS = "IN_PROGRESS"
    PHYSICAL_COMPLETED = "PHYSICAL_COMPLETED"
    RESOURCE_PROJECTED = "RESOURCE_PROJECTED"
    WMS_CONFIRMED = "WMS_CONFIRMED"
    BUSINESS_COMPLETED = "BUSINESS_COMPLETED"
    WMS_REJECTED = "WMS_REJECTED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    RECONCILING = "RECONCILING"


class ExecutionZoneBase(BaseMixin):
    """执行区域基础字段。"""

    zone_code: str = Field(min_length=1, max_length=50, index=True, description="WES 区域编码")
    zone_name: str = Field(min_length=1, max_length=100, description="区域名称")
    zone_type: ExecutionZoneType = Field(
        sa_type=cast("Any", SQLAEnum(ExecutionZoneType, native_enum=False, create_constraint=True, length=50)),
        description="区域类型",
    )
    wms_zone_code: str | None = Field(default=None, max_length=100, description="WMS 区域引用")
    status: ResourceMasterStatus = Field(
        default=ResourceMasterStatus.ACTIVE,
        sa_type=cast("Any", SQLAEnum(ResourceMasterStatus, native_enum=False, create_constraint=True, length=50)),
        description="区域状态",
    )
    allowed_rack_types: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSON),
        description="允许进入的货架类型",
    )
    max_concurrent_tasks: int | None = Field(default=None, ge=1, description="并发任务上限")
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON), description="扩展属性")


class ExecutionZone(ExecutionZoneBase, EnterpriseMixin, SoftDeleteMixin, DataTableMixin, table=True):
    """WES 可识别的执行区域。"""

    __tablename__: ClassVar[Literal["resource_execution_zones"]] = "resource_execution_zones"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index("ux_resource_execution_zones_code_deleted", "zone_code", unique=True, postgresql_where="NOT is_deleted"),
    )


class ExecutionLocationBase(BaseMixin):
    """执行地码基础字段。"""

    location_code: str = Field(min_length=1, max_length=80, index=True, description="WES 地码编码")
    zone_code: str = Field(min_length=1, max_length=50, index=True, description="所属区域编码")
    location_type: ExecutionLocationType = Field(
        sa_type=cast("Any", SQLAEnum(ExecutionLocationType, native_enum=False, create_constraint=True, length=50)),
        description="地码类型",
    )
    wms_location_code: str | None = Field(default=None, max_length=100, description="WMS/RCS 地码引用")
    rack_capacity: int = Field(default=1, ge=1, description="可容纳货架数量")
    allowed_rack_types: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSON),
        description="允许货架类型",
    )
    status: ExecutionLocationStatus = Field(
        default=ExecutionLocationStatus.UNKNOWN,
        sa_type=cast("Any", SQLAEnum(ExecutionLocationStatus, native_enum=False, create_constraint=True, length=50)),
        description="地码状态",
    )
    coordinates_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON), description="RCS 坐标透传")


class ExecutionLocation(ExecutionLocationBase, EnterpriseMixin, SoftDeleteMixin, DataTableMixin, table=True):
    """地码、缓存位、工作站位置或交换区排队位。"""

    __tablename__: ClassVar[Literal["resource_execution_locations"]] = "resource_execution_locations"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index(
            "ux_resource_execution_locations_code_deleted",
            "location_code",
            unique=True,
            postgresql_where="NOT is_deleted",
        ),
    )


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


class RackType(RackTypeBase, EnterpriseMixin, SoftDeleteMixin, DataTableMixin, table=True):
    """货架物理结构定义。"""

    __tablename__: ClassVar[Literal["resource_rack_types"]] = "resource_rack_types"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index("ux_resource_rack_types_code_deleted", "rack_type_code", unique=True, postgresql_where="NOT is_deleted"),
    )


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


class RackSlotTemplate(RackSlotTemplateBase, EnterpriseMixin, SoftDeleteMixin, DataTableMixin, table=True):
    """货架槽位模板。"""

    __tablename__: ClassVar[Literal["resource_rack_slot_templates"]] = "resource_rack_slot_templates"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index(
            "ux_resource_rack_slot_templates_type_slot_deleted",
            "rack_type_code",
            "slot_code",
            unique=True,
            postgresql_where="NOT is_deleted",
        ),
    )


class RackBase(BaseMixin):
    """货架实例基础字段。"""

    rack_code: str = Field(min_length=1, max_length=80, index=True, description="WES 货架编码")
    wms_rack_id: str | None = Field(default=None, max_length=100, description="WMS 货架 ID")
    rack_type_code: str = Field(min_length=1, max_length=50, index=True, description="货架类型编码")
    status: RackStatus = Field(
        default=RackStatus.UNKNOWN,
        sa_type=cast("Any", SQLAEnum(RackStatus, native_enum=False, create_constraint=True, length=50)),
        description="货架执行状态",
    )
    current_location_code: str | None = Field(default=None, max_length=80, index=True, description="最后确认地码")
    last_seen_at: datetime | None = Field(default=None, description="最近一次现场确认时间")
    source_system: ResourceSourceSystem = Field(
        default=ResourceSourceSystem.MANUAL_IMPORT,
        sa_type=cast("Any", SQLAEnum(ResourceSourceSystem, native_enum=False, create_constraint=True, length=50)),
        description="来源系统",
    )
    source_version: str | None = Field(default=None, max_length=100, description="来源版本")
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON), description="扩展属性")


class Rack(RackBase, EnterpriseMixin, SoftDeleteMixin, DataTableMixin, table=True):
    """物理货架实例。"""

    __tablename__: ClassVar[Literal["resource_racks"]] = "resource_racks"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index("ux_resource_racks_code_deleted", "rack_code", unique=True, postgresql_where="NOT is_deleted"),
    )


class BinTypeBase(BaseMixin):
    """料箱类型基础字段。"""

    bin_type_code: str = Field(min_length=1, max_length=50, index=True, description="料箱类型编码")
    bin_type_name: str = Field(min_length=1, max_length=100, description="料箱类型名称")
    description: str | None = Field(default=None, max_length=500, description="说明")
    active: bool = Field(default=True, description="是否启用")
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON), description="扩展属性")


class BinType(BinTypeBase, EnterpriseMixin, SoftDeleteMixin, DataTableMixin, table=True):
    """料箱内部结构定义。"""

    __tablename__: ClassVar[Literal["resource_bin_types"]] = "resource_bin_types"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index("ux_resource_bin_types_code_deleted", "bin_type_code", unique=True, postgresql_where="NOT is_deleted"),
    )


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


class BinSlotTemplate(BinSlotTemplateBase, EnterpriseMixin, SoftDeleteMixin, DataTableMixin, table=True):
    """料箱内部槽位模板。"""

    __tablename__: ClassVar[Literal["resource_bin_slot_templates"]] = "resource_bin_slot_templates"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index(
            "ux_resource_bin_slot_templates_type_slot_deleted",
            "bin_type_code",
            "bin_slot_code",
            unique=True,
            postgresql_where="NOT is_deleted",
        ),
    )


class BinBase(BaseMixin):
    """料箱实例基础字段。"""

    bin_code: str = Field(min_length=1, max_length=80, index=True, description="WES 料箱编码")
    wms_bin_id: str | None = Field(default=None, max_length=100, description="WMS 料箱 ID")
    bin_type_code: str = Field(min_length=1, max_length=50, index=True, description="料箱类型编码")
    status: BinStatus = Field(
        default=BinStatus.UNKNOWN,
        sa_type=cast("Any", SQLAEnum(BinStatus, native_enum=False, create_constraint=True, length=50)),
        description="料箱执行状态",
    )
    last_seen_at: datetime | None = Field(default=None, description="最近一次现场确认时间")
    source_system: ResourceSourceSystem = Field(
        default=ResourceSourceSystem.MANUAL_IMPORT,
        sa_type=cast("Any", SQLAEnum(ResourceSourceSystem, native_enum=False, create_constraint=True, length=50)),
        description="来源系统",
    )
    source_version: str | None = Field(default=None, max_length=100, description="来源版本")
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON), description="扩展属性")


class Bin(BinBase, EnterpriseMixin, SoftDeleteMixin, DataTableMixin, table=True):
    """物理料箱实例。"""

    __tablename__: ClassVar[Literal["resource_bins"]] = "resource_bins"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index("ux_resource_bins_code_deleted", "bin_code", unique=True, postgresql_where="NOT is_deleted"),
    )


class ResourceStateEventBase(BaseMixin):
    """资源 append-only 事实基础字段。"""

    event_code: str = Field(min_length=1, max_length=160, index=True, description="资源事件唯一编码")
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
    payload_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON), description="事件事实")
    occurred_at: datetime = Field(description="事实发生时间")
    received_at: datetime = Field(description="WES 接收时间")


class ResourceStateEvent(ResourceStateEventBase, EnterpriseMixin, DataTableMixin, table=True):
    """资源 append-only 事实账本。"""

    __tablename__: ClassVar[Literal["resource_state_events"]] = "resource_state_events"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index("ux_resource_state_events_event_code", "event_code", unique=True),
        Index("ux_resource_state_events_source_event", "source_system", "source_event_id", unique=True),
        Index("ix_resource_state_events_resource_time", "resource_type", "resource_code", "occurred_at"),
    )


class RackPlacementBase(BaseMixin):
    """货架当前地码投影基础字段。"""

    rack_code: str = Field(min_length=1, max_length=80, index=True, description="货架编码")
    location_code: str = Field(min_length=1, max_length=80, index=True, description="地码编码")
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


class RackPlacement(RackPlacementBase, EnterpriseMixin, SoftDeleteMixin, DataTableMixin, table=True):
    """货架处于哪个执行地码的当前投影与历史。"""

    __tablename__: ClassVar[Literal["resource_rack_placements"]] = "resource_rack_placements"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index(
            "ux_resource_rack_placements_active_rack",
            "rack_code",
            unique=True,
            postgresql_where="ended_at IS NULL AND NOT is_deleted",
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
    source_system: ResourceRelationSourceSystem = Field(
        sa_type=cast(
            "Any", SQLAEnum(ResourceRelationSourceSystem, native_enum=False, create_constraint=True, length=50)
        ),
        description="来源系统",
    )
    source_event_id: str = Field(min_length=1, max_length=200, index=True, description="来源事件 ID")
    source_version: str | None = Field(default=None, max_length=100, description="来源版本")
    trace_id: str | None = Field(default=None, max_length=100, index=True, description="WorkLine trace")
    session_id: str | None = Field(default=None, max_length=100, index=True, description="WorkLine Session")
    started_at: datetime = Field(description="挂载确认时间")
    ended_at: datetime | None = Field(default=None, index=True, description="解除挂载时间")


class RackBinMount(RackBinMountBase, EnterpriseMixin, SoftDeleteMixin, DataTableMixin, table=True):
    """料箱挂载在哪个货架槽位的当前投影与历史。"""

    __tablename__: ClassVar[Literal["resource_rack_bin_mounts"]] = "resource_rack_bin_mounts"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index(
            "ux_resource_rack_bin_mounts_active_slot",
            "rack_code",
            "rack_slot_code",
            unique=True,
            postgresql_where="ended_at IS NULL AND NOT is_deleted",
        ),
        Index(
            "ux_resource_rack_bin_mounts_active_bin",
            "bin_code",
            unique=True,
            postgresql_where="ended_at IS NULL AND NOT is_deleted",
        ),
    )


class RackMaterialMountBase(BaseMixin):
    """物料卡槽投影基础字段。"""

    rack_code: str = Field(min_length=1, max_length=80, index=True, description="货架编码")
    rack_slot_code: str = Field(min_length=1, max_length=50, index=True, description="卡槽货位")
    material_identity_key: str = Field(min_length=1, max_length=300, index=True, description="WES 过程物料身份幂等键")
    pkg_code: str | None = Field(default=None, max_length=200, description="PKG 展示字段")
    material_code: str | None = Field(default=None, max_length=120, index=True, description="物料编码引用")
    lot_code: str | None = Field(default=None, max_length=120, description="批次展示字段")
    vendor_code: str | None = Field(default=None, max_length=120, description="供应商引用")
    qty_snapshot: float | None = Field(default=None, ge=0, description="当时执行过程看到的数量")
    wms_inventory_id: str | None = Field(default=None, max_length=120, index=True, description="WMS 库存记录引用")
    wms_inventory_version: str | None = Field(default=None, max_length=120, description="WMS 库存或分拆版本引用")
    wms_split_policy: WmsSplitPolicy = Field(
        default=WmsSplitPolicy.UNKNOWN,
        sa_type=cast("Any", SQLAEnum(WmsSplitPolicy, native_enum=False, create_constraint=True, length=50)),
        description="WMS 物料拆分策略",
    )
    wms_confirmation_status: WmsConfirmationStatus = Field(
        default=WmsConfirmationStatus.PENDING,
        sa_type=cast("Any", SQLAEnum(WmsConfirmationStatus, native_enum=False, create_constraint=True, length=50)),
        description="WMS 确认状态",
    )
    writeback_evidence_id: int | None = Field(default=None, description="关联 WMS 回写证据")
    mount_status: RackMaterialMountStatus = Field(
        default=RackMaterialMountStatus.UNKNOWN,
        sa_type=cast("Any", SQLAEnum(RackMaterialMountStatus, native_enum=False, create_constraint=True, length=50)),
        description="物料占用状态",
    )
    source_system: ResourceRelationSourceSystem = Field(
        sa_type=cast(
            "Any", SQLAEnum(ResourceRelationSourceSystem, native_enum=False, create_constraint=True, length=50)
        ),
        description="来源系统",
    )
    source_event_id: str = Field(min_length=1, max_length=200, index=True, description="来源事件 ID")
    source_version: str | None = Field(default=None, max_length=100, description="来源版本")
    trace_id: str | None = Field(default=None, max_length=100, index=True, description="WorkLine trace")
    session_id: str | None = Field(default=None, max_length=100, index=True, description="WorkLine Session")
    started_at: datetime = Field(description="占用确认时间")
    ended_at: datetime | None = Field(default=None, index=True, description="离开卡槽时间")


class RackMaterialMount(RackMaterialMountBase, EnterpriseMixin, SoftDeleteMixin, DataTableMixin, table=True):
    """物料/PKG/料盘直接占用哪个卡槽式货架槽位的当前投影与历史。"""

    __tablename__: ClassVar[Literal["resource_rack_material_mounts"]] = "resource_rack_material_mounts"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index(
            "ux_resource_rack_material_mounts_active_slot",
            "rack_code",
            "rack_slot_code",
            unique=True,
            postgresql_where="ended_at IS NULL AND NOT is_deleted",
        ),
        Index(
            "ix_resource_rack_material_mounts_identity_active",
            "material_identity_key",
            "ended_at",
        ),
    )


class WmsWritebackEvidenceBase(BaseMixin):
    """WMS 回写与确认 append-only 证据基础字段。"""

    evidence_code: str = Field(min_length=1, max_length=160, index=True, description="WMS 回写证据编码")
    request_id: str = Field(min_length=1, max_length=120, index=True, description="WES 请求 ID")
    idempotency_key: str = Field(min_length=1, max_length=200, index=True, description="WMS 回写幂等键")
    dispatch_key: str | None = Field(default=None, max_length=200, index=True, description="Outbox 派发键")
    endpoint: str = Field(min_length=1, max_length=300, description="WMS 接口或回调类型")
    source_system: ResourceSourceSystem = Field(
        sa_type=cast("Any", SQLAEnum(ResourceSourceSystem, native_enum=False, create_constraint=True, length=50)),
        description="WMS/RCS 来源系统",
    )
    request_hash: str = Field(min_length=1, max_length=128, description="脱敏请求摘要 hash")
    response_hash: str | None = Field(default=None, max_length=128, description="脱敏响应摘要 hash")
    request_summary_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
        description="脱敏请求摘要",
    )
    response_summary_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
        description="脱敏响应摘要",
    )
    http_status: int | None = Field(default=None, ge=100, le=599, description="HTTP 状态")
    wms_document_id: str | None = Field(default=None, max_length=160, index=True, description="WMS 单据或任务引用")
    inventory_version: str | None = Field(default=None, max_length=160, description="WMS 库存或业务版本")
    confirmed_at: datetime | None = Field(default=None, index=True, description="WMS 确认时间")
    retry_count: int = Field(default=0, ge=0, description="重试次数")
    failure_code: str | None = Field(default=None, max_length=120, index=True, description="失败原因")
    trace_id: str | None = Field(default=None, max_length=100, index=True, description="WorkLine trace")
    session_id: str | None = Field(default=None, max_length=100, index=True, description="WorkLine Session")


class WmsWritebackEvidence(WmsWritebackEvidenceBase, EnterpriseMixin, DataTableMixin, table=True):
    """WMS 回写与确认 append-only 证据。"""

    __tablename__: ClassVar[Literal["resource_wms_writeback_evidence"]] = "resource_wms_writeback_evidence"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index("ux_resource_wms_writeback_evidence_code", "evidence_code", unique=True),
        Index("ux_resource_wms_writeback_evidence_idempotency", "idempotency_key", unique=True),
        Index("ix_resource_wms_writeback_evidence_dispatch", "dispatch_key"),
        Index("ix_resource_wms_writeback_evidence_confirmed", "wms_document_id", "confirmed_at"),
    )


class RackReleaseBase(BaseMixin):
    """单层货架释放周期基础字段。"""

    rack_release_id: str = Field(min_length=1, max_length=160, index=True, description="释放周期业务 ID")
    single_layer_rack_code: str = Field(min_length=1, max_length=80, index=True, description="单层货架编码")
    source_classifier_line_code: str | None = Field(default=None, max_length=100, index=True, description="粗分线编码")
    source_task_batch_id: str | None = Field(default=None, max_length=160, index=True, description="粗分整架任务或批次")
    source_event_id: str | None = Field(default=None, max_length=200, index=True, description="来源事件 ID")
    release_status: RackReleaseStatus = Field(
        default=RackReleaseStatus.CANDIDATE,
        sa_type=cast("Any", SQLAEnum(RackReleaseStatus, native_enum=False, create_constraint=True, length=50)),
        description="释放周期状态",
    )
    released_at: datetime = Field(description="整架完成时间")
    moved_out_at: datetime | None = Field(default=None, index=True, description="离开粗分机时间")
    inbox_id: int | None = Field(default=None, index=True, description="关联 WorklineInbox")
    session_id: int | None = Field(default=None, index=True, description="关联 WorklineSession")
    release_cycle_seq: int = Field(default=1, ge=1, description="同一货架连续释放周期序号")
    idempotency_key: str = Field(min_length=1, max_length=300, index=True, description="释放周期幂等键")
    snapshot_hash: str = Field(min_length=1, max_length=128, description="4 箱快照摘要")
    trace_id: str | None = Field(default=None, max_length=100, index=True, description="WorkLine trace")


class RackRelease(RackReleaseBase, EnterpriseMixin, DataTableMixin, table=True):
    """单层货架一次释放周期。"""

    __tablename__: ClassVar[Literal["resource_rack_releases"]] = "resource_rack_releases"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index("ux_resource_rack_releases_release_id", "rack_release_id", unique=True),
        Index("ux_resource_rack_releases_idempotency", "idempotency_key", unique=True),
        Index(
            "ix_resource_rack_releases_rack_cycle",
            "single_layer_rack_code",
            "release_cycle_seq",
        ),
        Index("ix_resource_rack_releases_session", "session_id"),
    )


class RackReleaseBinSnapshotBase(BaseMixin):
    """释放瞬间料箱槽位快照基础字段。"""

    rack_release_id: str = Field(min_length=1, max_length=160, index=True, description="释放周期业务 ID")
    slot_code: str = Field(min_length=1, max_length=50, index=True, description="单层货架槽位")
    bin_code: str = Field(min_length=1, max_length=80, index=True, description="料箱编码")
    bin_type_code: str | None = Field(default=None, max_length=80, description="快照时料箱类型")
    bin_execution_status: BinStatus = Field(
        sa_type=cast("Any", SQLAEnum(BinStatus, native_enum=False, create_constraint=True, length=50)),
        description="快照时料箱执行状态",
    )
    usage_snapshot: float | None = Field(default=None, ge=0, le=1, description="过程计算使用率")
    material_summary_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
        description="物料摘要，不作为库存主账",
    )
    wms_inventory_refs_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
        description="WMS 库存记录引用与版本",
    )
    snapshot_id: str | None = Field(default=None, max_length=160, index=True, description="料箱内容快照 ID")
    content_snapshot_hash: str | None = Field(default=None, max_length=128, description="内容快照摘要")


class RackReleaseBinSnapshot(RackReleaseBinSnapshotBase, EnterpriseMixin, DataTableMixin, table=True):
    """释放瞬间每个槽位的料箱快照。"""

    __tablename__: ClassVar[Literal["resource_rack_release_bin_snapshots"]] = "resource_rack_release_bin_snapshots"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index(
            "ux_resource_rack_release_bin_snapshots_slot",
            "rack_release_id",
            "slot_code",
            unique=True,
        ),
        Index("ix_resource_rack_release_bin_snapshots_bin", "bin_code"),
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
    snapshot_hash: str = Field(min_length=1, max_length=128, description="快照头和明细稳定摘要")
    wms_snapshot_version: str | None = Field(default=None, max_length=160, description="WMS 查询版本或时间")


class BinContentSnapshot(BinContentSnapshotBase, EnterpriseMixin, DataTableMixin, table=True):
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
    bin_slot_code: str | None = Field(default=None, max_length=50, index=True, description="料箱内部槽位")
    pkg_code: str | None = Field(default=None, max_length=200, index=True, description="PKG 展示字段")
    material_code: str | None = Field(default=None, max_length=120, index=True, description="物料编码引用")
    vendor_code: str | None = Field(default=None, max_length=120, description="供应商引用")
    lot_code: str | None = Field(default=None, max_length=120, description="批次展示字段")
    date_code: str | None = Field(default=None, max_length=80, description="Date Code")
    qty_snapshot: float | None = Field(default=None, ge=0, description="当时执行过程看到的数量")
    thickness_mm: float | None = Field(default=None, ge=0, description="厚度")
    dims_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False), description="尺寸")
    wms_inventory_id: str | None = Field(default=None, max_length=160, index=True, description="WMS 库存记录引用")


class BinContentSnapshotItem(BinContentSnapshotItemBase, EnterpriseMixin, DataTableMixin, table=True):
    """料箱内部过程内容快照明细。"""

    __tablename__: ClassVar[Literal["resource_bin_content_snapshot_items"]] = "resource_bin_content_snapshot_items"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index("ix_resource_bin_content_snapshot_items_snapshot", "snapshot_id"),
        Index("ix_resource_bin_content_snapshot_items_pkg", "pkg_code"),
    )


class FullBoxExchangeTaskBase(BaseMixin):
    """满箱交换任务过程镜像基础字段。"""

    exchange_request_code: str = Field(min_length=1, max_length=200, index=True, description="满箱交换请求编码")
    rack_release_id: str = Field(min_length=1, max_length=160, index=True, description="来源释放周期")
    session_id: int | None = Field(default=None, index=True, description="关联 WorklineSession")
    outbox_id: int | None = Field(default=None, index=True, description="EXTERNAL_HTTP Outbox")
    dispatch_key: str | None = Field(default=None, max_length=200, index=True, description="Outbox 派发键")
    exchange_status: FullBoxExchangeStatus = Field(
        default=FullBoxExchangeStatus.REQUESTED,
        sa_type=cast("Any", SQLAEnum(FullBoxExchangeStatus, native_enum=False, create_constraint=True, length=50)),
        description="满箱交换状态",
    )
    exchange_area_code: str | None = Field(default=None, max_length=100, index=True, description="满箱交换区")
    requested_bins_json: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False),
        description="建议交换的料箱槽位",
    )
    wms_rcs_task_id: str | None = Field(default=None, max_length=160, index=True, description="WMS/RCS 任务 ID")
    wms_rcs_event_id: str | None = Field(default=None, max_length=200, index=True, description="最近一次 WMS/RCS 事件")
    queue_position: int | None = Field(default=None, ge=0, description="排队位置")
    eta_seconds: int | None = Field(default=None, ge=0, description="预计等待或完成时间")
    failure_code: str | None = Field(default=None, max_length=120, index=True, description="失败或拒绝原因")
    failure_message: str | None = Field(default=None, max_length=500, description="失败或拒绝描述")
    request_payload_hash: str | None = Field(default=None, max_length=128, description="请求摘要")
    last_callback_payload_hash: str | None = Field(default=None, max_length=128, description="最近回调摘要")
    writeback_evidence_id: int | None = Field(default=None, index=True, description="关联 WMS 回写证据")
    trace_id: str | None = Field(default=None, max_length=100, index=True, description="WorkLine trace")


class FullBoxExchangeTask(FullBoxExchangeTaskBase, EnterpriseMixin, DataTableMixin, table=True):
    """满箱交换任务过程镜像。"""

    __tablename__: ClassVar[Literal["resource_full_box_exchange_tasks"]] = "resource_full_box_exchange_tasks"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index("ux_resource_full_box_exchange_tasks_request", "exchange_request_code", unique=True),
        Index("ix_resource_full_box_exchange_tasks_release", "rack_release_id"),
        Index("ix_resource_full_box_exchange_tasks_outbox", "outbox_id"),
        Index("ix_resource_full_box_exchange_tasks_status", "exchange_status"),
    )


class ExecutionZoneCreate(ModelFactory(ExecutionZoneBase).for_create()):
    """执行区域创建 Schema。"""


class ExecutionZoneUpdate(ModelFactory(ExecutionZoneBase).for_optimistic_update()):
    """执行区域更新 Schema。"""


class ExecutionZoneResponse(ExecutionZoneBase):
    """执行区域响应 Schema。"""

    id: int
    version: int


class ExecutionLocationCreate(ModelFactory(ExecutionLocationBase).for_create()):
    """执行地码创建 Schema。"""


class ExecutionLocationUpdate(ModelFactory(ExecutionLocationBase).for_optimistic_update()):
    """执行地码更新 Schema。"""


class ExecutionLocationResponse(ExecutionLocationBase):
    """执行地码响应 Schema。"""

    id: int
    version: int


class RackTypeCreate(ModelFactory(RackTypeBase).for_create()):
    """货架类型创建 Schema。"""


class RackTypeUpdate(ModelFactory(RackTypeBase).for_optimistic_update()):
    """货架类型更新 Schema。"""


class RackTypeResponse(RackTypeBase):
    """货架类型响应 Schema。"""

    id: int
    version: int


class RackSlotTemplateCreate(ModelFactory(RackSlotTemplateBase).for_create()):
    """货架槽位模板创建 Schema。"""


class RackSlotTemplateUpdate(ModelFactory(RackSlotTemplateBase).for_optimistic_update()):
    """货架槽位模板更新 Schema。"""


class RackSlotTemplateResponse(RackSlotTemplateBase):
    """货架槽位模板响应 Schema。"""

    id: int
    version: int


class RackCreate(ModelFactory(RackBase).for_create()):
    """货架实例创建 Schema。"""


class RackUpdate(ModelFactory(RackBase).for_optimistic_update()):
    """货架实例更新 Schema。"""


class RackResponse(RackBase):
    """货架实例响应 Schema。"""

    id: int
    version: int


class BinTypeCreate(ModelFactory(BinTypeBase).for_create()):
    """料箱类型创建 Schema。"""


class BinTypeUpdate(ModelFactory(BinTypeBase).for_optimistic_update()):
    """料箱类型更新 Schema。"""


class BinTypeResponse(BinTypeBase):
    """料箱类型响应 Schema。"""

    id: int
    version: int


class BinSlotTemplateCreate(ModelFactory(BinSlotTemplateBase).for_create()):
    """料箱槽位模板创建 Schema。"""


class BinSlotTemplateUpdate(ModelFactory(BinSlotTemplateBase).for_optimistic_update()):
    """料箱槽位模板更新 Schema。"""


class BinSlotTemplateResponse(BinSlotTemplateBase):
    """料箱槽位模板响应 Schema。"""

    id: int
    version: int


class BinCreate(ModelFactory(BinBase).for_create()):
    """料箱实例创建 Schema。"""


class BinUpdate(ModelFactory(BinBase).for_optimistic_update()):
    """料箱实例更新 Schema。"""


class BinResponse(BinBase):
    """料箱实例响应 Schema。"""

    id: int
    version: int


class ResourceStateEventCreate(ModelFactory(ResourceStateEventBase).for_create()):
    """资源事实创建 Schema。"""


class ResourceStateEventUpdate(ModelFactory(ResourceStateEventBase).for_optimistic_update()):
    """资源事实更新 Schema。"""


class ResourceStateEventResponse(ResourceStateEventBase):
    """资源事实响应 Schema。"""

    id: int
    version: int


class RackPlacementCreate(ModelFactory(RackPlacementBase).for_create()):
    """货架位置投影创建 Schema。"""


class RackPlacementUpdate(ModelFactory(RackPlacementBase).for_optimistic_update()):
    """货架位置投影更新 Schema。"""


class RackPlacementResponse(RackPlacementBase):
    """货架位置投影响应 Schema。"""

    id: int
    version: int


class RackBinMountCreate(ModelFactory(RackBinMountBase).for_create()):
    """料箱挂载投影创建 Schema。"""


class RackBinMountUpdate(ModelFactory(RackBinMountBase).for_optimistic_update()):
    """料箱挂载投影更新 Schema。"""


class RackBinMountResponse(RackBinMountBase):
    """料箱挂载投影响应 Schema。"""

    id: int
    version: int


class RackMaterialMountCreate(ModelFactory(RackMaterialMountBase).for_create()):
    """物料卡槽投影创建 Schema。"""


class RackMaterialMountUpdate(ModelFactory(RackMaterialMountBase).for_optimistic_update()):
    """物料卡槽投影更新 Schema。"""


class RackMaterialMountResponse(RackMaterialMountBase):
    """物料卡槽投影响应 Schema。"""

    id: int
    version: int


class WmsWritebackEvidenceCreate(ModelFactory(WmsWritebackEvidenceBase).for_create()):
    """WMS 回写证据创建 Schema。"""


class WmsWritebackEvidenceUpdate(ModelFactory(WmsWritebackEvidenceBase).for_optimistic_update()):
    """WMS 回写证据更新 Schema。"""


class WmsWritebackEvidenceResponse(WmsWritebackEvidenceBase):
    """WMS 回写证据响应 Schema。"""

    id: int
    version: int


class RackReleaseCreate(ModelFactory(RackReleaseBase).for_create()):
    """释放周期创建 Schema。"""


class RackReleaseUpdate(ModelFactory(RackReleaseBase).for_optimistic_update()):
    """释放周期更新 Schema。"""


class RackReleaseResponse(RackReleaseBase):
    """释放周期响应 Schema。"""

    id: int
    version: int


class RackReleaseBinSnapshotCreate(ModelFactory(RackReleaseBinSnapshotBase).for_create()):
    """释放槽位快照创建 Schema。"""


class RackReleaseBinSnapshotUpdate(ModelFactory(RackReleaseBinSnapshotBase).for_optimistic_update()):
    """释放槽位快照更新 Schema。"""


class RackReleaseBinSnapshotResponse(RackReleaseBinSnapshotBase):
    """释放槽位快照响应 Schema。"""

    id: int
    version: int


class BinContentSnapshotCreate(ModelFactory(BinContentSnapshotBase).for_create()):
    """料箱内容快照头创建 Schema。"""


class BinContentSnapshotUpdate(ModelFactory(BinContentSnapshotBase).for_optimistic_update()):
    """料箱内容快照头更新 Schema。"""


class BinContentSnapshotResponse(BinContentSnapshotBase):
    """料箱内容快照头响应 Schema。"""

    id: int
    version: int


class BinContentSnapshotItemCreate(ModelFactory(BinContentSnapshotItemBase).for_create()):
    """料箱内容快照明细创建 Schema。"""


class BinContentSnapshotItemUpdate(ModelFactory(BinContentSnapshotItemBase).for_optimistic_update()):
    """料箱内容快照明细更新 Schema。"""


class BinContentSnapshotItemResponse(BinContentSnapshotItemBase):
    """料箱内容快照明细响应 Schema。"""

    id: int
    version: int


class FullBoxExchangeTaskCreate(ModelFactory(FullBoxExchangeTaskBase).for_create()):
    """满箱交换任务创建 Schema。"""


class FullBoxExchangeTaskUpdate(ModelFactory(FullBoxExchangeTaskBase).for_optimistic_update()):
    """满箱交换任务更新 Schema。"""


class FullBoxExchangeTaskResponse(FullBoxExchangeTaskBase):
    """满箱交换任务响应 Schema。"""

    id: int
    version: int


__all__ = [
    "Bin",
    "BinBase",
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
    "BinResponse",
    "BinSlotSize",
    "BinSlotTemplate",
    "BinSlotTemplateBase",
    "BinSlotTemplateCreate",
    "BinSlotTemplateResponse",
    "BinSlotTemplateUpdate",
    "BinStatus",
    "BinType",
    "BinTypeBase",
    "BinTypeCreate",
    "BinTypeResponse",
    "BinTypeUpdate",
    "BinUpdate",
    "ExecutionLocation",
    "ExecutionLocationBase",
    "ExecutionLocationCreate",
    "ExecutionLocationResponse",
    "ExecutionLocationStatus",
    "ExecutionLocationType",
    "ExecutionLocationUpdate",
    "ExecutionZone",
    "ExecutionZoneBase",
    "ExecutionZoneCreate",
    "ExecutionZoneResponse",
    "ExecutionZoneType",
    "ExecutionZoneUpdate",
    "FullBoxExchangeStatus",
    "FullBoxExchangeTask",
    "FullBoxExchangeTaskBase",
    "FullBoxExchangeTaskCreate",
    "FullBoxExchangeTaskResponse",
    "FullBoxExchangeTaskUpdate",
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
    "RackMaterialMount",
    "RackMaterialMountBase",
    "RackMaterialMountCreate",
    "RackMaterialMountResponse",
    "RackMaterialMountStatus",
    "RackMaterialMountUpdate",
    "RackPlacement",
    "RackPlacementBase",
    "RackPlacementCreate",
    "RackPlacementResponse",
    "RackPlacementStatus",
    "RackPlacementUpdate",
    "RackRelease",
    "RackReleaseBase",
    "RackReleaseBinSnapshot",
    "RackReleaseBinSnapshotBase",
    "RackReleaseBinSnapshotCreate",
    "RackReleaseBinSnapshotResponse",
    "RackReleaseBinSnapshotUpdate",
    "RackReleaseCreate",
    "RackReleaseResponse",
    "RackReleaseStatus",
    "RackReleaseUpdate",
    "RackResponse",
    "RackSlotKind",
    "RackSlotSide",
    "RackSlotTemplate",
    "RackSlotTemplateBase",
    "RackSlotTemplateCreate",
    "RackSlotTemplateResponse",
    "RackSlotTemplateUpdate",
    "RackStatus",
    "RackType",
    "RackTypeBase",
    "RackTypeCreate",
    "RackTypeResponse",
    "RackTypeUpdate",
    "RackUpdate",
    "ResourceMasterStatus",
    "ResourceRef",
    "ResourceRelationSourceSystem",
    "ResourceSourceSystem",
    "ResourceStateEvent",
    "ResourceStateEventBase",
    "ResourceStateEventCreate",
    "ResourceStateEventResponse",
    "ResourceStateEventType",
    "ResourceStateEventUpdate",
    "ResourceType",
    "WmsConfirmationStatus",
    "WmsSplitPolicy",
    "WmsWritebackEvidence",
    "WmsWritebackEvidenceBase",
    "WmsWritebackEvidenceCreate",
    "WmsWritebackEvidenceResponse",
    "WmsWritebackEvidenceUpdate",
]
