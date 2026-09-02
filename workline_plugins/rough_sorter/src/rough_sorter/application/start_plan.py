"""粗分机业务配置到通用 Epoch 激活计划的翻译。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, cast

from src.app.device.repositories.device_repository import device_repository
from src.app.workline.epoch_activation import (
    LineRunEpochDeviceBindingInput,
    LineRunEpochPositionBindingInput,
    WorkLineEpochActivationPlan,
)
from src.app.workline.services.workline_start_service import WorkLineStartConfigurationError

from rough_sorter.activation import POSITION_ROLES, RoughSorterConfigurationError, parse_activation_configuration
from rough_sorter.handlers._guards import ROLE_CONTRACTS
from rough_sorter.plugin import PLUGIN_KEY, PLUGIN_VERSION

_DEFAULT_DEVICE_REPOSITORY = cast("DeviceRepositoryPort", device_repository)


class DeviceRepositoryPort(Protocol):
    async def get_by_work_line_id_for_update(self, db: Any, work_line_id: int) -> list[Any]: ...


class RoughSorterStartPlanBuilder:
    """锁定一次 Device 集合，在内存中形成完整基础激活计划。"""

    def __init__(
        self,
        *,
        device_repository: DeviceRepositoryPort = _DEFAULT_DEVICE_REPOSITORY,
    ) -> None:
        self._devices = device_repository

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


__all__ = ["RoughSorterStartPlanBuilder"]
