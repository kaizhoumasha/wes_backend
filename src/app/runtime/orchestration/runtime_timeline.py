"""RuntimeTimeline (Phase 1 CEO-007 #5, 主计划 §9.2)。

append-only 执行轨迹, 不作为 owner 状态源。
记录 session 生命周期内所有事件 (inbox / intent / hold / projection / device)。
"""

from __future__ import annotations

from sqlmodel import Field, SQLModel


class RuntimeTimeline(SQLModel, table=True):
    """append-only 执行轨迹 (主计划 §9.2)。"""

    __tablename__ = "runtime_timelines"
    __schema__ = "wes_runtime"

    id: int | None = Field(default=None, primary_key=True)

    execution_session_id: int = Field(index=True)
    trace_id: str = Field(max_length=120, index=True)
    correlation_id: str | None = Field(default=None, max_length=120, index=True)

    event_type: str = Field(max_length=80, index=True)
    occurred_at: int = Field(index=True, description="Unix timestamp ms")
