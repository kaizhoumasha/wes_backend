"""后续 DDL 前保留的 SystemOutbox schema identity。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from enum import Enum
from typing import Any, ClassVar, Literal, cast

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    LargeBinary,
    Text,
)
from sqlalchemy import Enum as SQLAEnum
from sqlmodel import Field

from src.app.effect_ledger_status import SystemOutboxStatus
from src.core.mixins import BaseMixin, DataTableMixin
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType


class SystemOutboxDispatchType(str, Enum):
    """系统级派发类型。"""

    EXTERNAL_HTTP = "EXTERNAL_HTTP"
    INTERNAL_SIGNAL = "INTERNAL_SIGNAL"


class SystemOutboxTargetType(str, Enum):
    """系统级派发目标类型。"""

    HTTP_ENDPOINT = "HTTP_ENDPOINT"
    INTERNAL_SERVICE = "INTERNAL_SERVICE"


class SystemOutboxBase(BaseMixin):
    """系统级发件箱基础字段。"""

    session_id: int | None = Field(
        default=None,
        index=True,
        foreign_key="wes_biz.workline_sessions.id",
        description="可选关联 WorklineSession.id",
    )
    workline_id: int | None = Field(
        default=None,
        index=True,
        foreign_key="wes_biz.work_lines.id",
        description="可选关联 WorkLine.id",
    )
    operation_domain: str = Field(default="WORKLINE", max_length=50, index=True, description="操作域")
    operation_key: str | None = Field(default=None, max_length=240, index=True, description="操作幂等键")
    dispatch_type: SystemOutboxDispatchType = Field(
        index=True,
        sa_type=cast(
            "Any",
            SQLAEnum(SystemOutboxDispatchType, native_enum=False, create_constraint=True, length=50),
        ),
        description="派发类型",
    )
    dispatch_key: str = Field(min_length=1, max_length=240, index=True, description="派发幂等键")
    idempotency_key: str | None = Field(
        default=None,
        max_length=160,
        description="下游 EFFECT 请求幂等键；创建后不可变，通用/历史 Outbox 可空",
    )
    target_type: SystemOutboxTargetType = Field(
        index=True,
        sa_type=cast(
            "Any",
            SQLAEnum(SystemOutboxTargetType, native_enum=False, create_constraint=True, length=50),
        ),
        description="目标类型",
    )
    target_code: str = Field(min_length=1, max_length=240, index=True, description="目标逻辑编码")
    provider_profile_identity: str = Field(
        min_length=1,
        max_length=240,
        description="不可变 Provider profile 调度身份",
    )
    operation_identity: str = Field(
        min_length=1,
        max_length=240,
        description="不可变 operation 调度身份",
    )
    provider_profile_hash: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        description="EXTERNAL_HTTP author-time Provider profile SHA-256",
    )
    binding_revision: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        description="EXTERNAL_HTTP operation binding revision SHA-256",
    )
    target_snapshot_json: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
        description="EXTERNAL_HTTP 完整非秘密 endpoint/target 快照",
    )
    target_snapshot_hash: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        description="target_snapshot_json 的 SHA-256",
    )
    auth_scheme: str | None = Field(
        default=None,
        max_length=50,
        description="冻结的封闭出站认证 scheme",
    )
    network_trust_mode: str | None = Field(
        default=None,
        max_length=50,
        description="冻结的网络信任事实",
    )
    credential_reference: str | None = Field(
        default=None,
        max_length=240,
        description="冻结的版本化 credential reference；不包含 secret material",
    )
    payload_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON),
        description="仅用于查询的派发负载投影，不得作为 EXTERNAL_HTTP 发送输入",
    )
    canonical_payload_bytes: bytes | None = Field(
        default=None,
        sa_column=Column(
            LargeBinary,
            nullable=True,
            comment="EXTERNAL_HTTP 唯一权威的冻结 canonical 请求体",
        ),
        description="EXTERNAL_HTTP 唯一权威的冻结 canonical 请求体",
    )
    payload_hash: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        description="canonical_payload_bytes 的 SHA-256",
    )
    status: SystemOutboxStatus = Field(
        default=SystemOutboxStatus.NEW,
        index=True,
        sa_type=cast("Any", SQLAEnum(SystemOutboxStatus, native_enum=False, create_constraint=True, length=50)),
        description="派发状态",
    )
    attempt_count: int = Field(default=0, ge=0, description="尝试次数")
    next_retry_at: datetime | None = Field(default=None, index=True, description="下次重试时间")
    lease_owner_token: str | None = Field(
        default=None,
        max_length=240,
        description="当前或最近一次派发 lease owner token",
    )
    lease_expires_at: datetime | None = Field(default=None, description="当前 DISPATCHING lease 截止时间")
    dispatch_started_at: datetime | None = Field(
        default=None,
        description="当前 attempt 越过本地物理发送边界的时间；用于区分安全回队与送达歧义",
    )
    last_error: str | None = Field(default=None, sa_column=Column(Text), description="最后错误")
    sent_at: datetime | None = Field(default=None, description="发送时间")
    finished_at: datetime | None = Field(default=None, index=True, description="结束时间")
    trace_id: str | None = Field(default=None, max_length=100, index=True, description="Trace ID")
    blocked_by_reconciliation_session_id: int | None = Field(
        default=None,
        index=True,
        description="阻断该 outbox 的 runtime reconciliation owner session ID",
    )
    blocked_by_runtime_hold_id: int | None = Field(
        default=None,
        sa_column=Column(
            BigInteger,
            ForeignKey(
                "wes_biz.runtime_holds.id",
                name="fk_system_outbox_blocked_by_runtime_hold_id",
                use_alter=True,
            ),
            nullable=True,
            index=True,
        ),
        description="阻断该 outbox 的 RuntimeHold.id",
    )
    blocked_workline_id: int | None = Field(default=None, index=True, description="阻断相关工作线 ID")
    blocked_reason: str | None = Field(default=None, max_length=100, description="阻断原因")


class SystemOutbox(SystemOutboxBase, DataTableMixin, table=True):
    """系统级发件箱。"""

    __tablename__: ClassVar[Literal["system_outbox"]] = "system_outbox"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        CheckConstraint(
            "dispatch_type != 'EXTERNAL_HTTP' OR "
            "(canonical_payload_bytes IS NOT NULL AND length(canonical_payload_bytes) > 0 "
            "AND payload_hash IS NOT NULL AND length(payload_hash) = 64)",
            name="ck_system_outbox_external_http_canonical_payload",
        ),
        CheckConstraint(
            "dispatch_type != 'EXTERNAL_HTTP' OR "
            "(provider_profile_hash IS NOT NULL AND length(provider_profile_hash) = 64 "
            "AND binding_revision IS NOT NULL AND length(binding_revision) = 64 "
            "AND target_snapshot_json IS NOT NULL "
            "AND target_snapshot_hash IS NOT NULL AND length(target_snapshot_hash) = 64 "
            "AND ((auth_scheme = 'NONE' AND network_trust_mode = 'isolated_lan' "
            "AND credential_reference IS NULL) "
            "OR (auth_scheme = 'HMAC_SHA256' "
            "AND network_trust_mode IN ('isolated_lan', 'authenticated_network') "
            "AND credential_reference IS NOT NULL)))",
            name="ck_system_outbox_external_http_frozen_binding",
        ),
        CheckConstraint(
            "(status != 'DISPATCHING' OR (lease_owner_token IS NOT NULL AND lease_expires_at IS NOT NULL)) "
            "AND (status = 'DISPATCHING' OR lease_expires_at IS NULL)",
            name="ck_system_outbox_dispatch_lease_shape",
        ),
        Index("ux_system_outbox_dispatch_key", "dispatch_key", unique=True),
        Index("ix_system_outbox_status_retry_created", "status", "next_retry_at", "created_at"),
        Index(
            "ix_system_outbox_dispatch_bucket_claim",
            "provider_profile_identity",
            "operation_identity",
            "status",
            "next_retry_at",
            "created_at",
        ),
        Index(
            "ix_system_outbox_active_lease",
            "provider_profile_identity",
            "operation_identity",
            "status",
            "lease_expires_at",
        ),
        Index("ix_system_outbox_domain_operation", "operation_domain", "operation_key"),
        Index("ix_system_outbox_context_status", "workline_id", "session_id", "status"),
        Index("ix_system_outbox_blocked_release", "blocked_reason", "blocked_workline_id"),
        Index("ix_system_outbox_retention", "status", "finished_at"),
        {"schema": SchemaType.BIZ.value},
    )


class SystemOutboxCreate(ModelFactory(SystemOutboxBase).for_create()):
    """系统级发件箱创建 Schema。"""


class SystemOutboxUpdate(
    ModelFactory(SystemOutboxBase).for_update(
        exclude=(
            "dispatch_key",
            "idempotency_key",
            "target_code",
            "provider_profile_identity",
            "provider_profile_hash",
            "operation_identity",
            "binding_revision",
            "target_snapshot_json",
            "target_snapshot_hash",
            "auth_scheme",
            "network_trust_mode",
            "credential_reference",
            "payload_json",
            "canonical_payload_bytes",
            "payload_hash",
            "lease_owner_token",
            "lease_expires_at",
        )
    )
):
    """系统级发件箱更新 Schema。"""

    dispatch_key: ClassVar[str]
    idempotency_key: ClassVar[str]
    target_code: ClassVar[str]
    payload_json: ClassVar[dict[str, Any]]
    canonical_payload_bytes: ClassVar[bytes]
    payload_hash: ClassVar[str]
    provider_profile_identity: ClassVar[str]
    provider_profile_hash: ClassVar[str]
    operation_identity: ClassVar[str]
    binding_revision: ClassVar[str]
    target_snapshot_json: ClassVar[dict[str, Any]]
    target_snapshot_hash: ClassVar[str]
    auth_scheme: ClassVar[str]
    network_trust_mode: ClassVar[str]
    credential_reference: ClassVar[str]
    lease_owner_token: ClassVar[str]
    lease_expires_at: ClassVar[datetime]


__all__ = [
    "SystemOutbox",
    "SystemOutboxBase",
    "SystemOutboxCreate",
    "SystemOutboxDispatchType",
    "SystemOutboxStatus",
    "SystemOutboxTargetType",
    "SystemOutboxUpdate",
]
