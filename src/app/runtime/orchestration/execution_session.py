"""当前总控计划 §9 待清理的 ExecutionSession 过渡模型。

现存实现中，Session 是 runtime/orchestration 域的聚合根和唯一 session PK 拥有者。
Session 不持工作状态（work item 是 ExecutionWorkItem 的责任），只持：
- workline_id: 关联 WorkLine (workline 域配置, session 引用)
- manifest_version: RUNNING session 固定 manifest_version (CEO-011)
- state: lifecycle (CREATED / RUNNING / HOLD / CLOSED / RECONCILING)
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - SQLModel table fields need the runtime class at import time.
from typing import ClassVar

from sqlmodel import Field
from sqlmodel._compat import SQLModelConfig

from src.core.mixins.base import BaseMixin

RUNTIME_SCHEMA = "wes_runtime"


class ExecutionSession(BaseMixin, table=True):
    """现存过渡模型的 Runtime/orchestration 会话聚合根（当前总控计划 §9 清理范围）。

    现存实现中由本表持有唯一 session PK；目标模型按顶层 SPEC §6.1 拆分为
    LineRunEpoch 与对象级执行证据，不保留通用 Session 聚合。
    """

    __tablename__ = "execution_sessions"  # pyright: ignore[reportAssignmentType]
    __schema__ = RUNTIME_SCHEMA  # runtime 域新 schema
    __table_args__: ClassVar[dict[str, str]] = {"schema": RUNTIME_SCHEMA}
    model_config = SQLModelConfig(from_attributes=True, extra="forbid")

    id: int | None = Field(default=None, primary_key=True)

    # WorkLine 配置引用 (FK 到 workline.work_lines)
    workline_id: int = Field(
        index=True,
        description="关联 WorkLine 配置 (保留 workline_id 引用)",
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
