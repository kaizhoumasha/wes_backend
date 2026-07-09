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

from sqlalchemy import Index, text
from sqlmodel import Field

from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation  # noqa: F401
from src.app.runtime.orchestration.execution_session import RUNTIME_SCHEMA
from src.core.mixins.base import BaseMixin


class RuntimeInbox(BaseMixin, table=True):
    """入站消息统一入口 (主计划 §9.2)。

    5 态状态机: RECEIVED / PROCESSING / PROCESSED / FAILED / DEAD_LETTER。
    ACK-before-processing 边界: 未解析入站事件允许暂时无 session/correlation。
    """

    __tablename__ = "runtime_inbox"
    __schema__ = RUNTIME_SCHEMA
    __table_args__ = (
        Index(
            "ux_wes_runtime_runtime_inbox_source_event",
            "provider_code",
            "event_type",
            "source_event_id",
            unique=True,
            postgresql_where=text("source_event_id IS NOT NULL"),
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
    correlation_id: str | None = Field(
        default=None,
        foreign_key=f"{RUNTIME_SCHEMA}.execution_correlations.correlation_id",
        max_length=120,
        index=True,
        description="引用 ExecutionCorrelation.correlation_id",
    )

    # 入站事件元数据
    provider_code: str = Field(max_length=60, index=True)
    event_type: str = Field(max_length=80, index=True)
    source_event_id: str | None = Field(default=None, max_length=160, index=True)
    payload_hash: str | None = Field(default=None, max_length=128)

    # 状态机
    status: str = Field(
        max_length=20,
        default="RECEIVED",
        index=True,
        description="RECEIVED / PROCESSING / PROCESSED / FAILED / DEAD_LETTER",
    )

    # 重试字段
    attempt_count: int = Field(default=0)
    max_retries: int = Field(default=5)
    next_retry_at: int | None = Field(default=None, description="Unix timestamp")
    lease_until: int | None = Field(default=None, description="claim lease 过期时间")

    last_error_code: str | None = Field(default=None, max_length=120)
    last_error_message: str | None = Field(default=None, max_length=500)
