"""粗分机业务配置到通用 Epoch 激活计划的翻译。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Protocol, cast

from src.app.device.contracts import EcsDeviceMode, EcsDeviceState
from src.app.device.repositories.device_repository import device_repository
from src.app.workline.epoch_activation import (
    LineRunEpochDeviceBindingInput,
    LineRunEpochPositionBindingInput,
    WorkLineEpochActivationPlan,
)
from src.app.workline.services.workline_start_service import WorkLineStartConfigurationError
from src.utils.timezone import timezone

from rough_sorter.activation import POSITION_ROLES, RoughSorterConfigurationError, parse_activation_configuration
from rough_sorter.handlers._guards import ROLE_CONTRACTS
from rough_sorter.plugin import PLUGIN_KEY, PLUGIN_VERSION

_DEFAULT_DEVICE_REPOSITORY = cast("DeviceRepositoryPort", device_repository)

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from src.app.device.contracts import EcsDeviceStatus


_REQUIRED_COMMANDS = {
    "MEASUREMENT_DEVICE": "PICK_AND_PUT",
    "TRANSFER_DEVICE": "MOVE_FORWARD",
    "PLACEMENT_DEVICE": "PICK_AND_PUT",
}


class DeviceRepositoryPort(Protocol):
    async def get_by_work_line_id_for_update(self, db: Any, work_line_id: int) -> list[Any]: ...


class EcsAdapterPort(Protocol):
    async def fetch_statuses(self) -> tuple[EcsDeviceStatus, ...]: ...


class AdapterProviderPort(Protocol):
    async def get_adapter(self, endpoint_base_url: str) -> EcsAdapterPort: ...


class RoughSorterStartPlanBuilder:
    """锁定一次 Device 集合，在内存中形成完整基础激活计划。"""

    def __init__(
        self,
        *,
        device_repository: DeviceRepositoryPort = _DEFAULT_DEVICE_REPOSITORY,
        adapter_provider: AdapterProviderPort | None = None,
        clock: Callable[[], datetime] = timezone.now_for_db,
    ) -> None:
        self._devices = device_repository
        self._adapter_provider = adapter_provider
        self._clock = clock

    @staticmethod
    def compatibility_incompatibility_reasons(_workline: Any, devices: tuple[Any, ...]) -> tuple[str, ...]:
        """返回插件候选项的设备兼容原因；不解析其他插件的配置。"""
        reasons: list[str] = []
        for role in ROLE_CONTRACTS:
            matches = [device for device in devices if device.device_role == role]
            if not matches:
                reasons.append(f"DEVICE_ROLE_MISSING:{role}")
                continue
            if len(matches) != 1:
                reasons.append(f"DEVICE_ROLE_NOT_SINGLETON:{role}")
                continue
            device = matches[0]
            if not bool(device.is_active):
                reasons.append(f"DEVICE_INACTIVE:{device.device_code}")
            if not device.endpoint_base_url:
                reasons.append(f"DEVICE_ENDPOINT_MISSING:{device.device_code}")
        return tuple(reasons)

    @classmethod
    def configuration_incompatibility_reasons(cls, workline: Any, devices: tuple[Any, ...]) -> tuple[str, ...]:
        """返回当前已选粗分插件的配置与设备检查结果。"""

        reasons: list[str] = []
        config = getattr(workline, "config", None)
        try:
            if not isinstance(config, Mapping):
                raise RoughSorterConfigurationError("WorkLine.config 必须是对象")
            _ = parse_activation_configuration(cast("Mapping[str, object]", config).get("rough_sorter"))
        except RoughSorterConfigurationError:
            reasons.append("CONFIGURATION_INVALID")
        reasons.extend(cls.compatibility_incompatibility_reasons(workline, devices))
        return tuple(reasons)

    async def build(self, db: Any, workline: Any) -> WorkLineEpochActivationPlan:
        config = getattr(workline, "config", None)
        try:
            if not isinstance(config, Mapping):
                raise RoughSorterConfigurationError("WorkLine.config 必须是对象")
            parsed = parse_activation_configuration(cast("Mapping[str, object]", config).get("rough_sorter"))
        except RoughSorterConfigurationError as exc:
            raise WorkLineStartConfigurationError(str(exc)) from exc

        devices = await self._devices.get_by_work_line_id_for_update(db, workline.id)
        device_bindings: list[LineRunEpochDeviceBindingInput] = []
        for role, contract_key in ROLE_CONTRACTS.items():
            matches = [device for device in devices if device.device_role == role]
            if len(matches) != 1:
                raise WorkLineStartConfigurationError(f"粗分机 WorkLine 必须且只能包含一个 {role}")
            device = matches[0]
            if not bool(device.is_active):
                raise WorkLineStartConfigurationError(f"{role} 未静态启用")
            if device.id is None or not device.endpoint_base_url:
                raise WorkLineStartConfigurationError(f"{role} 缺少可冻结的 Device Endpoint")
            contract = parsed.device_contracts[role]
            try:
                device_bindings.append(
                    LineRunEpochDeviceBindingInput(
                        device_id=device.id,
                        device_code=device.device_code,
                        device_role=role,
                        endpoint_base_url=device.endpoint_base_url,
                        contract_key=contract_key,
                        contract_version="1.0",
                        status_max_age_ms=contract.status_max_age_ms,
                        command_timeout_ms=contract.command_timeout_ms,
                    )
                )
            except ValueError as exc:
                raise WorkLineStartConfigurationError(f"{role} Device Endpoint 非法") from exc

        await self._validate_live_devices(tuple(device_bindings))

        return WorkLineEpochActivationPlan(
            plugin_key=PLUGIN_KEY,
            plugin_version=PLUGIN_VERSION,
            flow_mode="ROUGH_SORT_INBOUND",
            configuration_snapshot=parsed.snapshot,
            device_bindings=tuple(device_bindings),
            position_bindings=tuple(
                LineRunEpochPositionBindingInput(
                    position_role=role,
                    location_id=parsed.position_bindings[role],
                    location_type=role,
                )
                for role in POSITION_ROLES
            ),
        )

    async def _validate_live_devices(self, bindings: tuple[LineRunEpochDeviceBindingInput, ...]) -> None:
        if self._adapter_provider is None:
            raise WorkLineStartConfigurationError("ECS 实时状态检查不可用")
        by_endpoint: dict[str, list[LineRunEpochDeviceBindingInput]] = {}
        for binding in bindings:
            by_endpoint.setdefault(binding.endpoint_base_url, []).append(binding)
        now_ms = int(timezone.to_utc(self._clock()).timestamp() * 1000)
        for endpoint, endpoint_bindings in sorted(by_endpoint.items()):
            try:
                adapter = await self._adapter_provider.get_adapter(endpoint)
                statuses = await adapter.fetch_statuses()
            except (KeyError, RuntimeError, ValueError) as exc:
                raise WorkLineStartConfigurationError(f"ECS Endpoint 实时状态不可用: {endpoint}") from exc
            by_code = {status.device.device_code: status for status in statuses}
            if len(by_code) != len(statuses):
                raise WorkLineStartConfigurationError(f"ECS Endpoint 返回重复 device_code: {endpoint}")
            for binding in endpoint_bindings:
                self._validate_live_device(endpoint, binding, by_code.get(binding.device_code), now_ms)

    @staticmethod
    def _validate_live_device(
        endpoint: str,
        binding: LineRunEpochDeviceBindingInput,
        status: EcsDeviceStatus | None,
        now_ms: int,
    ) -> None:
        if status is None:
            raise WorkLineStartConfigurationError(f"ECS Endpoint 缺少设备 {binding.device_code}: {endpoint}")
        state = status.state
        if (
            not state.is_online
            or state.mode is not EcsDeviceMode.AUTO
            or state.status is not EcsDeviceState.IDLE
            or state.current_command_code is not None
            or not 0 <= now_ms - state.updated_at <= binding.status_max_age_ms
        ):
            raise WorkLineStartConfigurationError(f"ECS 设备状态不可启动 {binding.device_code}: {endpoint}")
        supported = status.device.supported_commands
        required = _REQUIRED_COMMANDS[binding.device_role]
        if supported is None or required not in supported:
            raise WorkLineStartConfigurationError(f"ECS 设备能力不匹配 {binding.device_code}: {endpoint}")


__all__ = ["RoughSorterStartPlanBuilder"]
