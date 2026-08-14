"""工作线诊断模型。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from enum import Enum
from typing import Any, ClassVar, cast

from sqlalchemy import JSON, Column, Text
from sqlalchemy import Enum as SQLAEnum
from sqlmodel import Field

from src.core.mixins import BaseMixin, DataTableMixin
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType


class DiagnosticStatus(str, Enum):
    """诊断状态。"""

    ACTIVE = "ACTIVE"
    RESOLVED = "RESOLVED"
    SUPPRESSED = "SUPPRESSED"


class WorklineDiagnosticBase(BaseMixin):
    """工作线诊断基础字段。"""

    diagnostic_key: str = Field(max_length=300, unique=True, index=True, description="诊断幂等键")
    trace_id: str | None = Field(default=None, max_length=100, index=True, description="统一 trace ID")
    request_id: str | None = Field(default=None, max_length=200, index=True, description="入口请求 ID")
    event_id: str | None = Field(default=None, max_length=200, index=True, description="供应商事件 ID")
    causation_id: str | None = Field(default=None, max_length=200, description="因果事件 ID")

    session_id: int | None = Field(default=None, index=True, foreign_key="wes_biz.workline_sessions.id")
    inbox_id: int | None = Field(default=None, index=True, foreign_key="wes_runtime.runtime_inbox.id")
    outbox_id: int | None = Field(default=None, index=True, foreign_key="wes_biz.system_outbox.id")
    command_code: str | None = Field(default=None, max_length=200, index=True)
    device_code: str | None = Field(default=None, max_length=100, index=True)
    workline_id: int | None = Field(default=None, index=True, foreign_key="wes_biz.work_lines.id")

    diagnostic_code: str = Field(max_length=100, index=True, description="标准诊断码")
    error_domain: str = Field(max_length=100, index=True, description="错误域")
    severity: str = Field(max_length=50, index=True, description="严重度")
    recoverability: str = Field(max_length=100, index=True, description="恢复方式")
    problem_class: str = Field(max_length=50, index=True, description="软硬件归属")
    owner: str = Field(max_length=100, index=True, description="默认责任边界")
    status: DiagnosticStatus = Field(
        default=DiagnosticStatus.ACTIVE,
        index=True,
        sa_type=cast(
            "Any",
            SQLAEnum(
                DiagnosticStatus,
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
        ),
        description="诊断状态",
    )

    message: str = Field(sa_column=Column(Text), description="诊断摘要")
    operator_action: str | None = Field(default=None, sa_column=Column(Text), description="现场操作建议")
    technical_summary: str | None = Field(default=None, sa_column=Column(Text), description="技术摘要")
    docs_anchor: str | None = Field(default=None, max_length=200, description="文档锚点")

    next_steps_json: list[str] = Field(default_factory=list, sa_column=Column(JSON), description="后续步骤")
    evidence_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON), description="脱敏证据")
    card_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON), description="诊断卡快照")
    resolved_at: datetime | None = Field(default=None, description="解决时间")


class WorklineDiagnostic(WorklineDiagnosticBase, DataTableMixin, table=True):
    """工作线诊断持久化表。"""

    __tablename__: ClassVar[str] = "workline_diagnostics"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value


class WorklineDiagnosticCreate(ModelFactory(WorklineDiagnosticBase).for_create()):
    """诊断创建 Schema。"""


__all__ = [
    "DiagnosticStatus",
    "WorklineDiagnostic",
    "WorklineDiagnosticBase",
    "WorklineDiagnosticCreate",
]
