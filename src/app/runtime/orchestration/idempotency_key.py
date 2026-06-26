"""IdempotencyKey (Phase 1 H5, 主计划 §5.4)。

独立表, 复合主键 (provider_code, operation_kind, idempotency_key)。
通过 execution_correlation_id 引用 ExecutionCorrelation。

H5 (Phase 1 最小版本):
- 表落地 (schema-only)
- WES 内部 key 命名约束: WES-{OPERATION_KIND}-{HASH}
- RuntimeIntentLog outbound effect 最小同 key 不同 hash 拒绝
- 完整 409 安全审计留 Phase 3 ENG-009
"""

from __future__ import annotations

from sqlmodel import Field, SQLModel


class IdempotencyKey(SQLModel, table=True):
    """幂等键表 (主计划 §5.4)。"""

    __tablename__ = "idempotency_keys"
    __schema__ = "wes_runtime"

    # 复合主键 (provider_code, operation_kind, idempotency_key)
    provider_code: str = Field(max_length=60, primary_key=True)
    operation_kind: str = Field(max_length=80, primary_key=True)
    idempotency_key: str = Field(max_length=160, primary_key=True)

    # 引用 ExecutionCorrelation
    execution_correlation_id: str = Field(
        max_length=120,
        index=True,
        description="引用 ExecutionCorrelation.correlation_id",
    )

    request_hash: str = Field(max_length=128, description="immutable payload hash")
    business_owner_key: str | None = Field(default=None, max_length=160)

    # TTL 30 天 (主计划 §5.4)
    created_at: int = Field(index=True, description="Unix timestamp ms")
