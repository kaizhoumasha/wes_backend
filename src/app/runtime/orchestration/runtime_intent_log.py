"""RuntimeIntentLog runtime effect ledger。

outbox/effect ledger: Runtime 曾尝试发出的意图记录。
不是下游状态源 — 下游状态仍归 handling/device/resource/material/wms_integration
各 owner。

dispatch_status 5 态 (主计划 §9.2):
  PENDING -> DISPATCHING -> DISPATCHED / ACKED / FAILED

崩溃重放: 进程崩溃恢复时只重放 PENDING 或过期 DISPATCHING 且 request_hash
一致的记录; 不允许重新构造 payload 发起新 effect。

Runtime idempotency minimum contract: 同 key 不同 hash 拒绝 (outbound effect replay 不双发);
完整 409 安全审计由 runtime audit matrix 覆盖。
"""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import JSON, BigInteger, Column, UniqueConstraint
from sqlmodel import Field

from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation  # noqa: F401
from src.app.runtime.orchestration.execution_session import RUNTIME_SCHEMA
from src.core.mixins.base import BaseMixin


class RuntimeIntentLog(BaseMixin, table=True):
    """outbox/effect ledger (主计划 §9.2)。

    每条 effect 必带 correlation_id / provider_code / idempotency_key /
    request_hash, 用于崩溃恢复、幂等复查和乱序回调归因。
    """

    __tablename__ = "runtime_intent_logs"  # pyright: ignore[reportAssignmentType]
    __schema__ = RUNTIME_SCHEMA
    __table_args__: ClassVar[tuple[object, ...]] = (
        UniqueConstraint(
            "provider_code",
            "operation_kind",
            "idempotency_key",
            name="uq_runtime_intent_log_effect_identity",
        ),
        {"schema": RUNTIME_SCHEMA},
    )

    id: int | None = Field(default=None, primary_key=True)

    # runtime 域内强 FK
    execution_session_id: int = Field(
        foreign_key=f"{RUNTIME_SCHEMA}.execution_sessions.id",
        index=True,
    )
    execution_work_item_id: int | None = Field(
        default=None,
        foreign_key=f"{RUNTIME_SCHEMA}.execution_work_items.id",
        index=True,
    )
    correlation_id: str = Field(
        foreign_key=f"{RUNTIME_SCHEMA}.execution_correlations.correlation_id",
        max_length=120,
        index=True,
    )

    # effect 元数据
    provider_code: str = Field(max_length=60, index=True)
    operation_kind: str = Field(default="plugin_intent", max_length=80)
    target_domain: str = Field(max_length=60, description="handling / device / wms_integration")
    target_action: str = Field(max_length=120)

    # 幂等
    idempotency_key: str = Field(max_length=160, index=True)
    request_hash: str = Field(max_length=128, description="immutable payload hash")

    # Plugin/Capability 执行快照；effect replay 必须按这些固定值执行，不能重新选版本或 provider。
    plugin_key: str | None = Field(default=None, max_length=100, index=True)
    plugin_contract_version: str | None = Field(default=None, max_length=60)
    capability_key: str | None = Field(default=None, max_length=120, index=True)
    capability_contract_version: str | None = Field(default=None, max_length=60)
    operation_identity: str | None = Field(default=None, max_length=160)
    creator_authority: str | None = Field(default=None, max_length=100)
    authorization_policy: str | None = Field(default=None, max_length=120)
    binding_snapshot_json: dict[str, object] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    provider_snapshot_json: dict[str, object] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    precondition_json: dict[str, object] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    fact_version: str | None = Field(default=None, max_length=120)
    payload_hash: str | None = Field(default=None, max_length=64)
    completion_mode: str | None = Field(default=None, max_length=40)

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

    # SYSTEM_CAPABILITY effect 生命周期与 typed evidence；本表是唯一 effect ledger。
    effect_status: str | None = Field(default=None, max_length=40, index=True)
    outcome_kind: str | None = Field(default=None, max_length=40)
    outcome_code: str | None = Field(default=None, max_length=120)
    outcome_json: dict[str, object] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    outcome_history_json: list[dict[str, object]] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    effect_updated_at_ms: int | None = Field(default=None, sa_type=BigInteger)
