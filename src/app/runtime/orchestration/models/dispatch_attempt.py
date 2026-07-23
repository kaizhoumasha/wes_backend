"""工作线派发尝试账本模型。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any, ClassVar, cast

from sqlalchemy import JSON, CheckConstraint, Column, Index, Text
from sqlalchemy import Enum as SQLAEnum
from sqlmodel import Field

from src.app.effect_ledger_status import DispatchAttemptStatus
from src.core.mixins import BaseMixin, DataTableMixin
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType


class WorklineDispatchAttemptBase(BaseMixin):
    """派发尝试基础字段。"""

    outbox_id: int = Field(index=True, foreign_key="wes_biz.system_outbox.id", description="Outbox ID")
    dispatch_key: str = Field(max_length=200, index=True, description="派发键")
    attempt_no: int = Field(index=True, description="同一 outbox 的尝试序号")
    lease_token: str = Field(max_length=240, unique=True, index=True, description="派发租约 token")
    lease_expires_at: datetime = Field(description="该 attempt 继承的有限派发 lease 截止时间")
    status: DispatchAttemptStatus = Field(
        default=DispatchAttemptStatus.DISPATCHING,
        index=True,
        sa_type=cast(
            "Any",
            SQLAEnum(
                DispatchAttemptStatus,
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
        ),
        description="派发尝试状态",
    )
    target_type: str | None = Field(default=None, max_length=100, index=True, description="目标类型")
    target_code: str | None = Field(default=None, max_length=200, index=True, description="目标编码")
    transport_outcome: str | None = Field(
        default=None,
        index=True,
        sa_type=cast(
            "Any",
            SQLAEnum(
                "NOT_SENT",
                "ACCEPTED",
                "AMBIGUOUS",
                name="workline_dispatch_attempt_transport_outcome",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
        ),
        description="EXTERNAL_HTTP transport 结果",
    )
    transport_phase: str | None = Field(
        default=None,
        sa_type=cast(
            "Any",
            SQLAEnum(
                "PREPARING",
                "CONNECTING",
                "SENDING",
                "AWAITING_RESPONSE",
                "RESPONSE_RECEIVED",
                "SANDBOX",
                name="workline_dispatch_attempt_transport_phase",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
        ),
        description="EXTERNAL_HTTP transport 观测阶段",
    )
    protocol_result: str | None = Field(
        default=None,
        index=True,
        sa_type=cast(
            "Any",
            SQLAEnum(
                "NOT_AVAILABLE",
                "ACCEPTED",
                "REJECTED",
                "UNKNOWN",
                name="workline_dispatch_attempt_protocol_result",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
        ),
        description="EXTERNAL_HTTP 协议层结果",
    )
    safe_to_retry: bool | None = Field(default=None, description="明确未发送时是否允许自动重试")
    http_status_code: int | None = Field(default=None, ge=100, le=599, description="HTTP 响应状态码")
    started_at: datetime = Field(index=True, description="开始时间")
    finalized_at: datetime | None = Field(default=None, index=True, description="完成时间")
    error_message: str | None = Field(default=None, sa_column=Column(Text), description="失败原因")
    response_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON), description="派发响应摘要")
    trace_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON), description="trace 投影")


class WorklineDispatchAttempt(WorklineDispatchAttemptBase, DataTableMixin, table=True):
    """工作线派发尝试账本表。"""

    __tablename__: ClassVar[str] = "workline_dispatch_attempts"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        CheckConstraint(
            "length(lease_token) > 0 AND lease_expires_at IS NOT NULL",
            name="ck_workline_dispatch_attempt_lease_expiry",
        ),
        Index(
            "ix_workline_dispatch_attempt_outbox_lease",
            "outbox_id",
            "lease_token",
            "status",
        ),
        {"schema": SchemaType.BIZ.value},
    )


class WorklineDispatchAttemptCreate(ModelFactory(WorklineDispatchAttemptBase).for_create()):
    """派发尝试创建 Schema。"""


__all__ = [
    "WorklineDispatchAttempt",
    "WorklineDispatchAttemptBase",
    "WorklineDispatchAttemptCreate",
]
