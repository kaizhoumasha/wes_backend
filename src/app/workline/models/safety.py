"""WorkLine 安全事件模型。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - SQLModel needs the runtime type for table fields
from enum import Enum
from typing import Any, ClassVar, Literal, cast

from pydantic import BaseModel
from sqlalchemy import JSON, Column, Text
from sqlalchemy import Enum as SQLAEnum
from sqlmodel import Field

from src.core.mixins import DataTableMixin, EnterpriseMixin
from src.database.schema_conf import SchemaType


class WorklineSafetyIncidentStatus(str, Enum):
    """WorkLine 安全事件状态。"""

    ACTIVE = "ACTIVE"
    CLEARED = "CLEARED"
    UNRESOLVED = "UNRESOLVED"


class ClearWorkLineEstopRequest(BaseModel):
    """人工清除 WorkLine 急停请求。"""

    checks: dict[str, bool] = Field(default_factory=dict, description="恢复 checklist；所有项必须为 true")
    reason: str | None = Field(default=None, max_length=500, description="恢复说明")


class SimulateWorkLineEstopRequest(BaseModel):
    """沙箱模拟 WorkLine 软件急停请求。"""

    reason: str | None = Field(default=None, max_length=500, description="模拟急停说明")
    source_device_id: int | None = Field(default=None, description="模拟来源设备 ID")
    payload: dict[str, Any] = Field(default_factory=dict, description="模拟触发 payload")


class WorklineSafetyIncident(
    EnterpriseMixin,
    DataTableMixin,
    table=True,
):
    """WorkLine 安全事件审计表。"""

    __tablename__: ClassVar[Literal["workline_safety_incidents"]] = "workline_safety_incidents"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value

    workline_id: int | None = Field(
        default=None,
        index=True,
        foreign_key="wes_biz.work_lines.id",
        description="关联 WorkLine.id；无法解析时可为空并进入 UNRESOLVED",
    )
    status: WorklineSafetyIncidentStatus = Field(
        default=WorklineSafetyIncidentStatus.ACTIVE,
        index=True,
        sa_type=cast(
            "Any",
            SQLAEnum(
                WorklineSafetyIncidentStatus,
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
        ),
        description="安全事件状态",
    )
    event_type: str = Field(
        default="ESTOP_PRESSED",
        max_length=100,
        index=True,
        description="触发事件类型",
    )
    reason: str = Field(
        default="ESTOP_PRESSED",
        max_length=200,
        description="安全事件原因",
    )
    source_inbox_id: int | None = Field(default=None, index=True, description="来源 Inbox ID")
    source_device_id: int | None = Field(default=None, index=True, description="来源设备 ID")
    source_command_id: int | None = Field(default=None, index=True, description="来源指令 ID")
    trigger_payload_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON),
        description="触发事件原始 payload 摘要",
    )
    evidence_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON),
        description="冻结与排空证据",
    )
    release_evidence_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON),
        description="释放/复位侧证据，仅作审计",
    )
    recovery_check_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON),
        description="恢复 checklist 结果",
    )
    drain_status: str = Field(
        default="PENDING",
        max_length=50,
        index=True,
        description="排空状态",
    )
    drain_error_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON),
        description="排空异常摘要",
    )
    cleared_at: datetime | None = Field(default=None, index=True, description="人工清除时间")
    cleared_by: int | None = Field(default=None, description="人工清除操作人")
    clear_reason: str | None = Field(default=None, sa_column=Column(Text), description="人工清除说明")
    resolution_inputs_tried: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSON),
        description="未解析事件尝试过的归属输入",
    )
    missing_identifiers: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSON),
        description="未解析事件缺失的关键标识",
    )
    next_action: str | None = Field(default=None, max_length=200, description="未解析事件下一步处理建议")


__all__ = [
    "ClearWorkLineEstopRequest",
    "SimulateWorkLineEstopRequest",
    "WorklineSafetyIncident",
    "WorklineSafetyIncidentStatus",
]
