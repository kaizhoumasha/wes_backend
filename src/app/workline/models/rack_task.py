"""工作线货架级任务模型。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from enum import Enum
from typing import Any, ClassVar, Literal, cast

from sqlalchemy import JSON, Column, Index, Text, text
from sqlalchemy import Enum as SQLAEnum
from sqlmodel import Field

from src.core.mixins import BaseMixin, DataTableMixin
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType


class WorklineRackTaskType(str, Enum):
    """工作线货架级任务类型。"""

    MOVE_RACK = "MOVE_RACK"
    ALLOCATE_AND_MOVE_RACK = "ALLOCATE_AND_MOVE_RACK"
    TURN_RACK_SIDE = "TURN_RACK_SIDE"


class WorklineRackTaskStatus(str, Enum):
    """工作线货架级任务状态。"""

    PLANNED = "PLANNED"
    REQUESTED = "REQUESTED"
    IN_PROGRESS = "IN_PROGRESS"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    RECONCILING = "RECONCILING"
    CANCELLED = "CANCELLED"


class WorklineRackTaskBase(BaseMixin):
    """货架级任务基础字段。"""

    task_key: str = Field(min_length=1, max_length=240, index=True, description="任务幂等键")
    operation_key: str = Field(min_length=1, max_length=240, index=True, description="货架操作幂等键")
    operation_type: str = Field(min_length=1, max_length=100, index=True, description="货架操作类型")
    sequence_no: int = Field(index=True, description="同一货架操作下的任务序号")
    task_type: WorklineRackTaskType = Field(
        index=True,
        sa_type=cast("Any", SQLAEnum(WorklineRackTaskType, native_enum=False, create_constraint=True, length=50)),
        description="任务类型",
    )
    task_status: WorklineRackTaskStatus = Field(
        default=WorklineRackTaskStatus.PLANNED,
        index=True,
        sa_type=cast("Any", SQLAEnum(WorklineRackTaskStatus, native_enum=False, create_constraint=True, length=50)),
        description="任务状态",
    )
    workline_id: int = Field(index=True, foreign_key="wes_biz.work_lines.id", description="关联 WorkLine.id")
    workline_code: str | None = Field(default=None, max_length=50, index=True, description="工作线编码")
    material_session_id: int | None = Field(
        default=None,
        index=True,
        foreign_key="wes_biz.workline_sessions.id",
        description="关联的物料/料盘 Session.id",
    )
    rack_kind: str | None = Field(default=None, max_length=50, index=True, description="货架类型")
    rack_code: str | None = Field(default=None, max_length=100, index=True, description="货架编码")
    source_position_code: str | None = Field(default=None, max_length=100, index=True, description="来源位置编码")
    target_position_code: str | None = Field(default=None, max_length=100, index=True, description="目标位置编码")
    target_position_role: str | None = Field(default=None, max_length=50, index=True, description="目标位置角色")
    dispatch_key: str | None = Field(default=None, max_length=240, index=True, description="外部派发幂等键")
    outbox_id: int | None = Field(
        default=None,
        index=True,
        foreign_key="wes_biz.workline_outbox.id",
        description="关联 WorklineOutbox.id",
    )
    target_code: str | None = Field(default=None, max_length=200, description="外部目标编码或地址")
    source_system: str | None = Field(default=None, max_length=100, description="外部系统")
    trace_id: str | None = Field(default=None, max_length=100, index=True, description="Trace ID")
    source_event_id: str | None = Field(default=None, max_length=200, index=True, description="来源事件 ID")
    source_version: str | None = Field(default=None, max_length=100, description="来源版本")
    request_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON), description="请求证据")
    actions_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON), description="调度动作 payload")
    callback_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON), description="回调证据")
    result_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON), description="结果证据")
    error_code: str | None = Field(default=None, max_length=100, index=True, description="错误码")
    error_message: str | None = Field(default=None, sa_column=Column(Text), description="错误消息")
    requested_at: datetime | None = Field(default=None, index=True, description="请求时间")
    started_at: datetime | None = Field(default=None, index=True, description="开始时间")
    completed_at: datetime | None = Field(default=None, index=True, description="完成时间")


class WorklineRackTask(WorklineRackTaskBase, DataTableMixin, table=True):
    """工作线货架级任务。"""

    __tablename__: ClassVar[Literal["workline_rack_tasks"]] = "workline_rack_tasks"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index("ux_workline_rack_tasks_key", "task_key", unique=True),
        Index("ux_workline_rack_tasks_dispatch_key", "dispatch_key", unique=True),
        Index("ux_workline_rack_tasks_operation_sequence", "operation_key", "sequence_no", unique=True),
        Index(
            "ux_workline_rack_tasks_move_source_claim",
            "workline_code",
            "source_position_code",
            "rack_code",
            unique=True,
            postgresql_where=text(
                "task_type = 'MOVE_RACK' "
                "AND task_status IN ('PLANNED', 'REQUESTED', 'IN_PROGRESS', 'RECONCILING') "
                "AND workline_code IS NOT NULL "
                "AND source_position_code IS NOT NULL "
                "AND rack_code IS NOT NULL"
            ),
            sqlite_where=text(
                "task_type = 'MOVE_RACK' "
                "AND task_status IN ('PLANNED', 'REQUESTED', 'IN_PROGRESS', 'RECONCILING') "
                "AND workline_code IS NOT NULL "
                "AND source_position_code IS NOT NULL "
                "AND rack_code IS NOT NULL"
            ),
        ),
        Index("ix_workline_rack_tasks_operation_status", "operation_key", "task_status"),
        Index("ix_workline_rack_tasks_session_operation", "material_session_id", "operation_key"),
        Index("ix_workline_rack_tasks_target_status", "workline_code", "target_position_code", "task_status"),
        {"schema": SchemaType.BIZ.value},
    )


class WorklineRackTaskCreate(ModelFactory(WorklineRackTaskBase).for_create()):
    """货架级任务创建 Schema。"""


class WorklineRackTaskUpdate(ModelFactory(WorklineRackTaskBase).for_update()):
    """货架级任务更新 Schema。"""


class WorklineRackTaskResponse(WorklineRackTaskBase):
    """货架级任务响应 Schema。"""

    id: int


__all__ = [
    "WorklineRackTask",
    "WorklineRackTaskBase",
    "WorklineRackTaskCreate",
    "WorklineRackTaskResponse",
    "WorklineRackTaskStatus",
    "WorklineRackTaskType",
    "WorklineRackTaskUpdate",
]
