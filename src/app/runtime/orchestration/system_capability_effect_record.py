"""SYSTEM_CAPABILITY EFFECT 的幂等状态与 typed outcome evidence。"""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import JSON, BigInteger, Column, UniqueConstraint
from sqlmodel import Field

from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation  # noqa: F401
from src.app.runtime.orchestration.execution_session import RUNTIME_SCHEMA
from src.core.mixins.base import BaseMixin


class SystemCapabilityEffectRecord(BaseMixin, table=True):
    """Runtime-owned EFFECT 状态；BUSINESS_REJECT 不会被误判为幂等成功。"""

    __tablename__ = "system_capability_effect_records"  # pyright: ignore[reportAssignmentType]
    __schema__ = RUNTIME_SCHEMA
    __table_args__: ClassVar[tuple[object, ...]] = (
        UniqueConstraint(
            "provider_code",
            "operation_kind",
            "idempotency_key",
            name="uq_system_capability_effect_identity",
        ),
        {"schema": RUNTIME_SCHEMA},
    )

    id: int | None = Field(default=None, primary_key=True)
    execution_session_id: int = Field(foreign_key=f"{RUNTIME_SCHEMA}.execution_sessions.id", index=True)
    execution_work_item_id: int = Field(foreign_key=f"{RUNTIME_SCHEMA}.execution_work_items.id", index=True)
    correlation_id: str = Field(
        foreign_key=f"{RUNTIME_SCHEMA}.execution_correlations.correlation_id", max_length=120, index=True
    )
    provider_code: str = Field(max_length=60)
    operation_kind: str = Field(max_length=80)
    idempotency_key: str = Field(max_length=160, index=True)
    request_hash: str = Field(max_length=128)
    plugin_key: str = Field(max_length=100, index=True)
    plugin_contract_version: str = Field(max_length=60)
    binding_id: int
    binding_version: int
    capability_key: str = Field(max_length=120, index=True)
    capability_contract_version: str = Field(max_length=60)
    operation_identity: str = Field(max_length=160)
    status: str = Field(default="PROVISIONAL", max_length=40, index=True)
    attempt_count: int = Field(default=1, ge=1)
    outcome_kind: str | None = Field(default=None, max_length=40)
    outcome_code: str | None = Field(default=None, max_length=120)
    outcome_json: dict[str, object] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    outcome_history_json: list[dict[str, object]] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    updated_at_ms: int = Field(sa_type=BigInteger)


__all__ = ["SystemCapabilityEffectRecord"]
