"""货架级任务模型。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from enum import Enum
from typing import Any, ClassVar, Literal, cast

from sqlalchemy import JSON, Column, Index, Text, text
from sqlalchemy import Enum as SQLAEnum
from sqlmodel import Field

from src.app.sys.models import OperationCompletionPolicy
from src.core.mixins import BaseMixin, DataTableMixin
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType


class RackTaskType(str, Enum):
    """货架级任务类型。"""

    MOVE_RACK = "MOVE_RACK"
    ALLOCATE_AND_MOVE_RACK = "ALLOCATE_AND_MOVE_RACK"
    TURN_RACK_SIDE = "TURN_RACK_SIDE"


class RackTaskStatus(str, Enum):
    """货架级任务状态。"""

    PLANNED = "PLANNED"
    REQUESTED = "REQUESTED"
    IN_PROGRESS = "IN_PROGRESS"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    RECONCILING = "RECONCILING"
    CANCELLED = "CANCELLED"


class RackOperationStatus(str, Enum):
    """货架业务操作状态。"""

    REQUESTED = "REQUESTED"
    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    RECONCILING = "RECONCILING"
    CANCELLED = "CANCELLED"


class RackOperationBase(BaseMixin):
    """货架业务操作基础字段。"""

    operation_key: str = Field(min_length=1, max_length=240, index=True, description="货架操作幂等键")
    operation_type: str = Field(min_length=1, max_length=100, index=True, description="货架操作类型")
    operation_status: RackOperationStatus = Field(
        default=RackOperationStatus.REQUESTED,
        index=True,
        sa_type=cast("Any", SQLAEnum(RackOperationStatus, native_enum=False, create_constraint=True, length=50)),
        description="货架操作状态",
    )
    completion_policy: OperationCompletionPolicy = Field(
        default=OperationCompletionPolicy.RESOURCE_PROJECTION_REQUIRED,
        index=True,
        sa_type=cast("Any", SQLAEnum(OperationCompletionPolicy, native_enum=False, create_constraint=True, length=50)),
        description="完成确认策略",
    )
    workline_id: int | None = Field(
        default=None,
        index=True,
        foreign_key="wes_biz.work_lines.id",
        description="可选关联 WorkLine.id",
    )
    workline_code: str | None = Field(default=None, max_length=50, index=True, description="可选工作线编码")
    material_session_id: int | None = Field(
        default=None,
        index=True,
        foreign_key="wes_biz.workline_sessions.id",
        description="可选关联物料/料盘 Session.id",
    )
    trace_id: str | None = Field(default=None, max_length=100, index=True, description="Trace ID")
    request_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON), description="请求证据")
    result_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON), description="结果证据")
    error_code: str | None = Field(default=None, max_length=100, index=True, description="错误码")
    error_message: str | None = Field(default=None, sa_column=Column(Text), description="错误消息")
    requested_at: datetime | None = Field(default=None, index=True, description="请求时间")
    started_at: datetime | None = Field(default=None, index=True, description="开始时间")
    completed_at: datetime | None = Field(default=None, index=True, description="完成时间")


class RackOperation(RackOperationBase, DataTableMixin, table=True):
    """系统级货架业务操作。"""

    __tablename__: ClassVar[Literal["rack_operations"]] = "rack_operations"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index("ux_rack_operations_key", "operation_key", unique=True),
        Index("ix_rack_operations_status_requested", "operation_status", "requested_at"),
        Index("ix_rack_operations_context", "workline_id", "material_session_id", "operation_status"),
        {"schema": SchemaType.BIZ.value},
    )


class RackTaskBase(BaseMixin):
    """货架级任务基础字段。"""

    task_key: str = Field(min_length=1, max_length=240, index=True, description="任务幂等键")
    operation_id: int | None = Field(
        default=None,
        index=True,
        foreign_key="wes_biz.rack_operations.id",
        description="关联 RackOperation.id",
    )
    operation_key: str = Field(min_length=1, max_length=240, index=True, description="货架操作幂等键")
    operation_type: str = Field(min_length=1, max_length=100, index=True, description="货架操作类型")
    sequence_no: int = Field(index=True, description="同一货架操作下的任务序号")
    task_type: RackTaskType = Field(
        index=True,
        sa_type=cast("Any", SQLAEnum(RackTaskType, native_enum=False, create_constraint=True, length=50)),
        description="任务类型",
    )
    task_status: RackTaskStatus = Field(
        default=RackTaskStatus.PLANNED,
        index=True,
        sa_type=cast("Any", SQLAEnum(RackTaskStatus, native_enum=False, create_constraint=True, length=50)),
        description="任务状态",
    )
    workline_id: int | None = Field(
        default=None,
        index=True,
        foreign_key="wes_biz.work_lines.id",
        description="可选关联 WorkLine.id",
    )
    workline_code: str | None = Field(default=None, max_length=50, index=True, description="可选工作线编码")
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
        foreign_key="wes_biz.system_outbox.id",
        description="关联 SystemOutbox.id",
    )
    target_code: str | None = Field(default=None, max_length=200, description="外部目标逻辑编码")
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


class RackTask(RackTaskBase, DataTableMixin, table=True):
    """货架级任务。"""

    __tablename__: ClassVar[Literal["rack_tasks"]] = "rack_tasks"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index("ux_rack_tasks_key", "task_key", unique=True),
        Index("ux_rack_tasks_dispatch_key", "dispatch_key", unique=True),
        Index("ux_rack_tasks_operation_sequence", "operation_key", "sequence_no", unique=True),
        Index("ix_rack_tasks_operation_id_status", "operation_id", "task_status"),
        Index(
            "ux_rack_tasks_move_source_claim",
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
        Index("ix_rack_tasks_operation_status", "operation_key", "task_status"),
        Index("ix_rack_tasks_session_operation", "material_session_id", "operation_key"),
        Index("ix_rack_tasks_target_status", "workline_code", "target_position_code", "task_status"),
        {"schema": SchemaType.BIZ.value},
    )


class RackTaskCreate(ModelFactory(RackTaskBase).for_create()):
    """货架级任务创建 Schema。"""


class RackTaskUpdate(ModelFactory(RackTaskBase).for_update()):
    """货架级任务更新 Schema。"""


class RackTaskResponse(RackTaskBase):
    """货架级任务响应 Schema。"""

    id: int


class RackOperationCreate(ModelFactory(RackOperationBase).for_create()):
    """货架业务操作创建 Schema。"""


class RackOperationUpdate(ModelFactory(RackOperationBase).for_update()):
    """货架业务操作更新 Schema。"""


class RackOperationResponse(RackOperationBase):
    """货架业务操作响应 Schema。"""

    id: int


__all__ = [
    "RackOperation",
    "RackOperationBase",
    "RackOperationCreate",
    "RackOperationResponse",
    "RackOperationStatus",
    "RackOperationUpdate",
    "RackTask",
    "RackTaskBase",
    "RackTaskCreate",
    "RackTaskResponse",
    "RackTaskStatus",
    "RackTaskType",
    "RackTaskUpdate",
]
