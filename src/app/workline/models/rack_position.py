"""工作线货架停靠位配置模型。"""

from __future__ import annotations

from enum import Enum
from typing import Any, ClassVar, Literal, cast

from sqlalchemy import JSON, CheckConstraint, Column, Index
from sqlalchemy import Enum as SQLAEnum
from sqlmodel import Field

from src.app.resource.models import RackKind
from src.core.mixins import BaseMixin, DataTableMixin
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType


class WorklineRackPositionRole(str, Enum):
    """工作线停靠位角色。"""

    SOURCE_STORAGE = "SOURCE_STORAGE"
    OUTPUT_BUFFER = "OUTPUT_BUFFER"


class WorklineRackPositionBase(BaseMixin):
    """工作线货架停靠位基础字段。"""

    workline_id: int = Field(index=True, foreign_key="wes_biz.work_lines.id", description="关联 WorkLine.id")
    workline_code: str = Field(min_length=1, max_length=50, index=True, description="工作线编码")
    position_code: str = Field(min_length=1, max_length=80, index=True, description="停靠位编码")
    position_name: str = Field(min_length=1, max_length=120, description="停靠位名称")
    position_role: WorklineRackPositionRole = Field(
        sa_type=cast(
            "Any",
            SQLAEnum(WorklineRackPositionRole, native_enum=False, create_constraint=True, length=50),
        ),
        description="停靠位角色",
    )
    allowed_rack_kind: RackKind = Field(
        sa_type=cast("Any", SQLAEnum(RackKind, native_enum=False, create_constraint=True, length=50)),
        description="允许货架类型",
    )
    capacity: int = Field(default=1, ge=1, description="容量；Phase A 固定为 1")
    logic_location_code: str | None = Field(default=None, max_length=120, index=True, description="WES 逻辑位置")
    external_location_code: str | None = Field(default=None, max_length=120, index=True, description="外部地码证据")
    device_role: str | None = Field(default=None, max_length=100, index=True, description="关联设备角色")
    priority: int = Field(default=100, ge=0, description="候选优先级")
    enabled: bool = Field(default=True, index=True, description="是否启用")
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON), description="扩展属性")


class WorklineRackPosition(WorklineRackPositionBase, DataTableMixin, table=True):
    """工作线可停靠货架位置配置。"""

    __tablename__: ClassVar[Literal["workline_rack_positions"]] = "workline_rack_positions"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index("ux_workline_rack_positions_line_position", "workline_code", "position_code", unique=True),
        CheckConstraint("capacity = 1", name="ck_workline_rack_positions_capacity_one"),
    )


class WorklineRackPositionCreate(ModelFactory(WorklineRackPositionBase).for_create()):
    """工作线停靠位创建 Schema。"""


class WorklineRackPositionUpdate(ModelFactory(WorklineRackPositionBase).for_update()):
    """工作线停靠位更新 Schema。"""


class WorklineRackPositionResponse(WorklineRackPositionBase):
    """工作线停靠位响应 Schema。"""

    id: int


__all__ = [
    "WorklineRackPosition",
    "WorklineRackPositionBase",
    "WorklineRackPositionCreate",
    "WorklineRackPositionResponse",
    "WorklineRackPositionRole",
    "WorklineRackPositionUpdate",
]
