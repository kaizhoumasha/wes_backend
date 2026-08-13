"""DeviceCommand 对插件和统一 ECS Adapter 暴露的稳定类型。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime  # noqa: TC003
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from src.app.device.models.command import CommandStatus, DeviceCommandParamValue  # noqa: TC001

_WIRE_TOKEN_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"  # noqa: S105  # nosec B105 - token regex


@dataclass(frozen=True, slots=True)
class DeviceCommandRequest:
    """后续业务插件创建命令时使用的唯一应用端口请求。"""

    device_code: str
    line_run_epoch_id: int
    execution_ref_type: str
    execution_ref_id: str
    contract_key: str
    contract_version: str
    task_type: str
    params: dict[str, DeviceCommandParamValue]
    deadline_at: datetime
    trace_id: str | None = None


@dataclass(frozen=True, slots=True)
class DeviceCommandHandle:
    command_code: str
    status: CommandStatus


@dataclass(frozen=True, slots=True)
class DeviceCommandOutcome:
    command_code: str
    status: CommandStatus
    failure_code: str | None
    completed_at: datetime | None
    version: int


class EcsDeviceMode(str, Enum):
    AUTO = "AUTO"
    MANUAL = "MANUAL"
    MAINTENANCE = "MAINTENANCE"
    UNKNOWN = "UNKNOWN"


class EcsDeviceState(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    ERROR = "ERROR"
    OFFLINE = "OFFLINE"
    UNKNOWN = "UNKNOWN"


class EcsErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str
    supplier_raw_code: str | None = None
    supplier_raw_data: dict[str, Any] | None = None

    @model_validator(mode="after")
    def reject_explicit_null_optionals(self) -> EcsErrorDetail:
        for field in ("supplier_raw_code", "supplier_raw_data"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} 不可显式为 null")
        return self


class EcsDeviceStatus(BaseModel):
    """统一状态端点的完整响应快照。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    device_code: str = Field(min_length=1, max_length=100, pattern=_WIRE_TOKEN_PATTERN)
    contract_key: str = Field(min_length=1, max_length=100, pattern=_WIRE_TOKEN_PATTERN)
    contract_version: str = Field(min_length=1, max_length=40, pattern=_WIRE_TOKEN_PATTERN)
    mode: EcsDeviceMode
    status: EcsDeviceState
    current_command_code: str | None = Field(min_length=1, max_length=160, pattern=_WIRE_TOKEN_PATTERN)
    error_detail: EcsErrorDetail | None
    timestamp: StrictInt = Field(gt=0, le=2**63 - 1)

    @model_validator(mode="after")
    def validate_error_detail(self) -> EcsDeviceStatus:
        if self.status is EcsDeviceState.ERROR and self.error_detail is None:
            raise ValueError("ERROR 状态必须包含 error_detail")
        if self.status is not EcsDeviceState.ERROR and self.error_detail is not None:
            raise ValueError("非 ERROR 状态不得包含 error_detail")
        return self


class EcsSubmitDisposition(str, Enum):
    """同步发送后 WES 能确定的接纳事实。"""

    ACKNOWLEDGED = "ACKNOWLEDGED"
    RETRYABLE_NOT_ACCEPTED = "RETRYABLE_NOT_ACCEPTED"
    CONTRACT_REJECTED = "CONTRACT_REJECTED"
    RECONCILING = "RECONCILING"


@dataclass(frozen=True, slots=True)
class EcsSubmitResult:
    disposition: EcsSubmitDisposition
    code: int | None = None
    message: str | None = None
    trace_id: str | None = None
    retry_after_seconds: int | None = None


class EcsCommandResultValue(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class EcsCommandResult(BaseModel):
    """ECS 上报的命令物理终态公共包络。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    command_code: str = Field(min_length=1, max_length=160, pattern=_WIRE_TOKEN_PATTERN)
    device_code: str = Field(min_length=1, max_length=100, pattern=_WIRE_TOKEN_PATTERN)
    contract_key: str = Field(min_length=1, max_length=100, pattern=_WIRE_TOKEN_PATTERN)
    contract_version: str = Field(min_length=1, max_length=40, pattern=_WIRE_TOKEN_PATTERN)
    result: EcsCommandResultValue
    finish_time: StrictInt = Field(gt=0, le=2**63 - 1)
    source_event_id: str = Field(min_length=1, max_length=160, pattern=_WIRE_TOKEN_PATTERN)
    data: dict[str, Any]
    error_detail: EcsErrorDetail | None
    trace_id: str | None = Field(default=None, min_length=1, max_length=120, pattern=_WIRE_TOKEN_PATTERN)

    @model_validator(mode="after")
    def validate_error_detail(self) -> EcsCommandResult:
        if "trace_id" in self.model_fields_set and self.trace_id is None:
            raise ValueError("trace_id 不可显式为 null")
        if self.result is EcsCommandResultValue.FAILED and self.error_detail is None:
            raise ValueError("FAILED 结果必须包含 error_detail")
        if self.result is EcsCommandResultValue.SUCCESS and self.error_detail is not None:
            raise ValueError("SUCCESS 结果的 error_detail 必须为 null")
        return self


class EcsDeviceEvent(BaseModel):
    """ECS 上报的设备合同事件公共包络。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    device_code: str = Field(min_length=1, max_length=100, pattern=_WIRE_TOKEN_PATTERN)
    contract_key: str = Field(min_length=1, max_length=100, pattern=_WIRE_TOKEN_PATTERN)
    contract_version: str = Field(min_length=1, max_length=40, pattern=_WIRE_TOKEN_PATTERN)
    event_type: str = Field(min_length=1, max_length=160, pattern=_WIRE_TOKEN_PATTERN)
    timestamp: StrictInt = Field(gt=0, le=2**63 - 1)
    source_event_id: str = Field(min_length=1, max_length=160, pattern=_WIRE_TOKEN_PATTERN)
    data: dict[str, Any]
    trace_id: str | None = Field(default=None, min_length=1, max_length=120, pattern=_WIRE_TOKEN_PATTERN)

    @model_validator(mode="after")
    def reject_explicit_null_trace(self) -> EcsDeviceEvent:
        if "trace_id" in self.model_fields_set and self.trace_id is None:
            raise ValueError("trace_id 不可显式为 null")
        return self


@dataclass(frozen=True, slots=True)
class DeviceEvidenceReceipt:
    evidence_id: int
    source_event_id: str
    duplicate: bool
    trace_id: str | None


__all__ = [
    "DeviceCommandHandle",
    "DeviceCommandOutcome",
    "DeviceCommandRequest",
    "DeviceEvidenceReceipt",
    "EcsCommandResult",
    "EcsCommandResultValue",
    "EcsDeviceEvent",
    "EcsDeviceMode",
    "EcsDeviceState",
    "EcsDeviceStatus",
    "EcsSubmitDisposition",
    "EcsSubmitResult",
]
