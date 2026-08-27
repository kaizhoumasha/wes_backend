"""EVENT 被未闭合 DeviceCommand 阻塞时的持久因果记录。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from enum import Enum
from typing import Any, ClassVar, cast

from sqlalchemy import CheckConstraint, Index, text
from sqlalchemy import Enum as SQLAEnum
from sqlmodel import Field

from src.app.device.models.command import CommandStatus, DeviceCommand  # noqa: F401
from src.app.execution.models.inbound_evidence import InboundEvidence  # noqa: F401
from src.core.mixins import DataTableMixin, EnterpriseMixin
from src.core.mixins.primary_key import SQL_COMPAT_BIGINT
from src.database.schema_conf import SchemaType


class DeviceEventCommandBlockStatus(str, Enum):
    """阻塞记录生命周期。"""

    BLOCKED = "BLOCKED"
    REQUEUED = "REQUEUED"


class DeviceEventCommandBlock(EnterpriseMixin, DataTableMixin, table=True):
    """冻结 EVENT 与占用设备槽位命令之间的因果快照。"""

    __tablename__: ClassVar[str] = "device_event_command_blocks"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        CheckConstraint(
            "status IN ('BLOCKED', 'REQUEUED')",
            name="device_event_command_block_status_valid",
        ),
        CheckConstraint(
            "reason_code = 'DEVICE_HAS_ACTIVE_COMMAND'",
            name="device_event_command_block_reason_valid",
        ),
        CheckConstraint(
            "blocking_command_status IN ('PENDING', 'DISPATCHING', 'ACKNOWLEDGED', 'RECONCILING')",
            name="device_event_command_block_command_status_valid",
        ),
        CheckConstraint(
            "((status = 'BLOCKED' AND requeued_at IS NULL) OR (status = 'REQUEUED' AND requeued_at IS NOT NULL))",
            name="device_event_command_block_status_time_complete",
        ),
        Index(
            "ux_device_event_command_blocks_open_evidence",
            "evidence_id",
            unique=True,
            postgresql_where=text("status = 'BLOCKED'"),
            sqlite_where=text("status = 'BLOCKED'"),
        ),
        Index(
            "ix_device_event_command_blocks_evidence_history",
            "evidence_id",
            "blocked_at",
            "id",
        ),
        {"schema": SchemaType.BIZ.value},
    )

    evidence_id: int = Field(
        foreign_key="wes_biz.inbound_evidences.id",
        sa_type=SQL_COMPAT_BIGINT,
    )
    source_event_id: str = Field(min_length=1, max_length=300)
    device_code: str = Field(min_length=1, max_length=100)
    blocking_command_id: int = Field(
        foreign_key="wes_biz.device_commands.id",
        sa_type=SQL_COMPAT_BIGINT,
    )
    blocking_command_code: str = Field(min_length=1, max_length=100)
    blocking_command_status: CommandStatus = Field(
        sa_type=cast("Any", SQLAEnum(CommandStatus, native_enum=False, create_constraint=False, length=20))
    )
    blocking_reconciliation_reason: str | None = Field(default=None, max_length=120)
    reason_code: str = Field(default="DEVICE_HAS_ACTIVE_COMMAND", min_length=1, max_length=120)
    status: DeviceEventCommandBlockStatus = Field(
        default=DeviceEventCommandBlockStatus.BLOCKED,
        sa_type=cast(
            "Any",
            SQLAEnum(DeviceEventCommandBlockStatus, native_enum=False, create_constraint=False, length=20),
        ),
    )
    blocked_at: datetime
    requeued_at: datetime | None = Field(default=None)


__all__ = ["DeviceEventCommandBlock", "DeviceEventCommandBlockStatus"]
