"""粗分机业务配置到通用 WorkLine 激活计划的翻译。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, cast

from src.app.device.contracts import EcsDeviceMode, EcsDeviceState
from src.app.device.repositories.device_repository import device_repository
from src.app.workline.activation import (
    WorkLineActivationPlan,
    WorkLineDeviceBinding,
    WorkLinePositionBinding,
)
from src.app.workline.installed_plugin import parse_device_bindings
from src.app.workline.models.workline import WorkLineDeviceRole
from src.app.workline.services.workline_start_service import WorkLineStartConfigurationError
from src.utils.timezone import timezone

from rough_sorter.handlers._guards import ROLE_CONTRACTS
from rough_sorter.plugin import PLUGIN_KEY, PLUGIN_VERSION, POSITION_ROLES

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

    device_roles = (
        WorkLineDeviceRole(role_key="MEASUREMENT_DEVICE", display_name="测量设备"),
        WorkLineDeviceRole(role_key="TRANSFER_DEVICE", display_name="输送设备"),
        WorkLineDeviceRole(role_key="PLACEMENT_DEVICE", display_name="放置设备"),
    )

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

    async def build(self, db: Any, workline: Any) -> WorkLineActivationPlan:
        try:
            bindings = parse_device_bindings(workline.config, self.device_roles)
        except ValueError as exc:
            raise WorkLineStartConfigurationError(str(exc)) from exc

        devices = await self._devices.get_by_work_line_id_for_update(db, workline.id)
        by_code = {device.device_code: device for device in devices if not device.is_deleted}
        device_bindings: list[WorkLineDeviceBinding] = []
        for role, contract_key in ROLE_CONTRACTS.items():
            device = by_code.get(bindings[role])
            if device is None or not device.is_active or device.id is None or not device.endpoint_base_url:
                raise WorkLineStartConfigurationError(f"{role} 缺少本线启用设备或 Endpoint")
            try:
                device_bindings.append(
                    WorkLineDeviceBinding(
                        workline_id=workline.id,
                        device_id=device.id,
                        device_code=device.device_code,
                        device_role=role,
                        endpoint_base_url=device.endpoint_base_url,
                        contract_key=contract_key,
                        contract_version="1.0",
                        status_max_age_ms=10_000,
                        command_timeout_ms=30_000,
                    )
                )
            except ValueError as exc:
                raise WorkLineStartConfigurationError(f"{role} Device Endpoint 非法") from exc

        await self._validate_live_devices(tuple(device_bindings))

        return WorkLineActivationPlan(
            plugin_key=PLUGIN_KEY,
            plugin_version=PLUGIN_VERSION,
            flow_mode="ROUGH_SORT_INBOUND",
            device_bindings=tuple(device_bindings),
            position_bindings=tuple(
                WorkLinePositionBinding(
                    position_role=role,
                    location_id=role,
                    location_type=role,
                )
                for role in POSITION_ROLES
            ),
        )

    async def _validate_live_devices(self, bindings: tuple[WorkLineDeviceBinding, ...]) -> None:
        if self._adapter_provider is None:
            raise WorkLineStartConfigurationError("ECS 实时状态检查不可用")
        by_endpoint: dict[str, list[WorkLineDeviceBinding]] = {}
        for binding in bindings:
            by_endpoint.setdefault(binding.endpoint_base_url, []).append(binding)
        for endpoint, endpoint_bindings in sorted(by_endpoint.items()):
            try:
                adapter = await self._adapter_provider.get_adapter(endpoint)
                statuses = await adapter.fetch_statuses()
                now_ms = int(timezone.to_utc(self._clock()).timestamp() * 1000)
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
        binding: WorkLineDeviceBinding,
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
        if binding.device_role == "MEASUREMENT_DEVICE" and "SCAN_COMPLETED" not in (
            status.device.supported_events or ()
        ):
            raise WorkLineStartConfigurationError(
                f"ECS 测量设备缺少 SCAN_COMPLETED 事件能力 {binding.device_code}: {endpoint}"
            )


__all__ = ["RoughSorterStartPlanBuilder"]
