"""RuntimeTimeline (主计划 §9.2)。

append-only 执行轨迹, 不作为 owner 状态源。
记录 session 生命周期内所有事件 (inbox / intent / hold / projection / device)。
"""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import BigInteger
from sqlmodel import Field

from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation  # noqa: F401
from src.app.runtime.orchestration.execution_session import RUNTIME_SCHEMA
from src.core.mixins.base import BaseMixin


class RuntimeTimeline(BaseMixin, table=True):
    """append-only 执行轨迹 (主计划 §9.2)。"""

    __tablename__ = "runtime_timelines"
    __schema__ = RUNTIME_SCHEMA
    __table_args__: ClassVar[dict[str, str]] = {"schema": RUNTIME_SCHEMA}

    id: int | None = Field(default=None, primary_key=True)

    execution_session_id: int = Field(
        foreign_key=f"{RUNTIME_SCHEMA}.execution_sessions.id",
        index=True,
    )
    trace_id: str = Field(max_length=120, index=True)
    correlation_id: str | None = Field(
        default=None,
        foreign_key=f"{RUNTIME_SCHEMA}.execution_correlations.correlation_id",
        max_length=120,
        index=True,
    )

    event_type: str = Field(max_length=80, index=True)
    occurred_at: int = Field(index=True, sa_type=BigInteger, description="Unix timestamp ms")
