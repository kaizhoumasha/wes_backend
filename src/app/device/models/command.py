"""DeviceCommand 通用可靠生命周期模型。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from enum import Enum
from typing import Any, ClassVar, cast

from pydantic import field_validator
from sqlalchemy import JSON, CheckConstraint, Column, Index, UniqueConstraint, text
from sqlalchemy import Enum as SQLAEnum
from sqlmodel import Field
from sqlmodel._compat import SQLModelConfig

from src.core.mixins import BaseMixin, DataTableMixin, EnterpriseMixin
from src.database.schema_conf import SchemaType
from src.utils.timezone import timezone


class CommandStatus(str, Enum):
    """DeviceCommand 的完整且封闭的可靠生命周期。"""

    PENDING = "PENDING"
    DISPATCHING = "DISPATCHING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RECONCILING = "RECONCILING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"


class InvalidCommandTransitionError(ValueError):
    """请求了不允许的命令状态迁移。"""


type DeviceCommandParamScalar = str | int | float | bool | None
type DeviceCommandParamValue = (
    DeviceCommandParamScalar | list[DeviceCommandParamValue] | dict[str, DeviceCommandParamValue]
)
type _NormalizedCommandParamValue = DeviceCommandParamValue

_FORBIDDEN_PARAM_KEYS = {
    "axis",
    "coordinate",
    "coordinates",
    "joint",
    "joint_angle",
    "plc",
    "plc_address",
    "plc_point",
    "safety_loop",
    "speed",
    "velocity",
    "x_coord",
    "y_coord",
}


def _normalize_param(value: Any, *, path: str) -> _NormalizedCommandParamValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list):
        return [_normalize_param(item, path=f"{path}[]") for item in value]
    if isinstance(value, dict):
        normalized: dict[str, _NormalizedCommandParamValue] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str):
                raise TypeError(f"{path} 参数 key 必须是字符串")
            key = raw_key.strip()
            if not key:
                raise ValueError(f"{path} 参数 key 不能为空")
            if key.lower() in _FORBIDDEN_PARAM_KEYS:
                raise ValueError(f"params 禁止包含硬件控制字段: {key}")
            normalized[key] = _normalize_param(raw_value, path=f"{path}.{key}")
        return normalized
    raise ValueError(f"{path} 参数值必须是 JSON 标量、数组或对象")


class DeviceCommandRequestData(BaseMixin):
    """DeviceCommand 持久化前的业务无关请求字段。"""

    model_config = SQLModelConfig(from_attributes=True, extra="forbid")

    device_code: str = Field(min_length=1, max_length=100)
    line_run_epoch_id: int
    execution_ref_type: str = Field(min_length=1, max_length=50)
    execution_ref_id: str = Field(min_length=1, max_length=120)
    contract_key: str = Field(min_length=1, max_length=100)
    contract_version: str = Field(min_length=1, max_length=50)
    task_type: str = Field(min_length=1, max_length=100)
    params: dict[str, DeviceCommandParamValue] = Field(default_factory=dict)
    deadline_at: datetime
    trace_id: str | None = Field(default=None, max_length=100)

    @field_validator(
        "device_code",
        "execution_ref_type",
        "execution_ref_id",
        "contract_key",
        "contract_version",
        "task_type",
        mode="before",
    )
    @classmethod
    def normalize_required_text(cls, value: Any) -> str:
        if isinstance(value, Enum):
            value = value.value
        if isinstance(value, str) and (normalized := value.strip()):
            return normalized
        raise ValueError("字段必须是非空字符串")

    @field_validator("params", mode="before")
    @classmethod
    def validate_params(cls, value: Any) -> dict[str, DeviceCommandParamValue]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise TypeError("params 必须是对象")
        return cast("dict[str, DeviceCommandParamValue]", _normalize_param(value, path="params"))


_UNCLOSED_STATUSES = frozenset(
    {
        CommandStatus.PENDING,
        CommandStatus.DISPATCHING,
        CommandStatus.ACKNOWLEDGED,
        CommandStatus.RECONCILING,
    }
)
_ALLOWED_TRANSITIONS: dict[CommandStatus, frozenset[CommandStatus]] = {
    CommandStatus.PENDING: frozenset({CommandStatus.DISPATCHING, CommandStatus.FAILED, CommandStatus.TIMED_OUT}),
    CommandStatus.DISPATCHING: frozenset(
        {
            CommandStatus.PENDING,
            CommandStatus.ACKNOWLEDGED,
            CommandStatus.SUCCEEDED,
            CommandStatus.FAILED,
            CommandStatus.RECONCILING,
            CommandStatus.TIMED_OUT,
        }
    ),
    CommandStatus.ACKNOWLEDGED: frozenset({CommandStatus.SUCCEEDED, CommandStatus.FAILED, CommandStatus.RECONCILING}),
    CommandStatus.RECONCILING: frozenset({CommandStatus.SUCCEEDED, CommandStatus.FAILED}),
    CommandStatus.SUCCEEDED: frozenset(),
    CommandStatus.FAILED: frozenset(),
    CommandStatus.TIMED_OUT: frozenset(),
}


class DeviceCommand(DeviceCommandRequestData, EnterpriseMixin, DataTableMixin, table=True):
    """一次设备物理动作的唯一可靠命令记录。"""

    __tablename__: ClassVar[str] = "device_commands"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'DISPATCHING', 'ACKNOWLEDGED', 'RECONCILING', 'SUCCEEDED', 'FAILED', 'TIMED_OUT')",
            name="device_command_status_valid",
        ),
        CheckConstraint("attempt_count >= 0", name="device_command_attempt_count_nonnegative"),
        UniqueConstraint("command_code", name="ux_device_commands_command_code"),
        Index(
            "ux_device_commands_unclosed_device",
            "device_code",
            unique=True,
            postgresql_where=text("status IN ('PENDING', 'DISPATCHING', 'ACKNOWLEDGED', 'RECONCILING')"),
            sqlite_where=text("status IN ('PENDING', 'DISPATCHING', 'ACKNOWLEDGED', 'RECONCILING')"),
        ),
        Index("ix_device_commands_dispatch_claim", "status", "next_attempt_at", "id"),
        UniqueConstraint(
            "line_run_epoch_id",
            "device_code",
            "execution_ref_type",
            "execution_ref_id",
            name="ux_device_commands_execution_identity",
        ),
        {"schema": SchemaType.BIZ.value},
    )

    command_code: str = Field(min_length=1, max_length=100)
    device_binding_id: int = Field(foreign_key="wes_biz.line_run_epoch_device_bindings.id", index=True)
    payload_digest: str = Field(min_length=64, max_length=64)
    status: CommandStatus = Field(
        default=CommandStatus.PENDING,
        sa_type=cast("Any", SQLAEnum(CommandStatus, native_enum=False, create_constraint=False, length=20)),
        index=True,
    )
    params: dict[str, DeviceCommandParamValue] = Field(default_factory=dict, sa_column=Column(JSON))

    attempt_count: int = Field(default=0, ge=0)
    next_attempt_at: datetime | None = Field(default=None)
    claim_token: str | None = Field(default=None, max_length=80)
    claimed_at: datetime | None = Field(default=None)
    claim_expires_at: datetime | None = Field(default=None)

    ack_received_at: datetime | None = Field(default=None)
    completed_at: datetime | None = Field(default=None)
    result_evidence_id: int | None = Field(
        default=None,
        foreign_key="wes_biz.device_evidences.id",
        index=True,
    )
    failure_code: str | None = Field(default=None, max_length=120)
    reconciliation_reason: str | None = Field(default=None, max_length=120)
    outcome_published_at: datetime | None = Field(default=None)

    @property
    def occupies_device_slot(self) -> bool:
        """未闭合命令始终占用设备，包括结果不明的对账态。"""

        return CommandStatus(self.status) in _UNCLOSED_STATUSES

    def transition_to(self, target: CommandStatus) -> None:
        """执行单调状态迁移；物理终态只由上层匹配 evidence 后调用。"""

        current = CommandStatus(self.status)
        if target not in _ALLOWED_TRANSITIONS[current]:
            raise InvalidCommandTransitionError(f"不允许 DeviceCommand 从 {current.value} 迁移到 {target.value}")
        self.status = target
        if target in {CommandStatus.SUCCEEDED, CommandStatus.FAILED, CommandStatus.TIMED_OUT}:
            self.completed_at = timezone.now_for_db()


__all__ = [
    "CommandStatus",
    "DeviceCommand",
    "DeviceCommandParamValue",
    "DeviceCommandRequestData",
    "InvalidCommandTransitionError",
]
