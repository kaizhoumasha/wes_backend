"""系统级发件箱模型。

所有面向外部硬件系统的异步副作用都从这里派发：

    Domain Service -> DispatchEnvelope -> SystemOutbox -> SystemOutboxEngine
        -> endpoint/device sender -> WMS/RCS/AGV/CTU -> callback

SystemOutbox 采用 at-least-once 派发语义。下游请求必须携带稳定的
dispatch_key/request_id，并由对方按该键幂等处理重复请求。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime  # noqa: TC003
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar, Literal, cast

from pydantic import model_validator
from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    LargeBinary,
    Text,
    and_,
    event,
    inspect,
    text,
)
from sqlalchemy import Enum as SQLAEnum
from sqlmodel import Field

from src.app.effect_ledger_status import SystemOutboxStatus
from src.app.sys.canonical_dispatch import CanonicalPayload
from src.app.sys.external_http_binding import FrozenExternalHttpBinding
from src.app.wms_integration.operation_registry import (
    ASYNC_EFFECT_OPERATION_IDENTITIES,
    EFFECT_OPERATION_IDENTITIES,
)
from src.core.mixins import BaseMixin, DataTableMixin
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType

if TYPE_CHECKING:
    from sqlalchemy.orm.state import InstanceState


class SystemOutboxDispatchType(str, Enum):
    """系统级派发类型。"""

    DEVICE_COMMAND = "DEVICE_COMMAND"
    EXTERNAL_HTTP = "EXTERNAL_HTTP"
    INTERNAL_SIGNAL = "INTERNAL_SIGNAL"


class SystemOutboxTargetType(str, Enum):
    """系统级派发目标类型。"""

    DEVICE = "DEVICE"
    HTTP_ENDPOINT = "HTTP_ENDPOINT"
    INTERNAL_SERVICE = "INTERNAL_SERVICE"


SYSTEM_OUTBOX_RESOURCE_WAIT_REASONS = frozenset({"DEVICE_BUSY", "DEVICE_STATUS_PRECHECK_WAIT"})
WMS_EFFECT_OPERATION_IDENTITIES = EFFECT_OPERATION_IDENTITIES
WMS_ASYNC_EFFECT_OPERATION_IDENTITIES = ASYNC_EFFECT_OPERATION_IDENTITIES


def _validate_wms_effect_idempotency(outbox: Any) -> None:
    if getattr(outbox, "operation_identity", None) not in WMS_EFFECT_OPERATION_IDENTITIES:
        return
    idempotency_key = getattr(outbox, "idempotency_key", None)
    if (
        not isinstance(idempotency_key, str)
        or not idempotency_key.strip()
        or "\n" in idempotency_key
        or "\r" in idempotency_key
    ):
        raise ValueError("WMS EFFECT requires a non-empty single-line idempotency_key")


def is_system_outbox_resource_wait(outbox: Any) -> bool:
    """仅把带完整受控元数据的设备等待识别为受控资源等待投影。"""

    status = getattr(outbox, "status", None)
    dispatch_type = getattr(outbox, "dispatch_type", None)
    status_value = status.value if isinstance(status, Enum) else status
    dispatch_type_value = dispatch_type.value if isinstance(dispatch_type, Enum) else dispatch_type
    return (
        status_value == SystemOutboxStatus.RETRY_WAIT.value
        and dispatch_type_value == SystemOutboxDispatchType.DEVICE_COMMAND.value
        and getattr(outbox, "blocked_reason", None) in SYSTEM_OUTBOX_RESOURCE_WAIT_REASONS
        and getattr(outbox, "blocked_at", None) is not None
        and getattr(outbox, "finished_at", None) is None
    )


def system_outbox_resource_wait_clause(columns: Any) -> Any:
    """生成与内存谓词等价的 SQL 过滤条件。"""

    return and_(
        columns.status == SystemOutboxStatus.RETRY_WAIT,
        columns.dispatch_type == SystemOutboxDispatchType.DEVICE_COMMAND,
        columns.blocked_reason.in_(SYSTEM_OUTBOX_RESOURCE_WAIT_REASONS),
        columns.blocked_at.is_not(None),
        columns.finished_at.is_(None),
    )


class OperationCompletionPolicy(str, Enum):
    """Operation 完成确认策略。"""

    RESOURCE_PROJECTION_REQUIRED = "RESOURCE_PROJECTION_REQUIRED"


@dataclass(frozen=True)
class DispatchEnvelope:
    """领域 gateway 交给 SystemOutbox 的统一派发包络。"""

    dispatch_key: str
    dispatch_type: SystemOutboxDispatchType
    target_type: SystemOutboxTargetType
    target_code: str
    provider_profile_identity: str
    operation_identity: str
    payload_json: dict[str, Any]
    operation_domain: str
    idempotency_key: str | None = None
    frozen_binding: FrozenExternalHttpBinding | None = None
    canonical_payload_bytes: bytes | None = None
    payload_hash: str | None = None
    operation_key: str | None = None
    workline_id: int | None = None
    session_id: int | None = None
    device_id: int | None = None
    trace_id: str | None = None

    def __post_init__(self) -> None:
        _validate_wms_effect_idempotency(self)
        if self.dispatch_type != SystemOutboxDispatchType.EXTERNAL_HTTP:
            if self.frozen_binding is not None:
                raise ValueError("non-EXTERNAL_HTTP DispatchEnvelope must not carry frozen binding")
            return
        if self.frozen_binding is None:
            raise ValueError("EXTERNAL_HTTP DispatchEnvelope requires frozen binding")
        if (
            self.frozen_binding.provider_profile_identity != self.provider_profile_identity
            or self.frozen_binding.operation_identity != self.operation_identity
            or self.frozen_binding.target_snapshot.code != self.target_code
        ):
            raise ValueError("EXTERNAL_HTTP DispatchEnvelope identity differs from frozen binding")
        if self.canonical_payload_bytes is None:
            raise ValueError("EXTERNAL_HTTP DispatchEnvelope requires canonical_payload_bytes")
        if self.payload_hash is None:
            raise ValueError("EXTERNAL_HTTP DispatchEnvelope requires payload_hash")
        canonical = CanonicalPayload.from_persisted(
            canonical_payload_bytes=self.canonical_payload_bytes,
            payload_hash=self.payload_hash,
        )
        canonical.validate_projection(self.payload_json)


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
    device_id: int | None = Field(
        default=None,
        index=True,
        foreign_key="wes_biz.devices.id",
        description="可选关联 Device.id，用于物理设备 FIFO",
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
    blocked_device_id: int | None = Field(default=None, index=True, description="阻断相关设备 ID")
    blocked_workline_id: int | None = Field(default=None, index=True, description="阻断相关工作线 ID")
    blocked_reason: str | None = Field(default=None, max_length=100, description="阻断原因")
    blocked_at: datetime | None = Field(default=None, description="资源等待起始时间")
    last_blocked_check_at: datetime | None = Field(default=None, description="最近一次资源等待探测时间")
    blocked_check_count: int = Field(default=0, ge=0, description="资源等待探测次数")
    blocked_detail_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
        description="资源等待诊断摘要",
    )


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
            "AND auth_scheme = 'HMAC_SHA256' "
            "AND credential_reference IS NOT NULL)",
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
        Index("ix_system_outbox_blocked_release", "blocked_reason", "blocked_device_id", "blocked_workline_id"),
        Index(
            "ix_system_outbox_blocked_device_head_probe",
            "operation_domain",
            "status",
            "dispatch_type",
            "blocked_reason",
            "last_blocked_check_at",
            "blocked_device_id",
            "target_code",
            "created_at",
            postgresql_where=text(
                "status = 'RETRY_WAIT' AND dispatch_type = 'DEVICE_COMMAND' "
                "AND blocked_reason IN ('DEVICE_BUSY', 'DEVICE_STATUS_PRECHECK_WAIT') "
                "AND blocked_at IS NOT NULL AND finished_at IS NULL"
            ),
            sqlite_where=text(
                "status = 'RETRY_WAIT' AND dispatch_type = 'DEVICE_COMMAND' "
                "AND blocked_reason IN ('DEVICE_BUSY', 'DEVICE_STATUS_PRECHECK_WAIT') "
                "AND blocked_at IS NOT NULL AND finished_at IS NULL"
            ),
        ),
        Index("ix_system_outbox_retention", "status", "finished_at"),
        Index(
            "ix_system_outbox_device_fifo",
            "dispatch_type",
            "device_id",
            "target_code",
            "status",
            "created_at",
            postgresql_where=text("dispatch_type = 'DEVICE_COMMAND'"),
            sqlite_where=text("dispatch_type = 'DEVICE_COMMAND'"),
        ),
        {"schema": SchemaType.BIZ.value},
    )

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        _validate_system_outbox_canonical_payload(self)


class SystemOutboxCreate(ModelFactory(SystemOutboxBase).for_create()):
    """系统级发件箱创建 Schema。"""

    @model_validator(mode="after")
    def validate_external_http_canonical_payload(self) -> SystemOutboxCreate:
        _validate_system_outbox_canonical_payload(self)
        return self


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
    credential_reference: ClassVar[str]
    lease_owner_token: ClassVar[str]
    lease_expires_at: ClassVar[datetime]


def _validate_system_outbox_canonical_payload(outbox: Any) -> None:
    _validate_wms_effect_idempotency(outbox)
    dispatch_type = getattr(outbox, "dispatch_type", None)
    dispatch_type_value = dispatch_type.value if isinstance(dispatch_type, Enum) else dispatch_type
    if dispatch_type_value != SystemOutboxDispatchType.EXTERNAL_HTTP.value:
        frozen_fields = (
            "provider_profile_hash",
            "binding_revision",
            "target_snapshot_json",
            "target_snapshot_hash",
            "auth_scheme",
            "credential_reference",
        )
        if any(getattr(outbox, field_name, None) is not None for field_name in frozen_fields):
            raise ValueError("non-EXTERNAL_HTTP SystemOutbox must not carry frozen target and credential binding")
        return
    canonical_payload_bytes = getattr(outbox, "canonical_payload_bytes", None)
    payload_hash = getattr(outbox, "payload_hash", None)
    if canonical_payload_bytes is None:
        raise ValueError("EXTERNAL_HTTP SystemOutbox requires canonical_payload_bytes")
    if payload_hash is None:
        raise ValueError("EXTERNAL_HTTP SystemOutbox requires payload_hash")
    canonical = CanonicalPayload.from_persisted(
        canonical_payload_bytes=canonical_payload_bytes,
        payload_hash=payload_hash,
    )
    payload_projection = getattr(outbox, "payload_json", None)
    if not isinstance(payload_projection, dict):
        raise TypeError("EXTERNAL_HTTP SystemOutbox requires payload_json object")
    canonical.validate_projection(payload_projection)
    try:
        _ = FrozenExternalHttpBinding.from_persisted(
            provider_profile_identity=getattr(outbox, "provider_profile_identity", None),
            provider_profile_hash=getattr(outbox, "provider_profile_hash", None),
            operation_identity=getattr(outbox, "operation_identity", None),
            binding_revision=getattr(outbox, "binding_revision", None),
            target_code=getattr(outbox, "target_code", None),
            target_snapshot_json=getattr(outbox, "target_snapshot_json", None),
            target_snapshot_hash=getattr(outbox, "target_snapshot_hash", None),
            auth_scheme=getattr(outbox, "auth_scheme", None),
            credential_reference=getattr(outbox, "credential_reference", None),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("EXTERNAL_HTTP SystemOutbox requires frozen target and credential binding") from exc


@event.listens_for(SystemOutbox, "before_update")
def _prevent_external_http_payload_update(_mapper: Any, _connection: Any, outbox: SystemOutbox) -> None:
    """调度 identity 与 EXTERNAL_HTTP canonical payload 一经持久化均不可改写。"""

    state = cast("InstanceState[SystemOutbox]", inspect(outbox))
    scheduling_fields = (
        "provider_profile_identity",
        "provider_profile_hash",
        "operation_identity",
        "binding_revision",
        "target_code",
        "target_snapshot_json",
        "target_snapshot_hash",
        "auth_scheme",
        "credential_reference",
        "idempotency_key",
    )
    if any(state.attrs[field_name].history.has_changes() for field_name in scheduling_fields):
        raise ValueError("SystemOutbox scheduling identity persisted fields are immutable")
    dispatch_type = getattr(outbox, "dispatch_type", None)
    dispatch_type_value = dispatch_type.value if isinstance(dispatch_type, Enum) else dispatch_type
    if dispatch_type_value != SystemOutboxDispatchType.EXTERNAL_HTTP.value:
        return
    immutable_fields = ("payload_json", "canonical_payload_bytes", "payload_hash")
    if any(state.attrs[field_name].history.has_changes() for field_name in immutable_fields):
        raise ValueError("EXTERNAL_HTTP SystemOutbox canonical payload persisted fields are immutable")


__all__ = [
    "SYSTEM_OUTBOX_RESOURCE_WAIT_REASONS",
    "WMS_ASYNC_EFFECT_OPERATION_IDENTITIES",
    "WMS_EFFECT_OPERATION_IDENTITIES",
    "DispatchEnvelope",
    "OperationCompletionPolicy",
    "SystemOutbox",
    "SystemOutboxBase",
    "SystemOutboxCreate",
    "SystemOutboxDispatchType",
    "SystemOutboxStatus",
    "SystemOutboxTargetType",
    "SystemOutboxUpdate",
    "is_system_outbox_resource_wait",
    "system_outbox_resource_wait_clause",
]
