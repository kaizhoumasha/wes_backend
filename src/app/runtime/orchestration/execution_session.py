"""ExecutionSession (Phase 1 Packet C 起步, 主计划 §9.2)。

Session 是 runtime/orchestration 域的聚合根, 唯一 session PK 拥有者
(主计划 §3.2)。Session 不持工作状态 (work item 是 ExecutionWorkItem 的责任),
Session 只持:
- workline_id: 关联 WorkLine (workline 域配置, session 引用)
- manifest_version: RUNNING session 固定 manifest_version (CEO-011)
- state: lifecycle (CREATED / RUNNING / HOLD / CLOSED / RECONCILING)
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - SQLModel table fields need the runtime class at import time.

from sqlmodel import Field, SQLModel
from sqlmodel._compat import SQLModelConfig

RUNTIME_SCHEMA = "wes_runtime"


class ExecutionSession(SQLModel, table=True):
    """Runtime/orchestration 域会话聚合根 (主计划 §9.2)。

    唯一 session PK 拥有者 (target-state-contract.md §3 域边界); 跨域只持
    ExecutionCorrelation.correlation_id, 不持强 session FK。
    """

    __tablename__ = "execution_sessions"
    __schema__ = RUNTIME_SCHEMA  # runtime 域新 schema
    __table_args__ = {"schema": RUNTIME_SCHEMA}
    model_config = SQLModelConfig(from_attributes=True, extra="forbid")

    id: int | None = Field(default=None, primary_key=True)

    # WorkLine 关联 (FK 到 workline.work_lines, Phase 1 保留)
    workline_id: int = Field(
        index=True,
        description="关联 WorkLine (Phase 1 保留 Phase 0 workline_id 引用)",
    )

    # manifest version pin (Phase 1 CEO-011)
    manifest_version: str = Field(
        min_length=1,
        max_length=60,
        description="RUNNING session 固定 manifest_version, 新 manifest 只影响新 session",
    )

    # Lifecycle state
    state: str = Field(
        min_length=1,
        max_length=20,
        default="CREATED",
        description="session 生命周期: CREATED / RUNNING / HOLD / CLOSED / RECONCILING",
    )

    # 时间戳
    created_at: datetime | None = Field(default=None, description="naive UTC for DB")
    updated_at: datetime | None = Field(default=None, description="naive UTC for DB")
    closed_at: datetime | None = Field(default=None, description="session close timestamp")
