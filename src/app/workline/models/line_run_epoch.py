"""WorkLine 连续可信运行代际与设备合同绑定。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from enum import Enum
from typing import Any, ClassVar, cast

from sqlalchemy import CheckConstraint, Index, UniqueConstraint, text
from sqlalchemy import Enum as SQLAEnum
from sqlmodel import Field

from src.core.mixins import DataTableMixin, EnterpriseMixin
from src.database.schema_conf import SchemaType


class LineRunEpochStatus(str, Enum):
    """运行代际只允许活动或关闭。"""

    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


class LineRunEpoch(EnterpriseMixin, DataTableMixin, table=True):
    """一条 WorkLine 在冻结拓扑、配置和设备合同下的运行代际。"""

    __tablename__: ClassVar[str] = "line_run_epochs"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE', 'CLOSED')", name="line_run_epoch_status_valid"),
        UniqueConstraint("epoch_code", name="ux_line_run_epochs_epoch_code"),
        Index(
            "ux_line_run_epochs_active_workline",
            "workline_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
            sqlite_where=text("status = 'ACTIVE'"),
        ),
        {"schema": SchemaType.BIZ.value},
    )

    epoch_code: str = Field(min_length=1, max_length=100)
    workline_id: int = Field(foreign_key="wes_biz.work_lines.id", index=True)
    plugin_key: str = Field(min_length=1, max_length=100)
    plugin_version: str = Field(min_length=1, max_length=50)
    flow_mode: str = Field(min_length=1, max_length=100)
    topology_digest: str = Field(min_length=64, max_length=64)
    configuration_digest: str = Field(min_length=64, max_length=64)
    status: LineRunEpochStatus = Field(
        default=LineRunEpochStatus.ACTIVE,
        sa_type=cast(
            "Any",
            SQLAEnum(LineRunEpochStatus, native_enum=False, create_constraint=False, length=20),
        ),
    )
    started_at: datetime
    closed_at: datetime | None = Field(default=None)


class LineRunEpochDeviceBinding(EnterpriseMixin, DataTableMixin, table=True):
    """Epoch 内不可改写的设备与统一合同绑定。"""

    __tablename__: ClassVar[str] = "line_run_epoch_device_bindings"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        UniqueConstraint(
            "line_run_epoch_id",
            "device_code",
            name="ux_line_run_epoch_device_bindings_epoch_device_code",
        ),
        UniqueConstraint(
            "line_run_epoch_id",
            "device_id",
            name="ux_line_run_epoch_device_bindings_epoch_device_id",
        ),
        UniqueConstraint(
            "line_run_epoch_id",
            "device_role",
            name="ux_line_run_epoch_device_bindings_epoch_device_role",
        ),
        CheckConstraint("status_max_age_ms > 0", name="line_run_epoch_binding_status_age_positive"),
        CheckConstraint("command_timeout_ms > 0", name="line_run_epoch_binding_timeout_positive"),
        {"schema": SchemaType.BIZ.value},
    )

    line_run_epoch_id: int = Field(foreign_key="wes_biz.line_run_epochs.id", index=True)
    device_id: int = Field(foreign_key="wes_biz.devices.id", index=True)
    device_code: str = Field(min_length=1, max_length=100)
    device_role: str = Field(min_length=1, max_length=50)
    contract_key: str = Field(min_length=1, max_length=100)
    contract_version: str = Field(min_length=1, max_length=50)
    status_max_age_ms: int = Field(gt=0)
    command_timeout_ms: int = Field(gt=0)

    def identity_tuple(self) -> tuple[object, ...]:
        """返回决定绑定语义的全部不可变字段。"""

        return (
            self.line_run_epoch_id,
            self.device_id,
            self.device_code,
            self.device_role,
            self.contract_key,
            self.contract_version,
            self.status_max_age_ms,
            self.command_timeout_ms,
        )


class LineRunEpochPositionBinding(EnterpriseMixin, DataTableMixin, table=True):
    """Epoch 内冻结的逻辑位置拓扑；不承载动态占用状态。"""

    __tablename__: ClassVar[str] = "line_run_epoch_position_bindings"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        UniqueConstraint(
            "line_run_epoch_id",
            "position_role",
            name="ux_line_run_epoch_position_bindings_epoch_role",
        ),
        UniqueConstraint(
            "line_run_epoch_id",
            "location_id",
            name="ux_line_run_epoch_position_bindings_epoch_location",
        ),
        {"schema": SchemaType.BIZ.value},
    )

    line_run_epoch_id: int = Field(foreign_key="wes_biz.line_run_epochs.id", index=True)
    position_role: str = Field(min_length=1, max_length=50)
    location_id: str = Field(min_length=1, max_length=120)
    location_type: str = Field(min_length=1, max_length=50)

    def identity_tuple(self) -> tuple[object, ...]:
        return self.line_run_epoch_id, self.position_role, self.location_id, self.location_type


__all__ = ["LineRunEpoch", "LineRunEpochDeviceBinding", "LineRunEpochPositionBinding", "LineRunEpochStatus"]
