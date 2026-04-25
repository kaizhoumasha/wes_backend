"""运行时解析后的 Device / Workline 配置模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from src.workline_runtime.run_mode import normalize_run_mode


class ResolvedDeviceRuntimeConfig(BaseModel):
    """插件和诊断链路使用的设备运行时快照。"""

    device_id: int | None = None
    device_code: str | None = None
    device_name: str | None = None
    device_role: str | None = None
    role_index: int | None = None
    upstream_device_id: int | None = None
    workline_id: int | None = None
    plugin_key: str | None = None
    contract_version: str | None = None
    protocol: str | None = None
    host: str | None = None
    port: int | None = None
    timeout_ms: int | None = None
    callback_path: str | None = None
    maintenance_mode: bool = False
    capabilities: dict[str, Any] = Field(default_factory=dict)
    diagnostic_profile: dict[str, Any] = Field(default_factory=dict)

    @property
    def communication_profile(self) -> dict[str, Any]:
        return {
            "protocol": self.protocol,
            "host": self.host,
            "port": self.port,
            "timeout_ms": self.timeout_ms,
            "callback_path": self.callback_path,
        }


class ResolvedWorklineRuntimeConfig(BaseModel):
    """插件和诊断链路使用的工作线运行时快照。"""

    workline_id: int | None = None
    line_code: str | None = None
    line_name: str | None = None
    line_type: str | None = None
    run_mode: str = "AUTO"
    plugin_key: str | None = None
    contract_version: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    runtime_config: dict[str, Any] = Field(default_factory=dict)
    owner_team: str | None = None
    support_contact: str | None = None
    diagnostic_profile: dict[str, Any] = Field(default_factory=dict)


class ResolvedExecutionContext(BaseModel):
    """传递给插件和诊断构建器的统一运行时上下文。"""

    workline: ResolvedWorklineRuntimeConfig | None = None
    devices_by_role: dict[str, list[ResolvedDeviceRuntimeConfig]] = Field(default_factory=dict)


def _dict_value(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _enum_str(value: Any) -> Any:
    return getattr(value, "value", value)


def resolve_device_runtime_config(device: Any, *, workline: Any | None = None) -> ResolvedDeviceRuntimeConfig:
    """从 Device 实体解析运行时配置。"""

    return ResolvedDeviceRuntimeConfig(
        device_id=getattr(device, "id", None),
        device_code=getattr(device, "device_code", None),
        device_name=getattr(device, "device_name", None),
        device_role=getattr(device, "device_role", None),
        role_index=getattr(device, "role_index", None),
        upstream_device_id=getattr(device, "upstream_device_id", None),
        workline_id=getattr(device, "work_line_id", None) or getattr(workline, "id", None),
        plugin_key=getattr(workline, "plugin_key", None),
        contract_version=getattr(workline, "contract_version", None),
        protocol=_enum_str(getattr(device, "protocol", None)),
        host=getattr(device, "host", None),
        port=getattr(device, "port", None),
        timeout_ms=getattr(device, "timeout", None),
        callback_path=getattr(device, "callback_path", None),
        maintenance_mode=bool(getattr(device, "maintenance_mode", False)),
        capabilities=_dict_value(getattr(device, "capabilities_json", None)),
        diagnostic_profile=_dict_value(getattr(device, "diagnostic_profile", None)),
    )


def resolve_workline_runtime_config(workline: Any | None) -> ResolvedWorklineRuntimeConfig | None:
    """从 WorkLine 实体解析运行时配置。"""

    if workline is None:
        return None

    return ResolvedWorklineRuntimeConfig(
        workline_id=getattr(workline, "id", None),
        line_code=getattr(workline, "line_code", None),
        line_name=getattr(workline, "line_name", None),
        line_type=_enum_str(getattr(workline, "line_type", None)),
        run_mode=normalize_run_mode(getattr(workline, "run_mode", None)),
        plugin_key=getattr(workline, "plugin_key", None),
        contract_version=getattr(workline, "contract_version", None),
        config=_dict_value(getattr(workline, "config", None)),
        runtime_config=_dict_value(getattr(workline, "runtime_config_json", None)),
        owner_team=getattr(workline, "owner_team", None),
        support_contact=getattr(workline, "support_contact", None),
        diagnostic_profile=_dict_value(getattr(workline, "diagnostic_profile", None)),
    )


def resolve_execution_context(
    workline: Any | None,
    devices_by_role: dict[str, list[Any]],
) -> ResolvedExecutionContext:
    """解析统一运行时上下文。"""

    resolved_workline = resolve_workline_runtime_config(workline)
    resolved_devices: dict[str, list[ResolvedDeviceRuntimeConfig]] = {}
    for role, devices in devices_by_role.items():
        resolved_devices[role] = [resolve_device_runtime_config(device, workline=workline) for device in devices]

    return ResolvedExecutionContext(workline=resolved_workline, devices_by_role=resolved_devices)


__all__ = [
    "ResolvedDeviceRuntimeConfig",
    "ResolvedExecutionContext",
    "ResolvedWorklineRuntimeConfig",
    "resolve_device_runtime_config",
    "resolve_execution_context",
    "resolve_workline_runtime_config",
]
