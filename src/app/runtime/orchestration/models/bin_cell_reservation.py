"""工作线料箱格位预占模型。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from enum import Enum
from typing import Any, ClassVar, Literal, cast

from sqlalchemy import JSON, Column, Index, text
from sqlalchemy import Enum as SQLAEnum
from sqlmodel import Field

from src.core.mixins import BaseMixin, DataTableMixin
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType


class BinCellReservationStatus(str, Enum):
    """料箱格位预占状态。"""

    PLANNED = "PLANNED"
    CONSUMED = "CONSUMED"
    RELEASED = "RELEASED"
    CANCELLED = "CANCELLED"
    RECONCILING = "RECONCILING"


class WorklineBinCellReservationBase(BaseMixin):
    """料箱格位预占基础字段。"""

    reservation_key: str = Field(min_length=1, max_length=240, index=True, description="预占幂等键")
    workline_id: int = Field(index=True, foreign_key="wes_biz.work_lines.id", description="关联 WorkLine.id")
    workline_code: str = Field(min_length=1, max_length=50, index=True, description="工作线编码")
    session_id: int = Field(index=True, foreign_key="wes_biz.workline_sessions.id", description="关联 Session.id")
    correlation_id: str | None = Field(default=None, max_length=120, index=True, description="跨域 correlation ID")
    trace_id: str | None = Field(default=None, max_length=100, index=True, description="trace ID")
    pkg_code: str = Field(min_length=1, max_length=200, index=True, description="PKG 编码")
    bin_code: str = Field(min_length=1, max_length=80, index=True, description="料箱编码")
    bin_cell_code: str | None = Field(default=None, max_length=80, index=True, description="料箱格位编码")
    bin_cell_index: str = Field(min_length=1, max_length=20, index=True, description="料箱格位序号")
    reservation_status: BinCellReservationStatus = Field(
        default=BinCellReservationStatus.PLANNED,
        sa_type=cast("Any", SQLAEnum(BinCellReservationStatus, native_enum=False, create_constraint=True, length=50)),
        description="预占状态",
    )
    source_event_id: str | None = Field(default=None, max_length=200, index=True, description="来源命令或事件")
    reserved_at: datetime = Field(description="预占时间")
    consumed_at: datetime | None = Field(default=None, index=True, description="消耗时间")
    released_at: datetime | None = Field(default=None, index=True, description="释放时间")
    expires_at: datetime | None = Field(default=None, index=True, description="预占过期时间")
    evidence_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON), description="预占证据")
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON), description="扩展属性")


class WorklineBinCellReservation(WorklineBinCellReservationBase, DataTableMixin, table=True):
    """工作线料箱格位计划预占。"""

    __tablename__: ClassVar[Literal["workline_bin_cell_reservations"]] = "workline_bin_cell_reservations"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index("ux_workline_bin_cell_reservations_key", "reservation_key", unique=True),
        Index(
            "ux_workline_bin_cell_reservations_active_cell",
            "bin_code",
            "bin_cell_index",
            unique=True,
            postgresql_where=text("reservation_status IN ('PLANNED', 'RECONCILING')"),
            sqlite_where=text("reservation_status IN ('PLANNED', 'RECONCILING')"),
        ),
        Index("ix_workline_bin_cell_reservations_session", "session_id", "reservation_status"),
    )


class WorklineBinCellReservationCreate(ModelFactory(WorklineBinCellReservationBase).for_create()):
    """料箱格位预占创建 Schema。"""


class WorklineBinCellReservationUpdate(ModelFactory(WorklineBinCellReservationBase).for_update()):
    """料箱格位预占更新 Schema。"""


class WorklineBinCellReservationResponse(WorklineBinCellReservationBase):
    """料箱格位预占响应 Schema。"""

    id: int


__all__ = [
    "BinCellReservationStatus",
    "WorklineBinCellReservation",
    "WorklineBinCellReservationBase",
    "WorklineBinCellReservationCreate",
    "WorklineBinCellReservationResponse",
    "WorklineBinCellReservationUpdate",
]
