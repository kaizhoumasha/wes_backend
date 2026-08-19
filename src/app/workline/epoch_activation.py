"""LineRunEpoch 创建前即可形成的稳定激活输入。"""

from dataclasses import dataclass

from src.app.device.endpoint import validate_device_endpoint_base_url


@dataclass(frozen=True, slots=True)
class LineRunEpochDeviceBindingInput:
    """设备数据库身份与冻结派发不变量。"""

    device_id: int
    device_code: str
    device_role: str
    endpoint_base_url: str
    contract_key: str
    contract_version: str
    status_max_age_ms: int
    command_timeout_ms: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "endpoint_base_url",
            validate_device_endpoint_base_url(self.endpoint_base_url),
        )


@dataclass(frozen=True, slots=True)
class LineRunEpochPositionBindingInput:
    """不依赖父 Epoch ID 的静态位置拓扑。"""

    position_role: str
    location_id: str
    location_type: str


@dataclass(frozen=True, slots=True)
class WorkLineEpochActivationPlan:
    """业务 builder 翻译后交给通用 START 的完整激活计划。"""

    plugin_key: str
    plugin_version: str
    flow_mode: str
    configuration_snapshot: dict[str, object]
    device_bindings: tuple[LineRunEpochDeviceBindingInput, ...]
    position_bindings: tuple[LineRunEpochPositionBindingInput, ...]


__all__ = [
    "LineRunEpochDeviceBindingInput",
    "LineRunEpochPositionBindingInput",
    "WorkLineEpochActivationPlan",
]
