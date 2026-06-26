"""RuntimeIntentLog (Phase 1 CEO-007 #7, 主计划 §9.2)。

outbox/effect ledger: Runtime 曾尝试发出的意图记录。
不是下游状态源 — 下游状态仍归 handling/device/resource/material/wms_integration
各 owner。

dispatch_status 5 态 (主计划 §9.2):
  PENDING -> DISPATCHING -> DISPATCHED / ACKED / FAILED

崩溃重放: 进程崩溃恢复时只重放 PENDING 或过期 DISPATCHING 且 request_hash
一致的记录; 不允许重新构造 payload 发起新 effect。

H5 (Phase 1 最小版本): 同 key 不同 hash 拒绝 (outbound effect replay 不双发);
完整 409 安全审计留 Phase 3 ENG-009。
"""

from __future__ import annotations

from sqlmodel import Field, SQLModel


class RuntimeIntentLog(SQLModel, table=True):
    """outbox/effect ledger (主计划 §9.2)。

    每条 effect 必带 correlation_id / provider_code / idempotency_key /
    request_hash, 用于崩溃恢复、幂等复查和乱序回调归因。
    """

    __tablename__ = "runtime_intent_logs"
    __schema__ = "wes_runtime"

    id: int | None = Field(default=None, primary_key=True)

    # runtime 域内强 FK
    execution_session_id: int = Field(index=True)
    correlation_id: str = Field(max_length=120, index=True)

    # effect 元数据
    provider_code: str = Field(max_length=60, index=True)
    target_domain: str = Field(max_length=60, description="handling / device / wms_integration")
    target_action: str = Field(max_length=120)

    # 幂等
    idempotency_key: str = Field(max_length=160, index=True)
    request_hash: str = Field(max_length=128, description="immutable payload hash")

    # dispatch 状态机
    dispatch_status: str = Field(
        max_length=20,
        default="PENDING",
        index=True,
        description="PENDING / DISPATCHING / DISPATCHED / ACKED / FAILED",
    )

    # 重试字段
    attempt_count: int = Field(default=0)
    last_error_code: str | None = Field(default=None, max_length=120)
    last_error_message: str | None = Field(default=None, max_length=500)
