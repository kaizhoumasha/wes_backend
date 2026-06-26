"""RuntimeHold (Phase 1 CEO-007 #6, 主计划 §9.2)。

暂停新 effect 的运行时闸门; 必须有 object/device/resource scope。
解除必须声明 allowed_next_effect_scope。

RuntimeHold scope 契约 (主计划 §9.2):
- scope_type 枚举: WORK_ITEM / OBJECT / DEVICE / RESOURCE / QUEUE / SESSION / WORKLINE
- 优先使用小 scope; 只有影响整线安全时才用 SESSION/WORKLINE
- 解除必须声明 allowed_next_effect_scope
"""

from __future__ import annotations

from sqlmodel import Field, SQLModel


class RuntimeHold(SQLModel, table=True):
    """运行时闸门 (主计划 §9.2)。"""

    __tablename__ = "runtime_holds"
    __schema__ = "wes_runtime"

    id: int | None = Field(default=None, primary_key=True)

    execution_session_id: int = Field(index=True)
    correlation_id: str | None = Field(default=None, max_length=120, index=True)

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
    resolved_at: int | None = Field(default=None, description="Unix timestamp ms")
    allowed_next_effect_scope: str | None = Field(
        default=None,
        max_length=200,
        description="解除时声明的允许下一步 effect 范围",
    )
