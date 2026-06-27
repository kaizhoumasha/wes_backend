"""ExecutionWorkItem (Phase 1 CEO-007 #3, 主计划 §9.2)。

对象级执行令牌: runtime capability 的最小推进单位。
粗分机单个料盘、分拣机单个物料、滚筒线单个料箱都必须有独立 correlation。

对象级流水并发契约 (主计划 §9.2):
- ExecutionSession 不是整条 WorkLine 的串行锁
- WorkItem 独立推进, 设备串行只按 DeviceDispatchPolicy
- 父子 work item 只用于追溯和批次收敛, 不允许子项失败静默污染父项成功
"""

from __future__ import annotations

from sqlalchemy import UniqueConstraint
from sqlmodel import Field

from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation  # noqa: F401
from src.app.runtime.orchestration.execution_session import RUNTIME_SCHEMA
from src.core.mixins.base import BaseMixin


class ExecutionWorkItem(BaseMixin, table=True):
    """对象级执行令牌 (主计划 §9.2)。"""

    __tablename__ = "execution_work_items"
    __schema__ = RUNTIME_SCHEMA
    __table_args__ = (
        UniqueConstraint("correlation_id", name="uq_wes_runtime_execution_work_items_correlation_id"),
        {"schema": RUNTIME_SCHEMA},
    )

    id: int | None = Field(default=None, primary_key=True)

    execution_session_id: int = Field(
        foreign_key=f"{RUNTIME_SCHEMA}.execution_sessions.id",
        index=True,
    )
    correlation_id: str = Field(
        foreign_key=f"{RUNTIME_SCHEMA}.execution_correlations.correlation_id",
        max_length=120,
    )

    # 对象身份
    object_type: str = Field(max_length=60, description="bin / material / pkg / rack")
    object_key: str = Field(max_length=160, index=True)

    # 步骤推进
    current_step: str = Field(max_length=120)
    step_status: str = Field(
        max_length=20,
        default="PENDING",
        description="PENDING / IN_PROGRESS / COMPLETED / FAILED / SKIPPED",
    )

    # 父子关系
    parent_correlation_id: str | None = Field(
        default=None,
        foreign_key=f"{RUNTIME_SCHEMA}.execution_work_items.correlation_id",
        max_length=120,
        index=True,
    )

    # 并发控制
    lease_expires_at: int | None = Field(default=None, description="Unix timestamp")
