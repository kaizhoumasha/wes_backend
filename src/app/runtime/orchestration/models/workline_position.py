"""工作线位置存储；position_type 区分货架位与普通工作位。"""

from __future__ import annotations

from typing import Any, ClassVar, Literal, cast

from sqlalchemy import JSON, CheckConstraint, Column, Index, String
from sqlalchemy import Enum as SQLAEnum
from sqlmodel import Field

from src.app.resource.models import RackKind
from src.app.workline.rack_position_role import WorklineRackPositionRole
from src.core.mixins import BaseMixin, DataTableMixin
from src.core.mixins.primary_key import SQL_COMPAT_BIGINT
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType


class WorkLinePositionBase(BaseMixin):
    """工作线工作位基础字段。"""

    workline_id: int = Field(
        index=True,
        foreign_key="wes_biz.work_lines.id",
        sa_type=SQL_COMPAT_BIGINT,
        description="关联 WorkLine.id",
    )
    workline_code: str = Field(min_length=1, max_length=50, index=True, description="工作线编码")
    position_code: str = Field(min_length=1, max_length=80, index=True, description="工作位编码")
    position_name: str = Field(min_length=1, max_length=120, description="工作位名称")
    position_type: Literal["RACK_POSITION", "STATION"] = Field(
        default="RACK_POSITION", sa_column=Column(String(30), nullable=False, server_default="RACK_POSITION")
    )
    position_role: WorklineRackPositionRole | None = Field(
        default=None,
        sa_type=cast(
            "Any",
            SQLAEnum(WorklineRackPositionRole, native_enum=False, create_constraint=True, length=50),
        ),
        description="货架工作位用途",
    )
    allowed_rack_kind: RackKind | None = Field(
        default=None,
        sa_type=cast("Any", SQLAEnum(RackKind, native_enum=False, create_constraint=True, length=50)),
        description="允许货架类型",
    )
    capacity: int = Field(default=1, ge=1, description="位置容量；货架位按可容纳货架数量计")
    logic_location_code: str | None = Field(default=None, max_length=120, index=True, description="WES 逻辑位置")
    external_location_code: str | None = Field(default=None, max_length=120, index=True, description="外部地码证据")
    device_id: int | None = Field(
        default=None,
        foreign_key="wes_biz.devices.id",
        sa_type=SQL_COMPAT_BIGINT,
        index=True,
        description="关联物理设备，独立于业务插件角色",
    )
    device_role: str | None = Field(default=None, max_length=100, index=True, description="关联设备角色")
    priority: int = Field(default=100, ge=0, description="候选优先级")
    enabled: bool = Field(default=True, index=True, description="是否启用")
    metadata_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
        description="扩展属性",
    )


class WorkLinePosition(WorkLinePositionBase, DataTableMixin, table=True):
    """工作线静态位置配置，包含货架位和普通工作位。"""

    __tablename__: ClassVar[Literal["workline_positions"]] = "workline_positions"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index("ux_workline_positions_line_position", "workline_code", "position_code", unique=True),
        CheckConstraint("capacity > 0", name="ck_workline_positions_capacity_positive"),
        CheckConstraint(
            "(position_type = 'RACK_POSITION' AND position_role IS NOT NULL AND allowed_rack_kind IS NOT NULL) OR "
            "(position_type = 'STATION' AND position_role IS NULL AND allowed_rack_kind IS NULL)",
            name="ck_workline_positions_resource_type",
        ),
    )


class WorkLinePositionCreate(ModelFactory(WorkLinePositionBase).for_create()):
    """工作线工作位创建 Schema。"""


class WorkLinePositionUpdate(ModelFactory(WorkLinePositionBase).for_update()):
    """工作线工作位更新 Schema。"""


class WorkLinePositionResponse(WorkLinePositionBase):
    """工作线工作位响应 Schema。"""

    id: int


__all__ = [
    "WorkLinePosition",
    "WorkLinePositionBase",
    "WorkLinePositionCreate",
    "WorkLinePositionResponse",
    "WorkLinePositionUpdate",
    "WorklineRackPositionRole",
]
