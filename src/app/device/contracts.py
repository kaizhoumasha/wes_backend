"""DeviceCommand 对插件和统一 ECS Adapter 暴露的稳定类型。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime  # noqa: TC003
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from src.app.device.models.command import CommandStatus, DeviceCommandParamValue  # noqa: TC001

_WIRE_TOKEN_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"  # noqa: S105  # nosec B105 - token regex


@dataclass(frozen=True, slots=True)
class DeviceCommandRequest:
    """后续业务插件创建命令时使用的唯一应用端口请求。"""

    device_code: str
    line_run_epoch_id: int
    execution_ref_type: str
    execution_ref_id: str
    material_execution_id: int | None
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


@dataclass(frozen=True, slots=True)
class DeviceCommandCallbackSnapshot:
    result: str
    data: dict[str, Any]
    error_detail: dict[str, Any] | None
    source_event_id: str
    received_at: datetime
    apply_status: str


@dataclass(frozen=True, slots=True)
class ManualDebugDeviceCommandSnapshot:
    command_code: str
    client_request_id: str
    device_code: str
    endpoint_base_url: str
    contract_key: str
    contract_version: str
    command_timeout_ms: int
    task_type: str
    params: dict[str, DeviceCommandParamValue]
    trace_id: str | None
    status: CommandStatus
    attempt_count: int
    ack_received_at: datetime | None
    completed_at: datetime | None
    failure_code: str | None
    reconciliation_reason: str | None
    callback: DeviceCommandCallbackSnapshot | None


class EcsDeviceMode(str, Enum):
    AUTO = "AUTO"
    MANUAL = "MANUAL"
    MAINTENANCE = "MAINTENANCE"
    UNKNOWN = "UNKNOWN"


class EcsDeviceState(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    ERROR = "ERROR"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"
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


class EcsDeviceInfo(BaseModel):
    """ECS 返回的设备静态描述。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    device_code: str = Field(min_length=1, max_length=100, pattern=_WIRE_TOKEN_PATTERN)
    device_name: str | None = Field(min_length=1, max_length=200)
    device_type: str | None = Field(min_length=1, max_length=100, pattern=_WIRE_TOKEN_PATTERN)
    role: str | None = Field(min_length=1, max_length=100, pattern=_WIRE_TOKEN_PATTERN)
    supported_commands: tuple[str, ...] | None
    supported_events: tuple[str, ...] | None


class EcsDeviceRuntimeState(BaseModel):
    """ECS 返回的设备运行状态。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    device_code: str = Field(min_length=1, max_length=100, pattern=_WIRE_TOKEN_PATTERN)
    mode: EcsDeviceMode
    status: EcsDeviceState
    is_online: StrictBool
    current_command_code: str | None = Field(min_length=1, max_length=160, pattern=_WIRE_TOKEN_PATTERN)
    scenario: str | None = Field(min_length=1, max_length=100, pattern=_WIRE_TOKEN_PATTERN)
    updated_at: StrictInt = Field(gt=0, le=2**63 - 1)


class EcsDeviceStatus(BaseModel):
    """ECS 批量状态响应中的单设备条目。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    device: EcsDeviceInfo
    state: EcsDeviceRuntimeState

    @model_validator(mode="after")
    def validate_device_identity(self) -> EcsDeviceStatus:
        if self.device.device_code != self.state.device_code:
            raise ValueError("device 与 state 的 device_code 必须一致")
        return self


class EcsDeviceStatusResponse(BaseModel):
    """ECS 状态端点的批量响应。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    devices: tuple[EcsDeviceStatus, ...] = Field(min_length=1)


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


class EcsCallbackErrorDetail(BaseModel):
    """白皮书 1.1 callback 的外部错误结构。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1, max_length=100, pattern=_WIRE_TOKEN_PATTERN)
    msg: str = Field(min_length=1, max_length=500)


class EcsCommandResultReport(BaseModel):
    """ECS → WES 的白皮书 1.1 结果回传包络。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    command_code: str = Field(min_length=1, max_length=160, pattern=_WIRE_TOKEN_PATTERN)
    device_code: str = Field(min_length=1, max_length=100, pattern=_WIRE_TOKEN_PATTERN)
    result: EcsCommandResultValue
    finish_time: StrictInt = Field(gt=0, le=2**63 - 1)
    data: dict[str, Any] = Field(default_factory=dict)
    error_detail: EcsCallbackErrorDetail | None = None

    @model_validator(mode="after")
    def validate_error_detail(self) -> EcsCommandResultReport:
        if self.result is EcsCommandResultValue.FAILED and self.error_detail is None:
            raise ValueError("FAILED 结果必须包含 error_detail")
        if self.result is EcsCommandResultValue.SUCCESS and self.error_detail is not None:
            raise ValueError("SUCCESS 结果的 error_detail 必须为 null")
        return self


class EcsDeviceEventReport(BaseModel):
    """ECS → WES 的白皮书 1.1 事件上报包络。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    device_code: str = Field(min_length=1, max_length=100, pattern=_WIRE_TOKEN_PATTERN)
    event_type: str = Field(min_length=1, max_length=160, pattern=_WIRE_TOKEN_PATTERN)
    timestamp: StrictInt = Field(gt=0, le=2**63 - 1)
    data: dict[str, Any] = Field(default_factory=dict)


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
    "DeviceCommandCallbackSnapshot",
    "DeviceCommandHandle",
    "DeviceCommandOutcome",
    "DeviceCommandRequest",
    "DeviceEvidenceReceipt",
    "EcsCallbackErrorDetail",
    "EcsCommandResult",
    "EcsCommandResultReport",
    "EcsCommandResultValue",
    "EcsDeviceEvent",
    "EcsDeviceEventReport",
    "EcsDeviceInfo",
    "EcsDeviceMode",
    "EcsDeviceRuntimeState",
    "EcsDeviceState",
    "EcsDeviceStatus",
    "EcsDeviceStatusResponse",
    "EcsSubmitDisposition",
    "EcsSubmitResult",
    "ManualDebugDeviceCommandSnapshot",
]
