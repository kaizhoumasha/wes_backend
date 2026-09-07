"""在 WorkLine 行锁内检查版本并发布当前插件及执行合同。"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from src.app.workline.installed_plugin import InstalledWorkLinePlugin, resolve_installed_plugin
from src.app.workline.models.workline import LineType, WorkLine
from src.app.workline.repositories.safety_incident_repository import workline_safety_incident_repository
from src.app.workline.repositories.workline_repository import workline_repository


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
        safety_repository=workline_safety_incident_repository,
    ) -> None:
        self._plugins = plugins
        self._worklines = workline_repository
        self._safety = safety_repository

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
        if await self._safety.get_active_for_workline(db, workline_id) is not None:
            raise WorkLineStartInvalidStateError("WorkLine 存在 active safety incident")
        unfinished = await self._worklines.get_unfinished_workload_summary(db, workline_id)
        if any(unfinished["by_type"].values()):
            raise WorkLineStartInvalidStateError(f"WorkLine 存在未闭合负载: {unfinished.get('sample')}")
        plugin = self._resolve_plugin(workline)
        if plugin.business_blocker is not None:
            business = await plugin.business_blocker.get_unfinished_workload_summary(db, workline_id)
            if business["count"] > 0:
                raise WorkLineStartInvalidStateError(f"WorkLine 存在未闭合插件业务: {business.get('sample')}")
        plan = await plugin.start_plan_builder.build(db, workline)
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
