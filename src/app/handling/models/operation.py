"""系统级 Handling Operation 模型。"""

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


class HandlingObjectType(str, Enum):
    """Handling 操作对象类型。"""

    BIN = "BIN"


class HandlingOperationStatus(str, Enum):
    """Handling operation 聚合状态。"""

    PLANNED = "PLANNED"
    REQUESTED = "REQUESTED"
    IN_PROGRESS = "IN_PROGRESS"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    RECONCILING = "RECONCILING"
    CANCELLED = "CANCELLED"


class HandlingMoveStatus(str, Enum):
    """Handling move 状态。"""

    PLANNED = "PLANNED"
    REQUESTED = "REQUESTED"
    IN_PROGRESS = "IN_PROGRESS"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    RECONCILING = "RECONCILING"
    CANCELLED = "CANCELLED"


class HandlingStepKind(str, Enum):
    """Handling step 类型。"""

    RESOURCE_RESERVATION = "RESOURCE_RESERVATION"
    EXTERNAL_REQUEST = "EXTERNAL_REQUEST"
    DEVICE_COMMAND = "DEVICE_COMMAND"
    RESOURCE_PROJECTION = "RESOURCE_PROJECTION"
    SNAPSHOT_CONFIRMATION = "SNAPSHOT_CONFIRMATION"
    SESSION_WAKEUP = "SESSION_WAKEUP"


class HandlingStepStatus(str, Enum):
    """Handling step 状态。"""

    PLANNED = "PLANNED"
    READY = "READY"
    REQUESTED = "REQUESTED"
    IN_PROGRESS = "IN_PROGRESS"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    RECONCILING = "RECONCILING"
    CANCELLED = "CANCELLED"


class HandlingOperationBase(BaseMixin):
    """Handling operation 基础字段。"""

    operation_key: str = Field(min_length=1, max_length=240, index=True, description="operation 幂等键")
    operation_type: str = Field(min_length=1, max_length=100, index=True, description="operation 类型")
    object_type: HandlingObjectType = Field(
        index=True,
        sa_type=cast("Any", SQLAEnum(HandlingObjectType, native_enum=False, create_constraint=True, length=50)),
        description="对象类型",
    )
    operation_status: HandlingOperationStatus = Field(
        default=HandlingOperationStatus.PLANNED,
        index=True,
        sa_type=cast("Any", SQLAEnum(HandlingOperationStatus, native_enum=False, create_constraint=True, length=50)),
        description="operation 状态",
    )
    workline_id: int | None = Field(
        default=None,
        index=True,
        foreign_key="wes_biz.work_lines.id",
        description="可选发起/关联 WorkLine.id",
    )
    workline_code: str | None = Field(default=None, max_length=50, index=True, description="可选工作线编码")
    material_session_id: int | None = Field(
        default=None,
        index=True,
        foreign_key="wes_biz.workline_sessions.id",
        description="可选关联 WorklineSession.id",
    )
    trace_id: str | None = Field(default=None, max_length=100, index=True, description="Trace ID")
    carrier_type: str | None = Field(default=None, max_length=50, index=True, description="承运设备类型")
    carrier_code: str | None = Field(default=None, max_length=100, index=True, description="承运设备编码")
    topology_snapshot_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON), description="拓扑快照")
    request_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON), description="内部请求证据")
    result_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON), description="完成结果证据")
    error_code: str | None = Field(default=None, max_length=100, index=True, description="错误码")
    error_message: str | None = Field(default=None, sa_column=Column(Text), description="错误信息")
    requested_at: datetime | None = Field(default=None, index=True, description="请求时间")
    started_at: datetime | None = Field(default=None, index=True, description="开始时间")
    completed_at: datetime | None = Field(default=None, index=True, description="完成时间")


class HandlingOperation(HandlingOperationBase, DataTableMixin, table=True):
    """系统级 Handling operation。"""

    __tablename__: ClassVar[Literal["handling_operations"]] = "handling_operations"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index("ux_handling_operations_key", "operation_key", unique=True),
        Index("ix_handling_operations_status", "operation_status"),
        Index("ix_handling_operations_workline_status", "workline_id", "operation_status"),
        {"schema": SchemaType.BIZ.value},
    )


class HandlingMoveBase(BaseMixin):
    """Handling move 基础字段。"""

    operation_id: int = Field(index=True, foreign_key="wes_biz.handling_operations.id", description="operation.id")
    operation_key: str = Field(min_length=1, max_length=240, index=True, description="operation 幂等键")
    sequence_no: int = Field(index=True, description="operation 内移动序号")
    object_type: HandlingObjectType = Field(
        index=True,
        sa_type=cast("Any", SQLAEnum(HandlingObjectType, native_enum=False, create_constraint=True, length=50)),
        description="移动对象类型",
    )
    move_status: HandlingMoveStatus = Field(
        default=HandlingMoveStatus.PLANNED,
        index=True,
        sa_type=cast("Any", SQLAEnum(HandlingMoveStatus, native_enum=False, create_constraint=True, length=50)),
        description="move 状态",
    )
    rack_code: str | None = Field(default=None, max_length=100, index=True, description="货架编码")
    rack_slot_code: str | None = Field(default=None, max_length=100, index=True, description="货架槽位编码")
    bin_code: str | None = Field(default=None, max_length=100, index=True, description="真实料箱编码")
    placeholder_key: str | None = Field(default=None, max_length=240, index=True, description="临时占位键")
    resolved_bin_code: str | None = Field(default=None, max_length=100, index=True, description="扫码解析后的料箱编码")
    candidate_authorized_bin_ids: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSON),
        description="候选授权料箱集合",
    )
    source_type: str = Field(min_length=1, max_length=100, index=True, description="来源类型")
    source_code: str = Field(min_length=1, max_length=160, index=True, description="来源编码")
    target_type: str = Field(min_length=1, max_length=100, index=True, description="目标类型")
    target_code: str = Field(min_length=1, max_length=160, index=True, description="目标编码")
    carrier_type: str | None = Field(default=None, max_length=50, index=True, description="承运类型")
    carrier_code: str | None = Field(default=None, max_length=100, index=True, description="承运编码")
    required: bool = Field(default=True, index=True, description="是否影响 operation 成功")
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON), description="移动证据")


class HandlingMove(HandlingMoveBase, DataTableMixin, table=True):
    """系统级 Handling move。"""

    __tablename__: ClassVar[Literal["handling_operation_moves"]] = "handling_operation_moves"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index("ux_handling_moves_operation_sequence", "operation_key", "sequence_no", unique=True),
        Index(
            "ux_handling_moves_active_known_bin",
            "bin_code",
            unique=True,
            postgresql_where=text(
                "bin_code IS NOT NULL AND move_status IN ('PLANNED', 'REQUESTED', 'IN_PROGRESS', 'RECONCILING')"
            ),
            sqlite_where=text(
                "bin_code IS NOT NULL AND move_status IN ('PLANNED', 'REQUESTED', 'IN_PROGRESS', 'RECONCILING')"
            ),
        ),
        Index(
            "ux_handling_moves_active_placeholder",
            "placeholder_key",
            unique=True,
            postgresql_where=text(
                "placeholder_key IS NOT NULL AND move_status IN ('PLANNED', 'REQUESTED', 'IN_PROGRESS', 'RECONCILING')"
            ),
            sqlite_where=text(
                "placeholder_key IS NOT NULL AND move_status IN ('PLANNED', 'REQUESTED', 'IN_PROGRESS', 'RECONCILING')"
            ),
        ),
        Index("ix_handling_moves_target_status", "target_type", "target_code", "move_status"),
        {"schema": SchemaType.BIZ.value},
    )


class HandlingStepBase(BaseMixin):
    """Handling step 基础字段。"""

    operation_id: int = Field(index=True, foreign_key="wes_biz.handling_operations.id", description="operation.id")
    operation_key: str = Field(min_length=1, max_length=240, index=True, description="operation 幂等键")
    move_id: int | None = Field(
        default=None,
        index=True,
        foreign_key="wes_biz.handling_operation_moves.id",
        description="关联 move.id",
    )
    sequence_no: int = Field(index=True, description="operation 内 step 序号")
    step_key: str = Field(min_length=1, max_length=240, index=True, description="step 幂等键")
    step_kind: HandlingStepKind = Field(
        index=True,
        sa_type=cast("Any", SQLAEnum(HandlingStepKind, native_enum=False, create_constraint=True, length=50)),
        description="step 类型",
    )
    step_status: HandlingStepStatus = Field(
        default=HandlingStepStatus.PLANNED,
        index=True,
        sa_type=cast("Any", SQLAEnum(HandlingStepStatus, native_enum=False, create_constraint=True, length=50)),
        description="step 状态",
    )
    dispatch_key: str | None = Field(default=None, max_length=240, index=True, description="外部派发键")
    outbox_id: int | None = Field(
        default=None,
        index=True,
        foreign_key="wes_biz.system_outbox.id",
        description="关联 system_outbox.id",
    )
    command_id: int | None = Field(default=None, index=True, description="关联 DeviceCommand.id")
    target_code: str | None = Field(default=None, max_length=240, index=True, description="目标编码")
    trace_id: str | None = Field(default=None, max_length=100, index=True, description="Trace ID")
    request_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON), description="请求证据")
    callback_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON), description="回调证据")
    result_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON), description="结果证据")
    error_code: str | None = Field(default=None, max_length=100, index=True, description="错误码")
    error_message: str | None = Field(default=None, sa_column=Column(Text), description="错误信息")
    started_at: datetime | None = Field(default=None, index=True, description="开始时间")
    completed_at: datetime | None = Field(default=None, index=True, description="完成时间")


class HandlingStep(HandlingStepBase, DataTableMixin, table=True):
    """系统级 Handling step。"""

    __tablename__: ClassVar[Literal["handling_operation_steps"]] = "handling_operation_steps"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index("ux_handling_steps_key", "step_key", unique=True),
        Index("ux_handling_steps_dispatch_key", "dispatch_key", unique=True),
        Index("ix_handling_steps_operation_status", "operation_id", "step_status"),
        Index("ix_handling_steps_kind_status", "step_kind", "step_status"),
        {"schema": SchemaType.BIZ.value},
    )


class HandlingOperationCreate(ModelFactory(HandlingOperationBase).for_create()):
    """Handling operation 创建 Schema。"""


class HandlingOperationUpdate(ModelFactory(HandlingOperationBase).for_update()):
    """Handling operation 更新 Schema。"""


class HandlingOperationResponse(HandlingOperationBase):
    """Handling operation 响应 Schema。"""

    id: int


class HandlingMoveCreate(ModelFactory(HandlingMoveBase).for_create()):
    """Handling move 创建 Schema。"""


class HandlingMoveResponse(HandlingMoveBase):
    """Handling move 响应 Schema。"""

    id: int


class HandlingStepCreate(ModelFactory(HandlingStepBase).for_create()):
    """Handling step 创建 Schema。"""


class HandlingStepUpdate(ModelFactory(HandlingStepBase).for_update()):
    """Handling step 更新 Schema。"""


class HandlingStepResponse(HandlingStepBase):
    """Handling step 响应 Schema。"""

    id: int


__all__ = [
    "HandlingMove",
    "HandlingMoveBase",
    "HandlingMoveCreate",
    "HandlingMoveResponse",
    "HandlingMoveStatus",
    "HandlingObjectType",
    "HandlingOperation",
    "HandlingOperationBase",
    "HandlingOperationCreate",
    "HandlingOperationResponse",
    "HandlingOperationStatus",
    "HandlingOperationUpdate",
    "HandlingStep",
    "HandlingStepBase",
    "HandlingStepCreate",
    "HandlingStepKind",
    "HandlingStepResponse",
    "HandlingStepStatus",
    "HandlingStepUpdate",
]
