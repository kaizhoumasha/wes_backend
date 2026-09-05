"""WorkLine 业务插件配置与设备归属的唯一写入口。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, cast

from src.app.device.repositories.command_repository import device_command_repository
from src.app.device.repositories.device_repository import device_repository
from src.app.workline.installed_plugin import InstalledWorkLinePlugin, resolve_installed_plugin
from src.app.workline.models.workline import (
    LineType,
    WorkLine,
    WorkLineConfigurationStatus,
    WorkLinePluginSummary,
)
from src.app.workline.repositories.line_run_epoch_repository import line_run_epoch_repository
from src.app.workline.repositories.safety_incident_repository import workline_safety_incident_repository
from src.app.workline.repositories.workline_repository import workline_repository
from src.app.workline.services.line_run_epoch_service import LineRunEpochService
from src.app.workline.services.workline_service import WorkLineService
from src.core.exceptions import BusinessException
from src.utils.device_cache import workline_device_cache
from src.utils.timezone import timezone


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


class EpochConfigurationRepositoryPort(Protocol):
    async def get_active_for_workline(self, db: Any, workline_id: int) -> Any | None: ...

    async def lock_epoch_lifecycle(self, db: Any, epoch_id: int) -> None: ...

    async def get_active_for_workline_for_update(self, db: Any, workline_id: int) -> Any | None: ...


class EpochClosingServicePort(Protocol):
    async def close_active_epoch(self, db: Any, **kwargs: Any) -> Any | None: ...


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


@dataclass(frozen=True, slots=True)
class WorkLineConfigurationResult:
    workline: WorkLine
    device_codes: tuple[str, ...]


class WorkLineConfigurationService:
    """在一个事务中替换插件草稿、配置和设备全集。"""

    def __init__(
        self,
        *,
        plugins: tuple[InstalledWorkLinePlugin, ...],
        workline_repository: WorkLineConfigurationRepositoryPort = cast(
            "WorkLineConfigurationRepositoryPort", workline_repository
        ),
        device_repository: DeviceConfigurationRepositoryPort = cast(
            "DeviceConfigurationRepositoryPort", device_repository
        ),
        epoch_repository: EpochConfigurationRepositoryPort = cast(
            "EpochConfigurationRepositoryPort", line_run_epoch_repository
        ),
        epoch_service: EpochClosingServicePort | None = None,
        command_repository: Any = device_command_repository,
        safety_repository: SafetyConfigurationRepositoryPort = cast(
            "SafetyConfigurationRepositoryPort", workline_safety_incident_repository
        ),
        device_cache_invalidator: CacheInvalidatorPort | None = None,
        clock: Any = timezone.now_for_db,
    ) -> None:
        self._plugins = plugins
        self._worklines = workline_repository
        self._devices = device_repository
        self._epochs = epoch_repository
        self._epoch_service = epoch_service or LineRunEpochService(repository=cast("Any", epoch_repository))
        self._commands = command_repository
        self._safety = safety_repository
        self._device_cache_invalidator = device_cache_invalidator
        self._clock = clock

    async def save(
        self,
        db: Any,
        *,
        workline_id: int,
        version: int,
        plugin_key: str | None,
        config: dict[str, Any],
        device_codes: tuple[str, ...],
        cache: object | None = None,
    ) -> WorkLineConfigurationResult:
        workline = await self._worklines.get_for_update(db, workline_id)
        if workline is None:
            raise ValueError(f"WorkLine 不存在: {workline_id}")
        WorkLineService._assert_version(workline, workline_id, version)
        if bool(workline.is_active):
            raise BusinessException(message="已启用工作线不能修改业务插件配置")
        if await self._safety.get_active_for_workline(db, workline_id) is not None:
            raise BusinessException(message="存在 active safety incident，不能修改工作线配置")

        workload = await self._worklines.get_unfinished_workload_summary(db, workline_id)
        if workload["count"] > 0:
            raise BusinessException(message="存在未完成运行负载，不能修改工作线配置", detail={"workload": workload})
        await self._assert_no_plugin_workload(db, workline, action="修改工作线配置")

        normalized_plugin_key = self._validate_plugin(workline, plugin_key)
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
            if bool(getattr(device, "is_deleted", False)):
                raise BusinessException(message="设备已删除", detail={"device_code": device.device_code})
            if device.work_line_id not in {None, workline_id}:
                raise BusinessException(
                    message="设备已属于其他工作线",
                    detail={"device_code": device.device_code, "work_line_id": device.work_line_id},
                )
        if normalized_plugin_key is not None:
            plugin = resolve_installed_plugin(self._plugins, normalized_plugin_key)
            line_type = workline.line_type if isinstance(workline.line_type, LineType) else LineType(workline.line_type)
            reasons = self._plugin_incompatibility_reasons(plugin, line_type, workline, selected_devices)
            if reasons:
                raise BusinessException(
                    message="业务插件与工作线设备不兼容",
                    detail={"plugin_key": normalized_plugin_key, "reasons": list(reasons)},
                )

        changed_device_ids: list[int] = []
        for device in devices:
            if device.device_code in selected:
                if device.work_line_id != workline_id:
                    device.work_line_id = workline_id
                    device.increment_version()
                    changed_device_ids.append(device.id)
            elif device.work_line_id == workline_id:
                device.work_line_id = None
                device.increment_version()
                changed_device_ids.append(device.id)

        updated = await self._worklines.update(
            db,
            workline_id,
            {"plugin_key": normalized_plugin_key, "config": dict(config), "version": version},
        )
        if updated is None:
            raise ValueError(f"WorkLine 不存在: {workline_id}")
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
            if self._device_cache_invalidator is not None:
                for device_id in changed_device_ids:
                    await self._device_cache_invalidator.invalidate_cache(cache, device_id)
                await self._device_cache_invalidator.invalidate_cache(cache, invalidate_list=True)
        return WorkLineConfigurationResult(workline=updated, device_codes=tuple(sorted(normalized_codes)))

    async def available_plugins(self, db: Any, *, workline_id: int) -> tuple[WorkLinePluginSummary, ...]:
        workline = await self._worklines.get_by_id(db, workline_id)
        if workline is None:
            raise ValueError(f"WorkLine 不存在: {workline_id}")
        devices = tuple(await self._devices.get_by_work_line_id(db, workline_id))
        return self._summarize_plugins(workline, devices)

    async def configuration_status(self, db: Any, *, workline_id: int) -> WorkLineConfigurationStatus:
        workline = await self._worklines.get_by_id(db, workline_id)
        if workline is None:
            raise ValueError(f"WorkLine 不存在: {workline_id}")
        devices = tuple(await self._devices.get_by_work_line_id(db, workline_id))
        summaries = self._summarize_plugins(workline, devices)
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
            selected_plugin = resolve_installed_plugin(self._plugins, selected.plugin_key)
            configuration_reasons = list(selected.incompatibility_reasons)
            if selected_plugin.configuration_checker is not None:
                checked = selected_plugin.configuration_checker(workline, devices)
                if type(checked) is not tuple or any(not isinstance(reason, str) or not reason for reason in checked):
                    raise TypeError("configuration_checker must return tuple[str, ...]")
                checked_reasons = cast("tuple[str, ...]", checked)
                configuration_reasons.extend(
                    reason for reason in checked_reasons if reason not in configuration_reasons
                )
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

    def _summarize_plugins(self, workline: WorkLine, devices: tuple[Any, ...]) -> tuple[WorkLinePluginSummary, ...]:
        line_type = workline.line_type if isinstance(workline.line_type, LineType) else LineType(workline.line_type)
        summaries: list[WorkLinePluginSummary] = []
        for plugin in self._plugins:
            reasons = self._plugin_incompatibility_reasons(plugin, line_type, workline, devices)
            summaries.append(
                WorkLinePluginSummary(
                    plugin_key=plugin.plugin_key,
                    plugin_version=plugin.plugin_version,
                    display_name=plugin.display_name,
                    supported_line_types=plugin.supported_line_types,
                    compatible=not reasons,
                    incompatibility_reasons=reasons,
                )
            )
        return tuple(summaries)

    @staticmethod
    def _plugin_incompatibility_reasons(
        plugin: InstalledWorkLinePlugin,
        line_type: LineType,
        workline: WorkLine,
        devices: tuple[Any, ...],
    ) -> tuple[str, ...]:
        reasons: list[str] = []
        if not plugin.supports(line_type):
            reasons.append(f"LINE_TYPE_UNSUPPORTED:{line_type.value}")
        if plugin.compatibility_checker is not None:
            checked = plugin.compatibility_checker(workline, devices)
            if type(checked) is not tuple or any(not isinstance(reason, str) or not reason for reason in checked):
                raise TypeError("compatibility_checker must return tuple[str, ...]")
            reasons.extend(cast("tuple[str, ...]", checked))
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
        active_epoch = await self._epochs.get_active_for_workline(db, workline_id)
        if not bool(workline.is_active):
            if active_epoch is not None:
                raise BusinessException(message="停用 WorkLine 仍存在 ACTIVE Epoch")
            return workline
        if active_epoch is None:
            raise BusinessException(message="已启用 WorkLine 缺少 ACTIVE Epoch")
        if active_epoch.id is None:
            raise BusinessException(message="ACTIVE Epoch 缺少持久化主键")
        expected_epoch_id = active_epoch.id
        await self._epochs.lock_epoch_lifecycle(db, expected_epoch_id)
        active_epoch = await self._epochs.get_active_for_workline_for_update(db, workline_id)
        if active_epoch is None or active_epoch.id != expected_epoch_id:
            raise BusinessException(message="ACTIVE Epoch 在停用事务中发生变化")

        if await self._safety.get_active_for_workline(db, workline_id) is not None:
            raise BusinessException(message="存在 active safety incident，不能停用作业线")

        workload = await self._worklines.get_unfinished_workload_summary(db, workline_id)
        common_blockers = [
            owner_type
            for owner_type, blocked in workload["by_type"].items()
            if owner_type != "line_run_epochs" and bool(blocked)
        ]
        if common_blockers:
            raise BusinessException(
                message=f"存在未完成运行负载，不能停用作业线: {workload.get('sample')}",
                detail={"workload": workload},
            )

        await self._assert_no_plugin_workload(db, workline, action="停用作业线")

        closed = await self._epoch_service.close_active_epoch(
            db,
            workline_id=workline_id,
            closed_at=self._clock(),
            command_repository=self._commands,
        )
        if closed is None:
            raise BusinessException(message="ACTIVE Epoch 在停用事务中发生变化")
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

    def _validate_plugin(self, workline: WorkLine, plugin_key: str | None) -> str | None:
        if plugin_key is None:
            return None
        try:
            plugin = resolve_installed_plugin(self._plugins, plugin_key)
        except (LookupError, ValueError) as exc:
            raise BusinessException(message=str(exc)) from exc
        line_type = workline.line_type if isinstance(workline.line_type, LineType) else LineType(workline.line_type)
        if not plugin.supports(line_type):
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
            installed = resolve_installed_plugin(self._plugins, workline.plugin_key)
        except LookupError as exc:
            raise BusinessException(message=str(exc)) from exc
        except ValueError as exc:
            raise BusinessException(message=str(exc)) from exc
        if installed.business_blocker is None:
            return
        business = await installed.business_blocker.get_unfinished_workload_summary(db, workline.id)
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


__all__ = ["WorkLineConfigurationResult", "WorkLineConfigurationService"]
