"""WorkLine Service 层"""

from types import SimpleNamespace
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.device.repositories import device_repository
from src.app.workline.models import WorkLine, WorkLineRunMode
from src.app.workline.repositories import WorkLineRepository, workline_repository
from src.common.cache_config import cache_settings
from src.core.base_service import BaseService
from src.core.conf import settings
from src.utils.device_cache import workline_device_cache
from src.workline_plugin_registry import (
    get_plugin_contract_version,
    get_workline_plugin_definition,
    validate_workline_plugin_assignment,
)
from src.workline_runtime.run_mode import is_sandbox_allowed_environment, is_simulation_run_mode, normalize_run_mode


class WorkLineService(BaseService[WorkLine, WorkLineRepository]):
    """作业线业务逻辑层"""

    def __init__(self) -> None:
        super().__init__(
            workline_repository,
            enable_cache=True,
            cache_prefix=cache_settings.WORKLINE.prefix,
            cache_expire=cache_settings.WORKLINE.expire,
            list_cache_prefix=cache_settings.WORKLINE_LIST.prefix,
            list_cache_expire=cache_settings.WORKLINE_LIST.expire,
        )

    @staticmethod
    def _resolve_plugin_key(data: dict[str, Any], current: WorkLine | None = None) -> str | None:
        plugin_key = data.get("plugin_key", getattr(current, "plugin_key", None))
        return plugin_key if isinstance(plugin_key, str) and plugin_key else None

    async def create(self, db: AsyncSession, data: dict[str, Any], cache: object | None = None) -> WorkLine | None:
        """创建工作线时仅校验插件标识，拓扑校验留到设备已关联后。"""
        self._validate_plugin_key(data.get("plugin_key"))
        self._validate_run_mode(data)
        self._validate_runtime_config(data)
        self._apply_runtime_defaults(data)
        return await super().create(db, data, cache)

    async def update(
        self,
        db: AsyncSession,
        id: int,
        data: dict[str, Any],
        cache: object | None = None,
    ) -> WorkLine | None:
        """更新工作线前校验插件拓扑要求。"""
        current = await self.repo.get_by_id(db, id)
        if current is None:
            raise ValueError(f"WorkLine 不存在: {id}")

        await self._validate_plugin_assignment(db, current=current, data=data)
        self._validate_run_mode(data, current=current)
        self._validate_runtime_config(data, current=current)
        self._apply_runtime_defaults(data, current=current)
        return await super().update(db, id, data, cache)

    async def delete(self, db: AsyncSession, id: int, cache: object | None = None) -> bool | None:
        """删除工作线后失效设备缓存"""
        result = await super().delete(db, id, cache)
        if result:
            # 失效该工作线的设备缓存
            workline_device_cache.invalidate(id)
        return result

    async def _validate_plugin_assignment(
        self,
        db: AsyncSession,
        current: WorkLine | None,
        data: dict[str, Any],
    ) -> None:
        plugin_key = self._resolve_plugin_key(data, current)
        if plugin_key is None:
            return

        workline_id = getattr(current, "id", None)
        devices = await device_repository.get_by_work_line_id(db, workline_id) if isinstance(workline_id, int) else []
        workline_like = SimpleNamespace(
            id=workline_id,
            line_code=data.get("line_code", getattr(current, "line_code", None)),
            line_name=data.get("line_name", getattr(current, "line_name", None)),
            plugin_key=plugin_key,
        )
        validate_workline_plugin_assignment(plugin_key, workline_like, devices)

    @staticmethod
    def _validate_plugin_key(plugin_key: object) -> None:
        if not isinstance(plugin_key, str) or not plugin_key:
            return
        definition = get_workline_plugin_definition(plugin_key)
        if definition is None:
            from src.core.exceptions import BadRequestException

            raise BadRequestException(message=f"不支持的工作线插件: {plugin_key}")
        try:
            _ = definition.manifest
        except (TypeError, ValueError) as exc:
            from src.core.exceptions import BadRequestException

            raise BadRequestException(message=str(exc)) from exc

    @staticmethod
    def _validate_run_mode(data: dict[str, Any], current: WorkLine | None = None) -> None:
        """校验 WORKLINE 级运行模式。

        SIMULATION 是真实派发到沙箱通道的调试模式，只允许在开发/测试环境启用。
        """

        raw_run_mode = data.get("run_mode", getattr(current, "run_mode", WorkLineRunMode.AUTO))
        run_mode = normalize_run_mode(raw_run_mode)
        if run_mode not in {item.value for item in WorkLineRunMode}:
            return

        if is_simulation_run_mode(run_mode) and not is_sandbox_allowed_environment(settings.APP_ENV):
            from src.core.exceptions import BadRequestException

            raise BadRequestException(message="SIMULATION 运行模式只能在 dev/test 环境启用")

    @staticmethod
    def _apply_runtime_defaults(data: dict[str, Any], current: WorkLine | None = None) -> None:
        """为工作线写入运行时治理默认值。"""
        plugin_key = WorkLineService._resolve_plugin_key(data, current)
        contract_version = data.get("contract_version", getattr(current, "contract_version", None))
        if (not isinstance(contract_version, str) or not contract_version) and plugin_key is not None:
            resolved = get_plugin_contract_version(plugin_key)
            if isinstance(resolved, str) and resolved:
                data.setdefault("contract_version", resolved)

    @staticmethod
    def _validate_runtime_config(data: dict[str, Any], current: WorkLine | None = None) -> None:
        """校验工作线运行时配置，仅约束当前已消费的关键字段。"""

        if "runtime_config_json" not in data and current is None:
            return

        runtime_config = data.get("runtime_config_json", getattr(current, "runtime_config_json", None))
        if runtime_config is None:
            return
        if not isinstance(runtime_config, dict):
            raise TypeError("runtime_config_json 必须为对象")

        event_mapping = runtime_config.get("event_type_mapping")
        if event_mapping is not None and not isinstance(event_mapping, dict):
            raise TypeError("runtime_config_json.event_type_mapping 必须为对象")


# 创建单例
workline_service = WorkLineService()
