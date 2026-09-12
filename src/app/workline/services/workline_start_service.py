"""在 WorkLine 行锁内检查版本并发布当前插件及执行合同。"""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any, cast

from src.app.device.contracts import DEVICE_INTEGRATION_CONTRACT_KEY, DEVICE_INTEGRATION_CONTRACT_VERSION
from src.app.device.repositories.device_repository import device_repository
from src.app.runtime.orchestration.repositories.workline_position_repository import workline_position_repository
from src.app.workline.activation import WorkLineActivationPlan, WorkLineDeviceBinding, WorkLinePositionBinding
from src.app.workline.installed_plugin import (
    InstalledWorkLinePlugin,
    parse_device_bindings,
    resolve_installed_plugin,
    resolve_position_bindings,
)
from src.app.workline.models.workline import LineType, WorkLine, WorkLinePositionInput
from src.app.workline.repositories.workline_repository import workline_repository
from src.core.conf import settings

if TYPE_CHECKING:
    from src.app.device.composition import DeviceEndpointAdapterProvider


class WorkLineStartNotFoundError(LookupError):
    """WorkLine 不存在。"""


class WorkLineStartInvalidStateError(ValueError):
    """工作线存在尚未闭合的义务或已经启用。"""


class WorkLineStartConfigurationError(ValueError):
    """插件或启动配置无效。"""


class WorkLineStartVersionConflictError(ValueError):
    """请求基于过期 WorkLine 版本。"""


class WorkLineStartService:
    def __init__(
        self,
        *,
        plugins: tuple[InstalledWorkLinePlugin, ...],
        workline_repository=workline_repository,
        position_repository=workline_position_repository,
        device_repository=device_repository,
        device_adapter_provider: DeviceEndpointAdapterProvider | None = None,
    ) -> None:
        self._plugins = plugins
        self._worklines = workline_repository
        self._positions = position_repository
        self._devices = device_repository
        self._adapter_provider = device_adapter_provider

    async def assert_execution_worker_startable(self, db: Any) -> None:
        for plugin_key, plugin_version in await self._worklines.list_active_plugin_identities(db):
            plugin = resolve_installed_plugin(self._plugins, plugin_key)
            if plugin.plugin_version != plugin_version:
                raise WorkLineStartConfigurationError(f"active WorkLine requires {plugin_key}@{plugin_version}")

    async def start(self, db: Any, *, workline_id: int, version: int) -> WorkLine:
        workline = await self._worklines.get_for_update(db, workline_id)
        if workline is None:
            raise WorkLineStartNotFoundError(f"WorkLine {workline_id} 不存在")
        if type(version) is not int or version != workline.version:
            raise WorkLineStartVersionConflictError(f"WorkLine {workline_id} 版本已变化，请重新读取状态")
        if workline.is_active:
            raise WorkLineStartInvalidStateError("WorkLine 已启用")
        # 明确启用不等待历史执行/反馈闭合；当前业务约束由已安装插件声明。
        plugin = self._resolve_plugin(workline)
        if plugin.business_blocker is not None:
            business = await plugin.business_blocker.get_unfinished_workload_summary(db, workline_id)
            if business["count"] > 0:
                raise WorkLineStartInvalidStateError(f"WorkLine 存在未闭合插件业务: {business.get('sample')}")
        position_rows = await self._positions.list_for_workline(db, workline_id, for_update=True)
        try:
            _ = parse_device_bindings(workline.config, plugin.device_roles)
            position_bindings = resolve_position_bindings(
                workline.config,
                plugin.position_slots,
                tuple(WorkLinePositionInput.model_validate(row) for row in position_rows),
            )
        except ValueError as exc:
            raise WorkLineStartConfigurationError(str(exc)) from exc
        plan = (
            await self._build_basic_plan(db, workline, plugin, position_bindings)
            if plugin.start_plan_builder is None
            else await plugin.start_plan_builder.build(db, workline, position_bindings=position_bindings)
        )
        if plan.position_bindings != position_bindings:
            raise WorkLineStartConfigurationError("START plan 工作位与 WorkLine 装配不一致")
        if (plan.plugin_key, plan.plugin_version) != (plugin.plugin_key, plugin.plugin_version):
            raise WorkLineStartConfigurationError("START plan 的插件身份与部署插件不一致")
        roles = {binding.device_role: binding.device_code for binding in plan.device_bindings}
        if roles != workline.config.get("device_bindings", {}):
            raise WorkLineStartConfigurationError("START plan 设备角色与 WorkLine 配置不一致")
        contracts = {}
        for binding in plan.device_bindings:
            contract = asdict(binding)
            contract.pop("device_role")
            if contract.pop("workline_id") != workline_id:
                raise WorkLineStartConfigurationError("START plan 设备不属于当前 WorkLine")
            code = contract.pop("device_code")
            if code in contracts:
                raise WorkLineStartConfigurationError("START plan 存在重复设备")
            contracts[code] = contract
        positions = {
            binding.position_role: {"location_id": binding.location_id, "location_type": binding.location_type}
            for binding in plan.position_bindings
        }
        if len(positions) != len(plan.position_bindings):
            raise WorkLineStartConfigurationError("START plan 存在重复位置角色")
        workline.plugin_version = plan.plugin_version
        workline.flow_mode = plan.flow_mode
        workline.device_contracts = contracts
        workline.position_bindings = positions
        return await self._worklines.set_active_for_start(db, workline)

    async def _build_basic_plan(
        self,
        db: Any,
        workline: WorkLine,
        plugin: InstalledWorkLinePlugin,
        position_bindings: tuple[WorkLinePositionBinding, ...],
    ) -> WorkLineActivationPlan:
        devices = await self._devices.get_by_work_line_id_for_update(db, cast("int", workline.id))
        by_code = {device.device_code: device for device in devices if not device.is_deleted}
        bindings: list[WorkLineDeviceBinding] = []
        for role, code in parse_device_bindings(workline.config, plugin.device_roles).items():
            device = by_code.get(code)
            if (
                device is None
                or not device.is_active
                or device.id is None
                or device.work_line_id != workline.id
                or not device.endpoint_base_url
            ):
                raise WorkLineStartConfigurationError(f"{role} 缺少本线启用设备或 Endpoint")
            try:
                bindings.append(
                    WorkLineDeviceBinding(
                        workline_id=cast("int", workline.id),
                        device_id=device.id,
                        device_code=code,
                        device_role=role,
                        endpoint_base_url=device.endpoint_base_url,
                        contract_key=DEVICE_INTEGRATION_CONTRACT_KEY,
                        contract_version=DEVICE_INTEGRATION_CONTRACT_VERSION,
                        status_max_age_ms=settings.WORKLINE_DEVICE_STATUS_MAX_AGE_MS,
                        command_timeout_ms=settings.WORKLINE_DEVICE_COMMAND_TIMEOUT_MS,
                    )
                )
            except ValueError as exc:
                raise WorkLineStartConfigurationError(str(exc)) from exc
        for endpoint in sorted({binding.endpoint_base_url for binding in bindings}):
            if self._adapter_provider is None:
                raise WorkLineStartConfigurationError("ECS 连通性检查不可用")
            try:
                adapter = await self._adapter_provider.get_adapter(endpoint)
                statuses = await adapter.fetch_statuses()
                statuses_by_code = {status.device.device_code: status for status in statuses}
                if len(statuses_by_code) != len(statuses):
                    raise ValueError("ECS 返回重复 device_code")
                for binding in bindings:
                    if binding.endpoint_base_url != endpoint:
                        continue
                    status = statuses_by_code.get(binding.device_code)
                    if status is None:
                        raise ValueError(f"ECS 缺少设备 {binding.device_code}")
                    # START 只发布执行合同；运行模式、空闲状态和时效由命令派发检查。
                    if not status.state.is_online:
                        raise ValueError(f"ECS 设备离线 {binding.device_code}")
            except (KeyError, RuntimeError, ValueError) as exc:
                raise WorkLineStartConfigurationError(f"ECS 连通性检查失败: {endpoint}: {exc}") from exc
        return WorkLineActivationPlan(
            plugin_key=plugin.plugin_key,
            plugin_version=plugin.plugin_version,
            flow_mode=None,
            device_bindings=tuple(bindings),
            position_bindings=position_bindings,
        )

    def _resolve_plugin(self, workline: Any) -> InstalledWorkLinePlugin:
        plugin_key = getattr(workline, "plugin_key", None)
        if not isinstance(plugin_key, str) or not plugin_key.strip():
            raise WorkLineStartConfigurationError("WorkLine 未选择业务插件")
        try:
            plugin = resolve_installed_plugin(self._plugins, plugin_key)
        except (LookupError, ValueError) as exc:
            raise WorkLineStartConfigurationError(str(exc)) from exc
        line_type = getattr(workline, "line_type", None)
        try:
            normalized_line_type = line_type if isinstance(line_type, LineType) else LineType(line_type)
        except ValueError as exc:
            raise WorkLineStartConfigurationError(f"WorkLine line_type 无效: {line_type}") from exc
        if not plugin.supports(normalized_line_type):
            raise WorkLineStartConfigurationError(
                f"plugin {plugin.plugin_key} 不支持 WorkLine line_type {normalized_line_type.value}"
            )
        return plugin
