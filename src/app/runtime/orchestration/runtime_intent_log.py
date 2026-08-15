"""RuntimeIntentLog capability EFFECT 语义账本。

本表只保存不可变请求、幂等身份和 capability 语义状态；transport 状态、
attempt/retry 与 transport error 分别归 SystemOutbox 和 WorklineDispatchAttempt。
有 transport 的 EFFECT 通过两侧唯一 ``dispatch_key`` 关联，不建立跨 schema FK。
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - SQLModel 运行时需要解析字段类型
from enum import Enum
from typing import Any, ClassVar, cast

from sqlalchemy import JSON, BigInteger, Column, Index, UniqueConstraint
from sqlalchemy import Enum as SQLAEnum
from sqlmodel import Field

from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation  # noqa: F401
from src.app.runtime.orchestration.execution_session import RUNTIME_SCHEMA
from src.core.mixins.base import BaseMixin


class RuntimeIntentStatus(str, Enum):
    """Capability EFFECT 语义状态。

    PROPOSED -> ACCEPTED -> COMPLETED
       |            |----> REJECTED
       |            `----> UNKNOWN -> RECONCILING -> COMPLETED / REJECTED
       `-----------------> TECHNICAL_FAILED（仅确认未发送且重试耗尽）
    """

    PROPOSED = "PROPOSED"
    ACCEPTED = "ACCEPTED"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    TECHNICAL_FAILED = "TECHNICAL_FAILED"
    UNKNOWN = "UNKNOWN"
    RECONCILING = "RECONCILING"


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
        Index("ux_runtime_intent_log_dispatch_key", "dispatch_key", unique=True),
        Index(
            "ix_runtime_intent_log_effect_status_check_after",
            "effect_status",
            "status_check_after",
        ),
        {"schema": RUNTIME_SCHEMA},
    )

    id: int | None = Field(default=None, primary_key=True)

    # runtime 域内强 FK
    execution_session_id: int | None = Field(
        default=None,
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
    operation_kind: str = Field(max_length=80)
    target_domain: str = Field(max_length=60, description="handling / device / wms_integration")
    target_action: str = Field(max_length=120)

    # 幂等
    idempotency_key: str = Field(max_length=160, index=True)
    request_hash: str = Field(max_length=128, description="immutable payload hash")
    dispatch_key: str = Field(
        min_length=1,
        max_length=240,
        description="与 SystemOutbox 1:1 共享的不可变派发键",
    )

    # Capability 执行快照；effect replay 必须按这些固定值执行，不能重新选版本或 provider。
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

    # reducer 是语义状态唯一写入口；transport terminal 不等于业务完成。
    effect_status: RuntimeIntentStatus = Field(
        default=RuntimeIntentStatus.PROPOSED,
        index=True,
        sa_type=cast(
            "Any",
            SQLAEnum(
                RuntimeIntentStatus,
                name="runtime_intent_status",
                native_enum=False,
                create_constraint=True,
                length=40,
            ),
        ),
    )
    outcome_kind: str | None = Field(default=None, max_length=40)
    outcome_code: str | None = Field(default=None, max_length=120)
    outcome_json: dict[str, object] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    outcome_history_json: list[dict[str, object]] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    effect_updated_at_ms: int | None = Field(default=None, sa_type=BigInteger)

    # WMS EFFECT 语义确认账本；transport terminal 与普通 retry 字段不得复用。
    status_check_started_at: datetime | None = Field(default=None)
    status_check_after: datetime | None = Field(default=None)
    status_check_count: int = Field(default=0, ge=0)
    status_resubmit_count: int = Field(default=0, ge=0)
    status_source_version: int | None = Field(default=None, ge=0, sa_type=BigInteger)
    status_check_lease_token: str | None = Field(default=None, max_length=64)
    status_check_lease_until: datetime | None = Field(default=None)
    status_binding_snapshot_json: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    status_binding_snapshot_hash: str | None = Field(default=None, max_length=64)


__all__ = ["RuntimeIntentLog", "RuntimeIntentStatus"]
