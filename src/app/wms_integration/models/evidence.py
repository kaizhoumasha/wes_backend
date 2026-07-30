"""WMS 调用证据留痕模型。

Evidence 只保存脱敏后的调用快照和摘要 hash。异步 outbox/callback 路径不得复制
SystemOutbox 或 CallbackLog 的完整事实源 payload，只记录关联键和脱敏摘要。
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from enum import Enum
from typing import Any, ClassVar, Literal, cast

from sqlalchemy import Column, Index
from sqlalchemy import Enum as SQLAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from src.core.mixins import DataTableMixin
from src.database.schema_conf import SchemaType
from src.utils.timezone import timezone


class WmsEvidenceStatus(str, Enum):
    """WMS evidence 记录状态。"""

    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    ASYNC_RECORDED = "ASYNC_RECORDED"


class WmsCallEvidence(DataTableMixin, table=True):
    """WMS 同步/异步对接调用证据。"""

    __tablename__: ClassVar[Literal["wms_call_evidence"]] = "wms_call_evidence"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index("ux_wms_call_evidence_key", "evidence_key", unique=True),
        Index("ix_wms_call_evidence_trace_request_dispatch", "trace_id", "request_id", "dispatch_key"),
        Index(
            "ix_wms_call_evidence_provider_operation_started",
            "provider_profile_identity",
            "operation_name",
            "started_at",
        ),
        Index("ix_wms_call_evidence_operation_started", "operation_name", "started_at"),
        Index("ix_wms_call_evidence_status_started", "status", "started_at"),
        Index("ix_wms_call_evidence_request_snapshot_gin", "request_snapshot", postgresql_using="gin"),
        Index("ix_wms_call_evidence_response_snapshot_gin", "response_snapshot", postgresql_using="gin"),
        {"schema": SchemaType.BIZ.value},
    )

    evidence_key: str = Field(min_length=1, max_length=240, description="证据幂等键")
    provider_profile_identity: str | None = Field(
        default=None,
        max_length=240,
        description="同步 QUERY 的冻结 provider profile identity；异步摘要不适用",
    )
    operation_name: str = Field(min_length=1, max_length=120, description="WMS 操作名")
    target_code: str | None = Field(default=None, max_length=240, description="WMS/RCS 目标编码")
    status: WmsEvidenceStatus = Field(
        default=WmsEvidenceStatus.STARTED,
        sa_type=cast(
            "Any",
            SQLAEnum(WmsEvidenceStatus, native_enum=False, create_constraint=True, length=50),
        ),
        description="证据状态",
    )

    request_id: str | None = Field(default=None, max_length=120, description="请求 ID")
    trace_id: str | None = Field(default=None, max_length=120, description="Trace ID")
    dispatch_key: str | None = Field(default=None, max_length=240, description="Outbox 派发键")
    source_ref_type: str | None = Field(default=None, max_length=50, description="异步事实源类型")
    source_ref_id: str | None = Field(default=None, max_length=120, description="异步事实源 ID")

    request_snapshot: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, comment="脱敏请求或异步摘要"),
        description="脱敏请求或异步摘要",
    )
    response_snapshot: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, comment="脱敏响应摘要"),
        description="脱敏响应摘要",
    )
    request_hash: str = Field(min_length=64, max_length=64, description="canonical request sha256")
    response_hash: str | None = Field(
        default=None, min_length=64, max_length=64, description="canonical response sha256"
    )

    http_status: int | None = Field(default=None, description="HTTP 状态码")
    reason_code: str | None = Field(default=None, max_length=120, description="WMS 原因码")
    retryable: bool | None = Field(default=None, description="调用方是否可重试")
    started_at: datetime = Field(default_factory=timezone.now_for_db, description="调用开始时间")
    finished_at: datetime | None = Field(default=None, description="调用结束时间")


__all__ = [
    "WmsCallEvidence",
    "WmsEvidenceStatus",
]
