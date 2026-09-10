"""WorkLine 业务插件配置与设备归属的唯一写入口。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, cast

from src.app.device.repositories.device_repository import device_repository
from src.app.runtime.orchestration.repositories.workline_position_repository import workline_position_repository
from src.app.workline.installed_plugin import (
    parse_device_bindings,
    resolve_position_bindings,
)
from src.app.workline.models.workline import (
    LineType,
    WorkLine,
    WorkLineBaseConfigurationResponse,
    WorkLineConfigurationStatus,
    WorkLinePluginSummary,
    WorkLinePositionInput,
)
from src.app.workline.repositories.safety_incident_repository import workline_safety_incident_repository
from src.app.workline.repositories.workline_repository import workline_repository
from src.app.workline.services.workline_service import WorkLineService
from src.core.exceptions import BusinessException
from src.utils.device_cache import workline_device_cache

if TYPE_CHECKING:
    from collections.abc import Mapping

    from wes_plugin_sdk import PluginDefinition


class WorkLineConfigurationRepositoryPort(Protocol):
    async def get_by_id(self, db: Any, workline_id: int) -> WorkLine | None: ...

    async def get_for_update(self, db: Any, workline_id: int) -> WorkLine | None: ...

    async def get_unfinished_workload_summary(self, db: Any, workline_id: int) -> dict[str, Any]: ...

    async def update(self, db: Any, id: int, data: dict[str, Any]) -> WorkLine | None: ...

    async def set_inactive_for_deactivate(self, db: Any, workline: WorkLine) -> WorkLine: ...


class DeviceConfigurationRepositoryPort(Protocol):
    async def list_for_workline_configuration_update(
        self,
        db: Any,
        *,
        workline_id: int,
        device_codes: tuple[str, ...],
    ) -> list[Any]: ...

    async def get_by_work_line_id(self, db: Any, workline_id: int) -> list[Any]: ...


class SafetyConfigurationRepositoryPort(Protocol):
    async def get_active_for_workline(self, db: Any, workline_id: int) -> Any | None: ...


class CacheInvalidatorPort(Protocol):
    async def invalidate_cache(
        self,
        cache: object,
        id: int | None = None,
        invalidate_list: bool = False,
        invalidate_tree: bool = False,
    ) -> None: ...


class PositionConfigurationRepositoryPort(Protocol):
    async def has_active_placements(self, db: Any, workline_id: int) -> bool: ...

    async def list_for_workline(self, db: Any, workline_id: int, *, for_update: bool = False) -> list[Any]: ...

    async def replace_for_workline(
        self, db: Any, *, workline: WorkLine, positions: tuple[WorkLinePositionInput, ...], existing: list[Any]
    ) -> None: ...


class PluginBusinessBlockerPort(Protocol):
    async def get_unfinished_workload_summary(self, db: Any, workline_id: int) -> dict[str, Any]: ...


class WorkLineConfigurationService:
    """分别维护稳定物理配置和业务插件关联，共用工作线事务边界。"""

    def __init__(
        self,
        *,
        definitions: tuple[PluginDefinition, ...],
        business_blockers: Mapping[str, PluginBusinessBlockerPort] | None = None,
        workline_repository: WorkLineConfigurationRepositoryPort = cast(
            "WorkLineConfigurationRepositoryPort", workline_repository
        ),
        device_repository: DeviceConfigurationRepositoryPort = cast(
            "DeviceConfigurationRepositoryPort", device_repository
        ),
        safety_repository: SafetyConfigurationRepositoryPort = cast(
            "SafetyConfigurationRepositoryPort", workline_safety_incident_repository
        ),
        device_cache_invalidator: CacheInvalidatorPort | None = None,
        position_repository: PositionConfigurationRepositoryPort = cast(
            "PositionConfigurationRepositoryPort", workline_position_repository
        ),
    ) -> None:
        self._definitions = definitions
        self._business_blockers = dict(business_blockers or {})
        self._worklines = workline_repository
        self._devices = device_repository
        self._safety = safety_repository
        self._device_cache_invalidator = device_cache_invalidator
        self._positions = position_repository

    async def _lock_editable(self, db: Any, *, workline_id: int, version: int) -> tuple[WorkLine, list[Any]]:
        workline = await self._worklines.get_for_update(db, workline_id)
        if workline is None:
            raise ValueError(f"WorkLine 不存在: {workline_id}")
        WorkLineService._assert_version(workline, workline_id, version)
        if bool(workline.is_active):
            raise BusinessException(message="已启用工作线不能修改配置")
        if await self._safety.get_active_for_workline(db, workline_id) is not None:
            raise BusinessException(message="存在 active safety incident，不能修改工作线配置")
        positions = await self._positions.list_for_workline(db, workline_id, for_update=True)
        workload = await self._worklines.get_unfinished_workload_summary(db, workline_id)
        if workload["count"] > 0:
            raise BusinessException(message="存在未完成运行负载，不能修改工作线配置", detail={"workload": workload})
        await self._assert_no_plugin_workload(db, workline, action="修改工作线配置")
        return workline, positions

    async def save(
        self,
        db: Any,
        *,
        workline_id: int,
        version: int,
        plugin_key: str | None,
        config: dict[str, Any],
        cache: object | None = None,
    ) -> WorkLine:
        """只更新业务装配，不触碰工作位和设备归属。"""
        workline, positions = await self._lock_editable(db, workline_id=workline_id, version=version)
        normalized_plugin_key = self._validate_plugin(workline, plugin_key)
        devices = await self._devices.list_for_workline_configuration_update(
            db,
            workline_id=workline_id,
            device_codes=(),
        )
        owned = tuple(device for device in devices if device.work_line_id == workline_id)
        if normalized_plugin_key is not None:
            plugin = self._resolve_definition(normalized_plugin_key)
            reasons = self._configuration_reasons(
                plugin,
                config,
                owned,
                tuple(WorkLinePositionInput.model_validate(p) for p in positions),
                require_complete=False,
            )
            if reasons:
                raise BusinessException(
                    message="业务插件与本线资源不兼容，请先完成基础配置",
                    detail={"plugin_key": normalized_plugin_key, "reasons": list(reasons)},
                )
        elif config not in (
            {},
            {"device_bindings": {}},
            {"position_bindings": {}},
            {"device_bindings": {}, "position_bindings": {}},
        ):
            raise BusinessException(message="未选择插件时不能保存角色配置")
        return await self._finish_save(
            db,
            workline_id=workline_id,
            version=version,
            changes={"plugin_key": normalized_plugin_key, "config": dict(config)},
            cache=cache,
        )

    async def base_configuration(self, db: Any, *, workline_id: int) -> WorkLineBaseConfigurationResponse:
        # 锁住工作线，避免读取到并发保存前的版本号与保存后的资源集合。
        workline = await self._worklines.get_for_update(db, workline_id)
        if workline is None:
            raise ValueError(f"WorkLine 不存在: {workline_id}")
        devices = await self._devices.get_by_work_line_id(db, workline_id)
        positions = await self._positions.list_for_workline(db, workline_id)
        return WorkLineBaseConfigurationResponse(
            workline_id=workline_id,
            version=workline.version,
            is_active=bool(workline.is_active),
            device_codes=tuple(sorted(device.device_code for device in devices if not device.is_deleted)),
            positions=tuple(WorkLinePositionInput.model_validate(position) for position in positions),
        )

    async def save_base(
        self,
        db: Any,
        *,
        workline_id: int,
        version: int,
        device_codes: tuple[str, ...],
        positions: tuple[WorkLinePositionInput, ...],
        cache: object | None = None,
    ) -> WorkLineBaseConfigurationResponse:
        """原子替换基础配置；保持当前插件选择和角色绑定。"""
        workline, existing_positions = await self._lock_editable(db, workline_id=workline_id, version=version)
        if await self._positions.has_active_placements(db, workline_id):
            raise BusinessException(message="存在货架或料箱占位，不能修改基础配置")
        normalized_codes = self._normalize_device_codes(device_codes)
        devices = await self._devices.list_for_workline_configuration_update(
            db,
            workline_id=workline_id,
            device_codes=normalized_codes,
        )
        by_code = {device.device_code: device for device in devices}
        missing = sorted(set(normalized_codes) - set(by_code))
        if missing:
            raise BusinessException(message="设备不存在", detail={"device_codes": missing})
        selected = set(normalized_codes)
        selected_devices = tuple(by_code[code] for code in normalized_codes)
        for device in selected_devices:
            if bool(device.is_deleted):
                raise BusinessException(message="设备已删除", detail={"device_code": device.device_code})
            if device.work_line_id not in {None, workline_id}:
                raise BusinessException(message="设备已属于其他工作线", detail={"device_code": device.device_code})
        self._validate_positions(positions, {device.id for device in selected_devices})
        if workline.plugin_key:
            plugin = self._resolve_definition(workline.plugin_key)
            reasons = self._configuration_reasons(
                plugin, workline.config, selected_devices, positions, require_complete=False
            )
            if reasons:
                raise BusinessException(
                    message="基础配置变更会使业务装配失效，请先解除相关角色绑定",
                    detail={"reasons": list(reasons)},
                )
        changed_device_ids: list[int] = []
        for device in devices:
            owner = workline_id if device.device_code in selected else None
            if device.work_line_id != owner:
                device.work_line_id = owner
                device.increment_version()
                changed_device_ids.append(device.id)
        await self._positions.replace_for_workline(
            db,
            workline=workline,
            positions=positions,
            existing=existing_positions,
        )
        updated = await self._finish_save(
            db,
            workline_id=workline_id,
            version=version,
            changes={},
            cache=cache,
            changed_device_ids=tuple(changed_device_ids),
        )
        return WorkLineBaseConfigurationResponse(
            workline_id=workline_id,
            version=updated.version,
            is_active=bool(updated.is_active),
            device_codes=tuple(sorted(normalized_codes)),
            positions=tuple(sorted(positions, key=lambda position: position.position_code)),
        )

    async def _finish_save(
        self,
        db: Any,
        *,
        workline_id: int,
        version: int,
        changes: dict[str, Any],
        cache: object | None,
        changed_device_ids: tuple[int, ...] = (),
    ) -> WorkLine:
        updated = await self._worklines.update(
            db,
            workline_id,
            {
                "plugin_version": None,
                "flow_mode": None,
                "device_contracts": {},
                "position_bindings": {},
                **changes,
                "version": version,
            },
        )
        if updated is None:
            raise ValueError(f"WorkLine 不存在: {workline_id}")
        try:
            await db.flush()
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        workline_device_cache.invalidate(workline_id)
        if cache is not None:
            from src.app.workline.services.workline_service import workline_service

            await workline_service.invalidate_cache(cache, workline_id, invalidate_list=True)
            if self._device_cache_invalidator is not None and changed_device_ids:
                for device_id in changed_device_ids:
                    await self._device_cache_invalidator.invalidate_cache(cache, device_id)
                await self._device_cache_invalidator.invalidate_cache(cache, invalidate_list=True)
        return updated

    @staticmethod
    def _validate_positions(positions: tuple[WorkLinePositionInput, ...], device_ids: set[int]) -> None:
        for field in ("position_code", "logic_location_code"):
            values = [getattr(position, field) for position in positions if getattr(position, field) is not None]
            if len(values) != len(set(values)):
                label = "工作位编码" if field == "position_code" else "逻辑位置编码"
                raise BusinessException(message=f"{label}不能重复")
        for position in positions:
            if position.device_id is not None and position.device_id not in device_ids:
                raise BusinessException(message=f"工作位 {position.position_code} 关联的设备不在本线设备中")

    @staticmethod
    def _configuration_reasons(
        plugin: PluginDefinition,
        config: object,
        devices: tuple[Any, ...],
        positions: tuple[WorkLinePositionInput, ...],
        *,
        require_complete: bool,
    ) -> tuple[str, ...]:
        try:
            bindings = parse_device_bindings(config, plugin.device_roles, require_complete=require_complete)
            _ = resolve_position_bindings(config, plugin.position_slots, positions, require_complete=require_complete)
        except ValueError:
            return ("CONFIGURATION_INVALID",)
        by_code = {device.device_code: device for device in devices if not device.is_deleted}
        reasons: list[str] = []
        for code in bindings.values():
            device = by_code.get(code)
            if device is None:
                reasons.append(f"DEVICE_BINDING_UNKNOWN:{code}")
            elif require_complete:
                if not device.is_active:
                    reasons.append(f"DEVICE_INACTIVE:{code}")
                if device.id is None or not device.endpoint_base_url:
                    reasons.append(f"DEVICE_ENDPOINT_MISSING:{code}")
        return tuple(reasons)

    async def available_plugins(self, db: Any, *, workline_id: int) -> tuple[WorkLinePluginSummary, ...]:
        workline = await self._worklines.get_by_id(db, workline_id)
        if workline is None:
            raise ValueError(f"WorkLine 不存在: {workline_id}")
        return self._summarize_plugins(workline)

    async def configuration_status(self, db: Any, *, workline_id: int) -> WorkLineConfigurationStatus:
        workline = await self._worklines.get_by_id(db, workline_id)
        if workline is None:
            raise ValueError(f"WorkLine 不存在: {workline_id}")
        devices = tuple(await self._devices.get_by_work_line_id(db, workline_id))
        positions = tuple(
            WorkLinePositionInput.model_validate(p) for p in await self._positions.list_for_workline(db, workline_id)
        )
        summaries = self._summarize_plugins(workline)
        checks = [WorkLineService._run_mode_check(workline), WorkLineService._runtime_config_check(workline)]
        selected = next((item for item in summaries if item.plugin_key == workline.plugin_key), None)
        if not workline.plugin_key:
            checks.append(WorkLineService._check("PLUGIN_SELECTED", "FAIL", "BLOCKER", {}))
        elif selected is None:
            checks.append(
                WorkLineService._check(
                    "PLUGIN_INSTALLED",
                    "FAIL",
                    "BLOCKER",
                    {"plugin_key": workline.plugin_key},
                )
            )
        else:
            selected_plugin = self._resolve_definition(selected.plugin_key)
            configuration_reasons = list(selected.incompatibility_reasons)
            checked_reasons = self._configuration_reasons(
                selected_plugin, workline.config, devices, positions, require_complete=True
            )
            configuration_reasons.extend(reason for reason in checked_reasons if reason not in configuration_reasons)
            checks.append(
                WorkLineService._check(
                    "PLUGIN_CONFIGURATION_COMPATIBLE",
                    "PASS" if not configuration_reasons else "FAIL",
                    "INFO" if not configuration_reasons else "BLOCKER",
                    {
                        "plugin_key": selected.plugin_key,
                        "reasons": configuration_reasons,
                    },
                )
            )
        return WorkLineConfigurationStatus(
            workline_id=workline_id,
            is_active=bool(workline.is_active),
            can_activate=WorkLineService._can_activate(checks),
            checks=checks,
        )

    def _summarize_plugins(self, workline: WorkLine) -> tuple[WorkLinePluginSummary, ...]:
        line_type = workline.line_type if isinstance(workline.line_type, LineType) else LineType(workline.line_type)
        summaries: list[WorkLinePluginSummary] = []
        for plugin in self._definitions:
            reasons = self._plugin_incompatibility_reasons(plugin, line_type)
            summaries.append(
                WorkLinePluginSummary(
                    plugin_key=plugin.plugin_key,
                    plugin_version=plugin.plugin_version,
                    display_name=plugin.display_name,
                    supported_line_types=tuple(LineType(value) for value in plugin.supported_line_types),
                    device_roles=plugin.device_roles,
                    position_slots=plugin.position_slots,
                    compatible=not reasons,
                    incompatibility_reasons=reasons,
                )
            )
        return tuple(summaries)

    @staticmethod
    def _plugin_incompatibility_reasons(
        plugin: PluginDefinition,
        line_type: LineType,
    ) -> tuple[str, ...]:
        reasons: list[str] = []
        if line_type.value not in plugin.supported_line_types:
            reasons.append(f"LINE_TYPE_UNSUPPORTED:{line_type.value}")
        return tuple(reasons)

    async def deactivate(
        self,
        db: Any,
        *,
        workline_id: int,
        version: int,
        cache: object | None = None,
    ) -> WorkLine:
        workline = await self._worklines.get_for_update(db, workline_id)
        if workline is None:
            raise ValueError(f"WorkLine 不存在: {workline_id}")
        WorkLineService._assert_version(workline, workline_id, version)
        if not bool(workline.is_active):
            return workline

        if await self._safety.get_active_for_workline(db, workline_id) is not None:
            raise BusinessException(message="存在 active safety incident，不能停用作业线")

        workload = await self._worklines.get_unfinished_workload_summary(db, workline_id)
        common_blockers = [owner_type for owner_type, blocked in workload["by_type"].items() if bool(blocked)]
        if common_blockers:
            raise BusinessException(
                message=f"存在未完成运行负载，不能停用作业线: {workload.get('sample')}",
                detail={"workload": workload},
            )

        await self._assert_no_plugin_workload(db, workline, action="停用作业线")

        updated = await self._worklines.set_inactive_for_deactivate(db, workline)
        await db.flush()
        try:
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        workline_device_cache.invalidate(workline_id)
        if cache is not None:
            from src.app.workline.services.workline_service import workline_service

            await workline_service.invalidate_cache(cache, workline_id, invalidate_list=True)
        return updated

    def _resolve_definition(self, plugin_key: str) -> PluginDefinition:
        matches = tuple(definition for definition in self._definitions if definition.plugin_key == plugin_key)
        if not matches:
            raise LookupError(f"未安装业务插件: {plugin_key}")
        if len(matches) != 1:
            raise ValueError(f"重复安装业务插件: {plugin_key}")
        return matches[0]

    def _validate_plugin(self, workline: WorkLine, plugin_key: str | None) -> str | None:
        if plugin_key is None:
            return None
        try:
            plugin = self._resolve_definition(plugin_key)
        except (LookupError, ValueError) as exc:
            raise BusinessException(message=str(exc)) from exc
        line_type = workline.line_type if isinstance(workline.line_type, LineType) else LineType(workline.line_type)
        if line_type.value not in plugin.supported_line_types:
            raise BusinessException(message=f"plugin {plugin_key} 不支持 WorkLine line_type {line_type.value}")
        return plugin.plugin_key

    async def _assert_no_plugin_workload(
        self,
        db: Any,
        workline: WorkLine,
        *,
        action: str,
    ) -> None:
        if workline.plugin_key is None:
            return
        try:
            installed = self._resolve_definition(workline.plugin_key)
        except LookupError as exc:
            raise BusinessException(message=str(exc)) from exc
        except ValueError as exc:
            raise BusinessException(message=str(exc)) from exc
        if workline.plugin_version is not None and installed.plugin_version != workline.plugin_version:
            raise BusinessException(message=f"未安装当前业务插件版本: {workline.plugin_key}@{workline.plugin_version}")
        blocker = self._business_blockers.get(installed.plugin_key)
        if blocker is None:
            return
        business = await blocker.get_unfinished_workload_summary(db, cast("int", workline.id))
        if business["count"] > 0:
            raise BusinessException(
                message=f"存在未完成插件业务任务，不能{action}: {business.get('sample')}",
                detail={"workload": business},
            )

    @staticmethod
    def _normalize_device_codes(device_codes: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(code.strip() for code in device_codes)
        if any(not code for code in normalized):
            raise BusinessException(message="设备编码不能为空")
        if len(set(normalized)) != len(normalized):
            raise BusinessException(message="设备编码重复")
        return normalized


__all__ = ["WorkLineConfigurationService"]
