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


__all__ = [
    "Bin",
    "BinBase",
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
    "Rack",
    "RackBase",
    "RackCreate",
    "RackKind",
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
    "ResourceSourceSystem",
    "ResourceType",
]
