"""Runtime Hold 与 NG Return 持久模型。"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime  # noqa: TC003 - SQLModel table fields need runtime type
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Literal, cast

from sqlalchemy import JSON, Column, Index, Text, UniqueConstraint, text
from sqlalchemy import Enum as SQLAEnum
from sqlmodel import Field

from src.core.mixins import DataTableMixin, EnterpriseMixin
from src.database.schema_conf import SchemaType

# NgReasonSource lives in src.workline_runtime.ng_reason, a leaf module with no
# app-layer dependencies.  Importing it normally triggers workline_runtime/__init__
# which cycles back through session_resolver → repositories → this module.
# Load the leaf module directly by file path to break the cycle.
_ng_reason_path = Path(__file__).resolve().parents[3] / "workline_runtime" / "ng_reason.py"
_ng_reason_mod = "src.workline_runtime.ng_reason"
if _ng_reason_mod not in sys.modules:
    _spec = importlib.util.spec_from_file_location(_ng_reason_mod, _ng_reason_path)
    if _spec is None or _spec.loader is None:  # pragma: no cover
        raise ImportError(f"Unable to load {_ng_reason_path}")
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[_ng_reason_mod] = _mod
    _spec.loader.exec_module(_mod)
NgReasonSource = sys.modules[_ng_reason_mod].NgReasonSource


class RuntimeHoldType(str, Enum):
    """Runtime Hold 类型。"""

    RUNTIME_RECONCILIATION = "RUNTIME_RECONCILIATION"
    SAFETY_ESTOP = "SAFETY_ESTOP"
    MANUAL_HOLD = "MANUAL_HOLD"


class RuntimeHoldStatus(str, Enum):
    """Runtime Hold 状态。"""

    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    VOIDED = "VOIDED"
    REOPENED = "REOPENED"


class MaterialDisposition(str, Enum):
    """异常恢复后的物料处置。"""

    CONTINUE = "CONTINUE"
    RETURN_TO_NG = "RETURN_TO_NG"


class NgReturnItemStatus(str, Enum):
    """NG Return 单物料状态。"""

    WAITING_REWORK = "WAITING_REWORK"
    REWORKING = "REWORKING"
    REWORKED = "REWORKED"
    CANCELLED = "CANCELLED"


class RuntimeHold(
    EnterpriseMixin,
    DataTableMixin,
    table=True,
):
    """运行时异常恢复权威事实源。"""

    __tablename__: ClassVar[Literal["runtime_holds"]] = "runtime_holds"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        UniqueConstraint("source_idempotency_key", name="uq_runtime_holds_source_idempotency_key"),
        Index("ix_runtime_holds_active_blocking", "workline_id", "status", "blocking"),
        Index(
            "ix_runtime_holds_source_refs",
            "source_kind",
            "source_inbox_id",
            "source_outbox_id",
            "source_command_id",
            "source_device_id",
        ),
        {"schema": SchemaType.BIZ.value},
    )

    hold_type: RuntimeHoldType = Field(
        index=True,
        sa_type=cast(
            "Any",
            SQLAEnum(RuntimeHoldType, native_enum=False, create_constraint=True, length=50),
        ),
        description="Runtime Hold 类型",
    )
    status: RuntimeHoldStatus = Field(
        default=RuntimeHoldStatus.OPEN,
        index=True,
        sa_type=cast(
            "Any",
            SQLAEnum(RuntimeHoldStatus, native_enum=False, create_constraint=True, length=50),
        ),
        description="Runtime Hold 状态",
    )
    blocking: bool = Field(default=True, index=True, description="是否阻断运行时派发")

    workline_id: int = Field(index=True, foreign_key="wes_biz.work_lines.id", description="关联 WorkLine.id")
    session_id: int | None = Field(
        default=None,
        index=True,
        foreign_key="wes_biz.workline_sessions.id",
        description="关联原始 WorklineSession.id",
    )
    trace_id: str | None = Field(default=None, max_length=100, index=True, description="统一 trace ID")
    plugin_key: str | None = Field(default=None, max_length=100, index=True, description="插件 key")
    contract_version: str | None = Field(default=None, max_length=50, description="插件契约版本")

    source_kind: str = Field(max_length=100, index=True, description="来源类型")
    source_reason: str = Field(max_length=200, index=True, description="来源原因")
    source_idempotency_key: str = Field(max_length=300, description="全局幂等键")
    source_inbox_id: int | None = Field(
        default=None,
        index=True,
        foreign_key="wes_biz.workline_inbox.id",
        description="来源 Inbox ID",
    )
    source_outbox_id: int | None = Field(
        default=None,
        index=True,
        foreign_key="wes_biz.workline_outbox.id",
        description="来源 Outbox ID",
    )
    source_command_id: int | None = Field(
        default=None,
        index=True,
        foreign_key="wes_biz.device_commands.id",
        description="来源设备指令 ID",
    )
    source_device_id: int | None = Field(
        default=None,
        index=True,
        foreign_key="wes_biz.devices.id",
        description="来源设备 ID",
    )

    evidence_snapshot_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON),
        description="触发 Hold 时的证据快照",
    )
    release_evidence_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON),
        description="释放 Hold 时的证据快照",
    )

    material_disposition: MaterialDisposition | None = Field(
        default=None,
        index=True,
        sa_type=cast(
            "Any",
            SQLAEnum(MaterialDisposition, native_enum=False, create_constraint=True, length=50),
        ),
        description="物料处置方式",
    )
    ng_reason_source: NgReasonSource | None = Field(
        default=None,
        index=True,
        sa_type=cast(
            "Any",
            SQLAEnum(NgReasonSource, native_enum=False, create_constraint=True, length=50),
        ),
        description="NG reason 来源",
    )
    ng_reason_code: str | None = Field(default=None, max_length=100, index=True, description="NG reason code")
    ng_reason_label: str | None = Field(default=None, max_length=200, description="NG reason 展示标签")

    resolved_at: datetime | None = Field(default=None, index=True, description="解决时间")
    resolved_by: int | None = Field(default=None, description="解决人")
    voided_at: datetime | None = Field(default=None, index=True, description="作废时间")
    voided_by: int | None = Field(default=None, description="作废人")
    reopened_from_hold_id: int | None = Field(
        default=None,
        index=True,
        foreign_key="wes_biz.runtime_holds.id",
        description="重新打开来源 RuntimeHold.id",
    )

    @property
    def is_active_blocking(self) -> bool:
        """Active blocking: OPEN/IN_PROGRESS/REOPENED 且 blocking=true。"""

        return (
            self.status
            in {
                RuntimeHoldStatus.OPEN,
                RuntimeHoldStatus.IN_PROGRESS,
                RuntimeHoldStatus.REOPENED,
            }
            and self.blocking
        )


class NgReturnItem(
    EnterpriseMixin,
    DataTableMixin,
    table=True,
):
    """进入 NG 队列的单物料/原 session 持久记录。"""

    __tablename__: ClassVar[Literal["ng_return_items"]] = "ng_return_items"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        UniqueConstraint(
            "created_from_runtime_hold_id",
            "material_identity_key",
            name="uq_ng_return_items_hold_material_identity",
        ),
        Index(
            "uq_ng_return_items_active_material_identity",
            "material_identity_key",
            unique=True,
            postgresql_where=text("status IN ('WAITING_REWORK', 'REWORKING')"),
            sqlite_where=text("status IN ('WAITING_REWORK', 'REWORKING')"),
        ),
        Index("ix_ng_return_items_source_refs", "source_workline_id", "source_session_id", "source_command_id"),
        {"schema": SchemaType.BIZ.value},
    )

    source_workline_id: int = Field(
        index=True,
        foreign_key="wes_biz.work_lines.id",
        description="来源 WorkLine.id",
    )
    source_session_id: int = Field(
        index=True,
        foreign_key="wes_biz.workline_sessions.id",
        description="来源 WorklineSession.id",
    )
    source_command_id: int | None = Field(
        default=None,
        index=True,
        foreign_key="wes_biz.device_commands.id",
        description="来源设备指令 ID",
    )
    source_event_id: str | None = Field(default=None, max_length=200, index=True, description="来源事件 ID")

    material_identity_key: str = Field(max_length=300, index=True, description="物料身份幂等 key")
    material_identity_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON),
        description="物料身份结构化快照",
    )
    physical_handoff_evidence_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON),
        description="实物流转交接证据",
    )
    disposition: MaterialDisposition = Field(
        default=MaterialDisposition.RETURN_TO_NG,
        index=True,
        sa_type=cast(
            "Any",
            SQLAEnum(MaterialDisposition, native_enum=False, create_constraint=True, length=50),
        ),
        description="物料处置方式；NG return item 固定为 RETURN_TO_NG",
    )
    ng_reason_source: NgReasonSource | None = Field(
        default=None,
        index=True,
        sa_type=cast(
            "Any",
            SQLAEnum(NgReasonSource, native_enum=False, create_constraint=True, length=50),
        ),
        description="NG reason 来源",
    )
    ng_reason_code: str | None = Field(default=None, max_length=100, index=True, description="NG reason code")
    ng_reason_label: str | None = Field(default=None, max_length=200, description="NG reason 展示标签")
    operator_note: str | None = Field(default=None, sa_column=Column(Text), description="操作员备注")

    created_from_runtime_hold_id: int | None = Field(
        default=None,
        index=True,
        foreign_key="wes_biz.runtime_holds.id",
        description="来源 RuntimeHold.id；普通扫码 NG 分流为空",
    )
    status: NgReturnItemStatus = Field(
        default=NgReturnItemStatus.WAITING_REWORK,
        index=True,
        sa_type=cast(
            "Any",
            SQLAEnum(NgReturnItemStatus, native_enum=False, create_constraint=True, length=50),
        ),
        description="NG Return 单物料状态",
    )
    confirmed_by: int | None = Field(default=None, description="服务端确认人")
    confirmed_at: datetime | None = Field(default=None, index=True, description="服务端确认时间")


__all__ = [
    "MaterialDisposition",
    "NgReasonSource",
    "NgReturnItem",
    "NgReturnItemStatus",
    "RuntimeHold",
    "RuntimeHoldStatus",
    "RuntimeHoldType",
]
