"""RuntimeInbox (主计划 §9.2)。

入站消息统一入口: callback API 在鉴权 + normalize + 幂等校验通过后
立即写入 RuntimeInbox(status=RECEIVED) 并 ACK; 异步 worker 以
RECEIVED -> PROCESSING -> PROCESSED 为唯一成功路径。

状态机 5 态 (RuntimeInbox state machine guardrail 锁定):
  RECEIVED -> PROCESSING -> PROCESSED
  PROCESSING -> FAILED (可重试)
  FAILED -> RECEIVED (到 next_retry_at 且未超 max_retries)
  FAILED -> DEAD_LETTER (超过 max_retries 或不可重试)
  PROCESSING -> RECEIVED (lease_until 过期 crash replay)

主计划 §9.2 RuntimeInbox 处理契约:
- execution_session_id + correlation_id 可空 (callback 未解析前先 ACK)
- source_event_id + provider_code + event_type 必须唯一
- 同 key 同 hash 返回既有 ACK; 同 key 不同 hash 返回 409 + 安全审计
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, BigInteger, CheckConstraint, Index, text
from sqlmodel import Field

from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation  # noqa: F401
from src.app.runtime.orchestration.execution_session import RUNTIME_SCHEMA
from src.core.mixins.base import BaseMixin
from src.core.mixins.primary_key import SQL_COMPAT_BIGINT

PRE_CUTOVER_AUDIT_ONLY = "PRE_CUTOVER_AUDIT_ONLY"

_KIND_CHECK_SQL = (
    "kind IN ('COMMAND_RESULT', 'DEVICE_EVENT', 'EXTERNAL_HTTP', 'INTERNAL_EVENT', 'TIMER_TIMEOUT', 'REPLAY_REQUEST')"
)
_STATUS_CHECK_SQL = "status IN ('RECEIVED', 'PROCESSING', 'PROCESSED', 'FAILED', 'DEAD_LETTER')"
_CONDITIONAL_ENVELOPE_CHECK_SQL = f"""
(
    status = 'DEAD_LETTER'
    AND last_error_code = '{PRE_CUTOVER_AUDIT_ONLY}'
    AND last_error_message IS NOT NULL
    AND received_at IS NOT NULL
    AND failed_at IS NOT NULL
    AND kind IS NULL
    AND workline_id IS NULL
    AND device_id IS NULL
    AND command_id IS NULL
    AND trace_id IS NULL
    AND event_id IS NULL
    AND causation_id IS NULL
    AND payload_json IS NULL
    AND payload_hash IS NULL
    AND payload_schema_version IS NULL
    AND claim_bucket_key IS NULL
    AND processor_token IS NULL
    AND processed_at IS NULL
)
OR
(
    last_error_code IS DISTINCT FROM '{PRE_CUTOVER_AUDIT_ONLY}'
    AND kind IS NOT NULL
    AND provider_code IS NOT NULL
    AND event_type IS NOT NULL
    AND source_event_id IS NOT NULL
    AND payload_json IS NOT NULL
    AND payload_hash IS NOT NULL
    AND payload_schema_version IS NOT NULL
    AND claim_bucket_key IS NOT NULL
    AND received_at IS NOT NULL
)
"""


class RuntimeInbox(BaseMixin, table=True):
    """入站消息统一入口 (主计划 §9.2)。

    5 态状态机: RECEIVED / PROCESSING / PROCESSED / FAILED / DEAD_LETTER。
    ACK-before-processing 边界: 未解析入站事件允许暂时无 session/correlation。

    Revision A 字段扩展:
    - 路由/证据: kind, workline_id, device_id, command_id, trace_id, event_id, causation_id
    - 内容: payload_json, payload_schema_version
    - claim: claim_bucket_key, processor_token
    - 时间: received_at, processed_at, failed_at
    """

    __tablename__ = "runtime_inbox"  # pyright: ignore[reportAssignmentType]
    __schema__ = RUNTIME_SCHEMA
    __table_args__ = (
        CheckConstraint(_KIND_CHECK_SQL, name="kind_valid"),
        CheckConstraint(_STATUS_CHECK_SQL, name="status_valid"),
        CheckConstraint("max_retries >= 1", name="max_retries_positive"),
        CheckConstraint(
            _CONDITIONAL_ENVELOPE_CHECK_SQL,
            name="conditional_envelope",
        ),
        Index(
            "ux_wes_runtime_runtime_inbox_source_event",
            "provider_code",
            "event_type",
            "source_event_id",
            unique=True,
            postgresql_where=text("source_event_id IS NOT NULL"),
        ),
        Index(
            "ix_wes_runtime_runtime_inbox_status_received",
            "status",
            "received_at",
            postgresql_where=text("status = 'RECEIVED'"),
        ),
        Index(
            "ix_wes_runtime_runtime_inbox_failed_retry_at",
            "status",
            "next_retry_at",
            postgresql_where=text("status = 'FAILED'"),
        ),
        Index(
            "ix_wes_runtime_runtime_inbox_processing_lease",
            "status",
            "lease_until",
            postgresql_where=text("status = 'PROCESSING'"),
        ),
        Index(
            "ix_wes_runtime_runtime_inbox_bucket_fifo",
            "claim_bucket_key",
            "received_at",
            "id",
            postgresql_where=text("status IN ('RECEIVED', 'FAILED')"),
        ),
        {"schema": RUNTIME_SCHEMA},
    )

    id: int | None = Field(default=None, primary_key=True)

    # runtime 域内强 FK (可空: callback 未解析前先 ACK)
    execution_session_id: int | None = Field(
        default=None,
        foreign_key=f"{RUNTIME_SCHEMA}.execution_sessions.id",
        index=True,
    )
    workline_session_id: int | None = Field(
        default=None,
        foreign_key="wes_biz.workline_sessions.id",
        index=True,
        sa_type=SQL_COMPAT_BIGINT,
        description="关联 WorklineSession；与 execution_session_id 分属不同命名空间",
    )
    correlation_id: str | None = Field(
        default=None,
        foreign_key=f"{RUNTIME_SCHEMA}.execution_correlations.correlation_id",
        max_length=120,
        index=True,
        description="引用 ExecutionCorrelation.correlation_id",
    )

    # 路由/证据 (Revision A 扩展)
    kind: str | None = Field(
        default=None,
        max_length=40,
        index=True,
        description="COMMAND_RESULT / DEVICE_EVENT / EXTERNAL_HTTP / INTERNAL_EVENT / TIMER_TIMEOUT / REPLAY_REQUEST",
    )
    workline_id: int | None = Field(
        default=None, index=True, description="强 FK 到 wes_biz.work_lines.id (无 DB 约束避免循环)"
    )
    device_id: int | None = Field(default=None, index=True, description="强 FK 到 wes_biz.devices.id (无 DB 约束)")
    command_id: int | None = Field(
        default=None, index=True, description="强 FK 到 wes_biz.device_commands.id (无 DB 约束)"
    )
    trace_id: str | None = Field(default=None, max_length=120, index=True, description="端到端 trace 标识")
    event_id: str | None = Field(default=None, max_length=120, description="事件自身 id")
    causation_id: str | None = Field(default=None, max_length=120, description="上游 event_id")

    # 入站事件元数据
    provider_code: str = Field(max_length=60, index=True)
    event_type: str = Field(max_length=80, index=True)
    source_event_id: str | None = Field(default=None, max_length=160, index=True)
    payload_hash: str | None = Field(default=None, max_length=128)

    # 内容 (Revision A 扩展)
    payload_json: dict[str, Any] | None = Field(
        default=None,
        sa_type=JSON(none_as_null=True),  # type: ignore[arg-type]
        description="canonical 业务 payload, 默认最大 1 MiB (application 层校验)",
    )
    payload_schema_version: int | None = Field(default=None, description="payload JSON schema 版本")

    # 状态机
    status: str = Field(
        max_length=20,
        default="RECEIVED",
        index=True,
        description="RECEIVED / PROCESSING / PROCESSED / FAILED / DEAD_LETTER",
    )

    # claim (Revision A 扩展)
    claim_bucket_key: str | None = Field(
        default=None,
        max_length=120,
        index=True,
        description="同桶 FIFO 桶键, 优先 session/device/correlation",
    )
    processor_token: str | None = Field(
        default=None,
        max_length=80,
        description="当前 claim 持有者 token, 写终态必须匹配",
    )

    # 重试字段
    attempt_count: int = Field(default=0)
    max_retries: int = Field(default=5)
    next_retry_at: int | None = Field(default=None, sa_type=BigInteger, description="Unix timestamp ms")
    lease_until: int | None = Field(default=None, sa_type=BigInteger, description="claim lease 过期时间 (Unix ms)")

    last_error_code: str | None = Field(default=None, max_length=120)
    last_error_message: str | None = Field(default=None, max_length=500)

    # 时间 (Revision A 扩展, naive UTC from timezone.now_for_db())
    received_at: int | None = Field(default=None, sa_type=BigInteger, description="Unix timestamp ms, 写库时填充")
    processed_at: int | None = Field(
        default=None,
        sa_type=BigInteger,
        description="Unix timestamp ms, 写终态 PROCESSED 时填充",
    )
    failed_at: int | None = Field(
        default=None,
        sa_type=BigInteger,
        description="Unix timestamp ms, 写终态 FAILED/DEAD_LETTER 时填充",
    )
