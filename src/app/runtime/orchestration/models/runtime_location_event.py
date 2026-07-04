"""RuntimeLocationEvent 作业期位置事实模型。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any, ClassVar

from sqlalchemy import JSON, Column, Index, Text, text
from sqlmodel import Field

from src.core.mixins import BaseMixin, DataTableMixin
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType
from src.utils.timezone import timezone


class RuntimeLocationEventBase(BaseMixin):
    """作业期对象位置事实基础字段。"""

    object_type: str = Field(min_length=1, max_length=80, index=True, description="对象类型")
    object_key: str = Field(min_length=1, max_length=300, index=True, description="对象业务键")
    location_scope: str = Field(min_length=1, max_length=80, index=True, description="位置作用域")
    location_code: str = Field(min_length=1, max_length=300, index=True, description="位置编码")
    business_step: str = Field(min_length=1, max_length=120, index=True, description="业务步骤")
    source: str = Field(min_length=1, max_length=80, index=True, description="位置事实来源")
    evidence_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, default=dict),
        description="位置事实证据",
    )
    correlation_id: str | None = Field(default=None, max_length=120, index=True, description="ExecutionCorrelation")
    source_event_id: str | None = Field(default=None, max_length=200, index=True, description="来源事件 ID")
    source_version: str | None = Field(default=None, max_length=80, index=True, description="来源版本")
    idempotency_key: str | None = Field(
        default=None,
        max_length=500,
        sa_column=Column(Text),
        description="位置事实幂等键",
    )
    external_reference_type: str | None = Field(default=None, max_length=100, index=True, description="外部引用类型")
    external_reference_value: str | None = Field(default=None, max_length=300, index=True, description="外部引用值")
    provider_code: str | None = Field(default=None, max_length=80, index=True, description="provider code")
    occurred_at: datetime = Field(default_factory=timezone.now_for_db, index=True, description="事实发生时间 UTC")


class RuntimeLocationEvent(RuntimeLocationEventBase, DataTableMixin, table=True):
    """append-only 作业期位置事实表。"""

    __tablename__: ClassVar[str] = "runtime_location_events"
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index(
            "uq_runtime_location_events_idempotency_key_not_null",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
            sqlite_where=text("idempotency_key IS NOT NULL"),
        ),
        Index("ix_runtime_location_events_object_occurred", "object_type", "object_key", "occurred_at"),
        Index("ix_runtime_location_events_correlation_occurred", "correlation_id", "occurred_at"),
        Index(
            "ix_runtime_location_events_external_ref",
            "provider_code",
            "external_reference_type",
            "external_reference_value",
            "occurred_at",
        ),
        Index("ix_runtime_location_events_source_event", "source", "source_event_id"),
        {"schema": SchemaType.BIZ.value},
    )


class RuntimeLocationEventCreate(ModelFactory(RuntimeLocationEventBase).for_create()):
    """位置事实创建 Schema。"""


class RuntimeLocationEventResponse(RuntimeLocationEventBase):
    """位置事实响应 Schema。"""

    id: int
    created_at: datetime
    updated_at: datetime | None = None


__all__ = [
    "RuntimeLocationEvent",
    "RuntimeLocationEventBase",
    "RuntimeLocationEventCreate",
    "RuntimeLocationEventResponse",
]
