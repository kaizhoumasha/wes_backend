"""RACK/BIN 共用的唯一 current position projection。"""

from __future__ import annotations

from typing import Any, ClassVar

from sqlalchemy import JSON, CheckConstraint, Index, UniqueConstraint
from sqlmodel import Field

from src.core.mixins import DataTableMixin, EnterpriseMixin
from src.core.mixins.primary_key import SQL_COMPAT_BIGINT
from src.database.schema_conf import SchemaType


class PositionProjection(EnterpriseMixin, DataTableMixin, table=True):
    """只保存由活动 execution authority 证明的当前规范化位置。"""

    __tablename__: ClassVar[str] = "position_projections"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        CheckConstraint("object_type IN ('RACK', 'BIN')", name="position_projection_object_type_valid"),
        CheckConstraint(
            "(object_type = 'RACK' AND bin_execution_id IS NULL) OR "
            "(object_type = 'BIN' AND bin_execution_id IS NOT NULL)",
            name="position_projection_bin_authority_valid",
        ),
        UniqueConstraint("object_type", "object_id", name="ux_position_projection_object"),
        Index("ix_position_projection_epoch", "line_run_epoch_id", "id"),
        Index("ix_position_projection_source_task", "source_transport_task_id"),
        {"schema": SchemaType.BIZ.value},
    )

    object_type: str = Field(max_length=10)
    object_id: str = Field(max_length=100)
    workline_id: int = Field(foreign_key="wes_biz.work_lines.id", index=True)
    line_run_epoch_id: int = Field(foreign_key="wes_biz.line_run_epochs.id", index=True)
    bin_execution_id: int | None = Field(
        default=None,
        foreign_key="wes_biz.bin_executions.id",
        sa_type=SQL_COMPAT_BIGINT,
        index=True,
    )
    position_json: dict[str, Any] | None = Field(default=None, sa_type=JSON)
    position_unknown: bool = Field(default=False)
    arrival_face: str | None = Field(default=None, max_length=1)
    source_operation_id: str = Field(max_length=36)
    source_transport_task_id: str = Field(max_length=80)


__all__ = ["PositionProjection"]
