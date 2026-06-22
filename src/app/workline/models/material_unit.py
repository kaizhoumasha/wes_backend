"""料盘根实体模型。

material_units 是料盘（REEL）的根域实体表，扫码时建立，
状态/位置变化时更新。NG 料盘保留在根实体中支持当前追溯，
长期处置/回流记录由 ng_return_items 承载。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, ClassVar, cast

from sqlalchemy import JSON, CheckConstraint, Column, Index
from sqlalchemy import Enum as SQLAEnum
from sqlmodel import Field

from src.core.mixins import BaseMixin, DataTableMixin
from src.core.mixins.primary_key import SQL_COMPAT_BIGINT
from src.database.schema_conf import SchemaType


class MaterialUnitStatus(str, Enum):
    """料盘状态机（物视角）。

    NG 是业务问题（料盘不合格，进 NG 域，单向不回正常流）；
    RECONCILING 是功能问题（系统状态不可信，对账后可回正常态）。
    两者不重叠。
    """

    IN_TRANSIT = "IN_TRANSIT"
    STORED = "STORED"
    COMPLETED = "COMPLETED"
    NG = "NG"
    RECONCILING = "RECONCILING"


class MaterialUnitBase(BaseMixin):
    """料盘根实体基础字段。"""

    pkg_code: str = Field(
        max_length=200,
        description="PkgID，单盘物理唯一业务键",
    )
    material_identity_key: str = Field(
        max_length=300,
        description="物料属性键（MAT:code:vendor:date:lot，同批次共享）",
    )
    six_in_one: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON),
        description="六合一码全字段",
    )
    status: MaterialUnitStatus = Field(
        default=MaterialUnitStatus.IN_TRANSIT,
        sa_type=cast("Any", SQLAEnum(MaterialUnitStatus, native_enum=False, create_constraint=False, length=50)),
        description="料盘状态（IN_TRANSIT/STORED/COMPLETED/NG/RECONCILING）",
    )
    current_location: str | None = Field(
        default=None,
        max_length=200,
        description="当前格位/工位（如 bin_code:cell_index 或工位码）",
    )
    current_session_id: int | None = Field(
        default=None,
        sa_type=SQL_COMPAT_BIGINT,
        description="当前处理 Session ID（引用 workline_sessions.id，无外键遵循辅助追溯字段规范）",
    )
    reconciliation_from_state: MaterialUnitStatus | None = Field(
        default=None,
        sa_type=cast("Any", SQLAEnum(MaterialUnitStatus, native_enum=False, create_constraint=False, length=50)),
        description="进入 RECONCILING 前的 status，对账后据此校验恢复集",
    )


class MaterialUnit(MaterialUnitBase, DataTableMixin, table=True):
    """料盘根实体表。

    自主主键（BaseMixin），pkg_code 为业务键。
    记录当前料盘状态（IN_TRANSIT/STORED/COMPLETED/NG/RECONCILING）；
    NG 处置和回流的长期记录归 ng_return_items。
    """

    __tablename__: ClassVar[str] = "material_units"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        CheckConstraint(
            "status IN ('IN_TRANSIT', 'STORED', 'COMPLETED', 'NG', 'RECONCILING')",
            name="status",
        ),
        Index("ix_material_units_pkg_code", "pkg_code", unique=True),
        Index("ix_material_units_status", "status"),
        Index("ix_material_units_current_session_id", "current_session_id"),
        {"schema": SchemaType.BIZ.value},
    )
