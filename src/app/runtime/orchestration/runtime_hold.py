"""RuntimeHold (Phase 1 CEO-007 #6, 主计划 §9.2)。

暂停新 effect 的运行时闸门; 必须有 object/device/resource scope。
解除必须声明 allowed_next_effect_scope。

RuntimeHold scope 契约 (主计划 §9.2):
- scope_type 枚举: WORK_ITEM / OBJECT / DEVICE / RESOURCE / QUEUE / SESSION / WORKLINE
- 优先使用小 scope; 只有影响整线安全时才用 SESSION/WORKLINE
- 解除必须声明 allowed_next_effect_scope
"""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import BigInteger
from sqlmodel import Field

from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation  # noqa: F401
from src.app.runtime.orchestration.execution_session import RUNTIME_SCHEMA
from src.core.mixins.base import BaseMixin


class RuntimeHold(BaseMixin, table=True):
    """运行时闸门 (主计划 §9.2)。"""

    __tablename__ = "runtime_holds"
    __schema__ = RUNTIME_SCHEMA
    __table_args__: ClassVar[dict[str, str]] = {"schema": RUNTIME_SCHEMA}

    id: int | None = Field(default=None, primary_key=True)

    execution_session_id: int = Field(
        foreign_key=f"{RUNTIME_SCHEMA}.execution_sessions.id",
        index=True,
    )
    correlation_id: str | None = Field(
        default=None,
        foreign_key=f"{RUNTIME_SCHEMA}.execution_correlations.correlation_id",
        max_length=120,
        index=True,
    )

    reason: str = Field(max_length=200)
    hold_type: str = Field(
        max_length=60,
        description="RESOURCE_WAIT / SAFETY / RECONCILING / MANUAL / TIMEOUT",
    )

    # scope
    scope_type: str = Field(
        max_length=20,
        index=True,
        description="WORK_ITEM / OBJECT / DEVICE / RESOURCE / QUEUE / SESSION / WORKLINE",
    )
    scope_key: str = Field(max_length=160, index=True)

    # 解除
    resolved_at: int | None = Field(default=None, sa_type=BigInteger, description="Unix timestamp ms")
    allowed_next_effect_scope: str | None = Field(
        default=None,
        max_length=200,
        description="解除时声明的允许下一步 effect 范围",
    )
