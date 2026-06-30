"""统一对象状态迁移事件模型。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from enum import Enum
from typing import Any, ClassVar, cast

from sqlalchemy import JSON, Column, Index, Text, text
from sqlalchemy import Enum as SQLAEnum
from sqlmodel import Field

from src.core.mixins import BaseMixin, DataTableMixin
from src.core.mixins.primary_key import SQL_COMPAT_BIGINT
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType
from src.utils.timezone import timezone


class ObjectTransitionDomain(str, Enum):
    """对象迁移事件所属业务域。"""

    RESOURCE = "RESOURCE"
    HANDLING = "HANDLING"


class ObjectTransitionEventBase(BaseMixin):
    """统一对象状态迁移事件基础字段。"""

    domain: ObjectTransitionDomain = Field(
        sa_type=cast(
            "Any",
            SQLAEnum(ObjectTransitionDomain, native_enum=False, create_constraint=False, length=50),
        ),
        description="迁移事件业务域",
    )
    object_type: str = Field(min_length=1, max_length=100, description="对象类型")
    object_key: str = Field(min_length=1, max_length=300, description="对象业务键")
    projection_type: str = Field(min_length=1, max_length=100, description="投影类型")
    from_state: str | None = Field(default=None, max_length=100, description="迁移前状态")
    to_state: str = Field(min_length=1, max_length=100, description="迁移后状态")
    reason_code: str = Field(min_length=1, max_length=100, description="迁移原因码")
    source_event_id: str = Field(min_length=1, max_length=200, description="来源事实事件 ID")
    source_ref_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, default=dict),
        description="来源引用",
    )
    evidence_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, default=dict),
        description="脱敏证据",
    )
    workline_session_id: int | None = Field(
        default=None,
        sa_type=SQL_COMPAT_BIGINT,
        description="关联 workline_sessions.id 的追溯字段",
    )
    trace_id: str | None = Field(default=None, max_length=100, description="统一 trace ID")
    occurred_at: datetime = Field(default_factory=timezone.now_for_db, description="事件发生时间 UTC")
    idempotency_key: str | None = Field(
        default=None, max_length=500, sa_column=Column(Text), description="派生迁移幂等键"
    )


class ObjectTransitionEvent(ObjectTransitionEventBase, DataTableMixin, table=True):
    """append-only 对象状态迁移事件表。"""

    __tablename__: ClassVar[str] = "object_transition_events"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index(
            "uq_object_transition_events_idempotency_key_not_null",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
            sqlite_where=text("idempotency_key IS NOT NULL"),
        ),
        Index("ix_object_transition_events_trace_occurred", "trace_id", "occurred_at"),
        Index("ix_object_transition_events_session_occurred", "workline_session_id", "occurred_at"),
        Index("ix_object_transition_events_object_occurred", "domain", "object_type", "object_key", "occurred_at"),
        Index("ix_object_transition_events_domain_source", "domain", "source_event_id"),
        {"schema": SchemaType.BIZ.value},
    )


class ObjectTransitionEventCreate(ModelFactory(ObjectTransitionEventBase).for_create()):
    """对象迁移事件创建 Schema。"""


class ObjectTransitionEventResponse(ObjectTransitionEventBase):
    """对象迁移事件响应 Schema。"""

    id: int
    created_at: datetime
    updated_at: datetime | None = None


__all__ = [
    "ObjectTransitionDomain",
    "ObjectTransitionEvent",
    "ObjectTransitionEventBase",
    "ObjectTransitionEventCreate",
    "ObjectTransitionEventResponse",
]
