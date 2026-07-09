"""IdempotencyKey runtime idempotency ledger。

独立表, 复合主键 (provider_code, operation_kind, idempotency_key)。
通过 execution_correlation_id 引用 ExecutionCorrelation。

Runtime idempotency minimum contract:
- 表落地 (schema-only)
- WES 内部 key 命名约束: WES-{OPERATION_KIND}-{HASH}
- RuntimeIntentLog outbound effect 最小同 key 不同 hash 拒绝
- 完整 409 安全审计由 runtime audit matrix 覆盖
"""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import BigInteger
from sqlmodel import Field

from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation  # noqa: F401
from src.app.runtime.orchestration.execution_session import RUNTIME_SCHEMA
from src.core.mixins.base import BaseMixin


class IdempotencyKey(BaseMixin, table=True):
    """幂等键表 (主计划 §5.4)。"""

    __tablename__ = "idempotency_keys"  # pyright: ignore[reportAssignmentType]
    __schema__ = RUNTIME_SCHEMA
    __table_args__: ClassVar[dict[str, str]] = {"schema": RUNTIME_SCHEMA}

    # 复合主键 (provider_code, operation_kind, idempotency_key)
    provider_code: str = Field(max_length=60, primary_key=True)
    operation_kind: str = Field(max_length=80, primary_key=True)
    idempotency_key: str = Field(max_length=160, primary_key=True)

    # 引用 ExecutionCorrelation
    execution_correlation_id: str = Field(
        foreign_key=f"{RUNTIME_SCHEMA}.execution_correlations.correlation_id",
        max_length=120,
        index=True,
        description="引用 ExecutionCorrelation.correlation_id",
    )

    request_hash: str = Field(max_length=128, description="immutable payload hash")
    business_owner_key: str | None = Field(default=None, max_length=160)

    # TTL 30 天 (主计划 §5.4)
    created_at: int = Field(index=True, sa_type=BigInteger, description="Unix timestamp ms")
