"""当前总控计划 §9 待清理的 ExecutionCorrelation 过渡模型。

跨域 correlation key；本模型属于当前总控计划 §9 清理范围，目标边界见顶层 SPEC §6.1。

以下字段记录现存过渡模型，并不代表目标数据模型：
字段组:
- correlation_id: 跨域稳定 correlation key, 唯一
- execution_session_id: runtime/orchestration 内部强 FK (NULL 允许 inbound
  callback 在未解析 session 前先 ACK)
- trace_id: 跨域 trace 时间线
- source_event_id: 外部事件归因 (request_id / event_id / command_code)
- business_owner_key: 业务 owner 审计

现存实现中的 idempotency_keys 是独立表，使用复合主键
(provider_code, operation_kind, idempotency_key), 通过 execution_correlation_id 引用
本表, 不重复 storage (schema 对齐)。
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - SQLModel table fields need the runtime class at import time.
from typing import ClassVar

from sqlmodel import Field
from sqlmodel._compat import SQLModelConfig

from src.app.runtime.orchestration.execution_session import (
    RUNTIME_SCHEMA,
    ExecutionSession,
)
from src.core.mixins.base import BaseMixin


class ExecutionCorrelation(BaseMixin, table=True):
    """现存过渡模型的跨域 correlation key（当前总控计划 §9 清理范围）。

    替代旧跨域 session FK 强引用, 跨域只持本表 correlation_id (无 session FK)。
    execution_session_id 允许为 NULL，使 inbound callback 可在解析前先 ACK。
    """

    __tablename__ = "execution_correlations"  # pyright: ignore[reportAssignmentType]
    __schema__ = RUNTIME_SCHEMA  # runtime 域新 schema (区别 workline/wes_biz)
    __table_args__: ClassVar[dict[str, str]] = {"schema": RUNTIME_SCHEMA}
    model_config = SQLModelConfig(from_attributes=True, extra="forbid")

    id: int | None = Field(default=None, primary_key=True)

    # 跨域稳定 correlation key, 唯一
    correlation_id: str = Field(
        min_length=1,
        max_length=120,
        unique=True,
        index=True,
        description="跨域稳定 correlation key, 唯一",
    )

    # runtime/orchestration 内部强 FK (NULL 允许 inbound callback 未解析前 ACK)
    execution_session_id: int | None = Field(
        default=None,
        foreign_key=f"{RUNTIME_SCHEMA}.{ExecutionSession.__tablename__}.id",
        description="runtime/orchestration 域内强 FK, 跨域不持",
    )

    # 跨域 trace 时间线
    trace_id: str = Field(
        min_length=1,
        max_length=120,
        index=True,
        description="跨域 trace 时间线",
    )

    # 外部事件归因
    source_event_id: str | None = Field(
        default=None,
        max_length=160,
        description="外部事件归因 (request_id / event_id / command_code)",
    )

    # 业务 owner 审计
    business_owner_key: str | None = Field(
        default=None,
        max_length=160,
        description="业务 owner 审计, 查询和冲突定位",
    )

    # 通用时间戳
    created_at: datetime | None = Field(
        default=None,
        description="naive UTC for DB",
    )
    updated_at: datetime | None = Field(
        default=None,
        description="naive UTC for DB",
    )
