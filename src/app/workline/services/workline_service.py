"""WorkLine Service 层。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.app.runtime.orchestration.events_bridge import assert_not_reserved_runtime_event
from src.app.workline.domain.run_mode import (
    is_sandbox_allowed_environment,
    is_simulation_run_mode,
    normalize_run_mode,
)
from src.app.workline.models import (
    WorkLine,
    WorkLineConfigurationCheck,
    WorkLineRunMode,
)
from src.app.workline.repositories import WorkLineRepository, workline_repository
from src.common.cache_config import cache_settings
from src.core.base_service import BaseService
from src.core.conf import settings
from src.core.exceptions import BusinessException, OptimisticLockException
from src.utils.device_cache import workline_device_cache

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_BLOCKER = "BLOCKER"
_FAIL = "FAIL"
_OK = "PASS"
_ACTIVE_CONFIGURATION_FIELDS = frozenset({"line_code", "runtime_config_json", "run_mode", "line_type"})
_PLUGIN_CONFIGURATION_FIELDS = frozenset({"plugin_key", "config"})


class WorkLineService(BaseService[WorkLine, WorkLineRepository]):
    """管理 WorkLine 的通用配置与启停。"""

    def __init__(
        self,
        repository: WorkLineRepository = workline_repository,
    ) -> None:
        super().__init__(
            repository,
            enable_cache=True,
            cache_prefix=cache_settings.WORKLINE.prefix,
            cache_expire=cache_settings.WORKLINE.expire,
            list_cache_prefix=cache_settings.WORKLINE_LIST.prefix,
            list_cache_expire=cache_settings.WORKLINE_LIST.expire,
        )

    async def create(self, db: AsyncSession, data: dict[str, Any], cache: object | None = None) -> WorkLine | None:
        self._reject_active_state_write(data)
        self._reject_plugin_configuration_write(data)
        self._validate_run_mode(data)
        self._validate_runtime_config(data)
        result = await self.repo.create(db, data)
        await self._commit_mutation(db)
        if cache:
            await self.invalidate_cache(cache, invalidate_list=True)
        return result

    async def update(
        self,
        db: AsyncSession,
        id: int,
        data: dict[str, Any],
        cache: object | None = None,
    ) -> WorkLine | None:
        self._reject_active_state_write(data)
        self._reject_plugin_configuration_write(data)
        current = (
            await self.repo.get_for_update(db, id)
            if _ACTIVE_CONFIGURATION_FIELDS.intersection(data)
            else await self.repo.get_by_id(db, id)
        )
        if current is None:
            raise ValueError(f"WorkLine 不存在: {id}")
        self._reject_active_configuration_update(current, data)
        self._validate_run_mode(data, current=current)
        self._validate_runtime_config(data, current=current)
        return await super().update(db, id, data, cache)

    async def delete(self, db: AsyncSession, id: int, cache: object | None = None) -> bool | None:
        current = await self.repo.get_for_update(db, id)
        if current is None:
            return None
        if current.is_active:
            raise BusinessException("作业线已启用，请先停用后再删除")
        workload = await self.repo.get_unfinished_workload_summary(db, id)
        if workload["count"] > 0:
            raise BusinessException(message="存在未完成运行负载，不能删除作业线", detail={"workload": workload})
        result = await super().delete(db, id, cache)
        if result:
            workline_device_cache.invalidate(id)
        return result

    async def restore(self, db: AsyncSession, id: int, cache: object | None = None) -> WorkLine | None:
        result = await self.repo.restore(db, id)
        await self._commit_mutation(db)
        if cache:
            await self.invalidate_cache(cache, id, invalidate_list=True)
        return result

    @staticmethod
    def _assert_version(workline: WorkLine, workline_id: int, version: int) -> None:
        if getattr(workline, "version", None) != version:
            raise OptimisticLockException(
                resource_type="WorkLine",
                resource_id=workline_id,
                current_version=getattr(workline, "version", None),
                provided_version=version,
            )

    @staticmethod
    def _reject_active_state_write(data: dict[str, Any]) -> None:
        if "is_active" in data:
            raise BusinessException(message="作业线启用状态只能通过专用操作修改", detail={"fields": ["is_active"]})

    @staticmethod
    def _reject_plugin_configuration_write(data: dict[str, Any]) -> None:
        fields = sorted(_PLUGIN_CONFIGURATION_FIELDS.intersection(data))
        if fields:
            raise BusinessException(message="业务插件配置只能通过工作线配置操作修改", detail={"fields": fields})

    @staticmethod
    def _reject_active_configuration_update(workline: WorkLine, data: dict[str, Any]) -> None:
        submitted_fields = sorted(_ACTIVE_CONFIGURATION_FIELDS.intersection(data))
        if bool(getattr(workline, "is_active", False)) and submitted_fields:
            raise BusinessException(
                message="已启用作业线下不能修改实时运行字段，请先停用作业线",
                detail={"workline_id": getattr(workline, "id", None), "fields": submitted_fields},
            )

    @staticmethod
    def _check(code: str, status: str, severity: str, context: dict[str, Any]) -> WorkLineConfigurationCheck:
        return WorkLineConfigurationCheck(code=code, status=status, severity=severity, context=context)  # type: ignore[arg-type]

    @staticmethod
    def _can_activate(checks: list[WorkLineConfigurationCheck]) -> bool:
        return not any(check.status == _FAIL and check.severity == _BLOCKER for check in checks)

    @staticmethod
    def _run_mode_check(workline: WorkLine) -> WorkLineConfigurationCheck:
        run_mode = normalize_run_mode(getattr(workline, "run_mode", WorkLineRunMode.AUTO))
        if is_simulation_run_mode(run_mode) and not is_sandbox_allowed_environment(settings.APP_ENV):
            return WorkLineService._check(
                "RUN_MODE_ALLOWED", _FAIL, _BLOCKER, {"run_mode": run_mode, "app_env": settings.APP_ENV}
            )
        return WorkLineService._check("RUN_MODE_ALLOWED", _OK, "INFO", {"run_mode": run_mode})

    @staticmethod
    def _runtime_config_check(workline: WorkLine) -> WorkLineConfigurationCheck:
        try:
            WorkLineService._validate_runtime_config({}, current=workline)
        except (TypeError, ValueError) as exc:
            return WorkLineService._check("RUNTIME_CONFIG_VALID", _FAIL, _BLOCKER, {"message": str(exc)})
        return WorkLineService._check("RUNTIME_CONFIG_VALID", _OK, "INFO", {})

    @staticmethod
    def _validate_run_mode(data: dict[str, Any], current: WorkLine | None = None) -> None:
        raw_run_mode = data.get("run_mode", getattr(current, "run_mode", WorkLineRunMode.AUTO))
        run_mode = normalize_run_mode(raw_run_mode)
        if (
            run_mode in {item.value for item in WorkLineRunMode}
            and is_simulation_run_mode(run_mode)
            and not is_sandbox_allowed_environment(settings.APP_ENV)
        ):
            from src.core.exceptions import BadRequestException

            raise BadRequestException(message="SIMULATION 运行模式只能在 dev/test 环境启用")

    @staticmethod
    def _validate_runtime_config(data: dict[str, Any], current: WorkLine | None = None) -> None:
        runtime_config = data.get("runtime_config_json", getattr(current, "runtime_config_json", None))
        if runtime_config is None:
            return
        if not isinstance(runtime_config, dict):
            raise TypeError("runtime_config_json 必须为对象")
        event_mapping = runtime_config.get("event_type_mapping")
        if event_mapping is not None and not isinstance(event_mapping, dict):
            raise TypeError("runtime_config_json.event_type_mapping 必须为对象")
        if isinstance(event_mapping, dict):
            for source_event_type, mapped_event_type in event_mapping.items():
                if isinstance(mapped_event_type, str) and mapped_event_type:
                    assert_not_reserved_runtime_event(
                        mapped_event_type,
                        owner="runtime_config_json.event_type_mapping",
                        declaration_surface=f"{source_event_type} 的映射目标",
                    )


workline_service = WorkLineService()
