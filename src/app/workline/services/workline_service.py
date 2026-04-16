"""WorkLine Service 层"""

from types import SimpleNamespace
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.device.repositories import device_repository
from src.app.workline.models import WorkLine
from src.app.workline.repositories import WorkLineRepository, workline_repository
from src.common.cache_config import cache_settings
from src.core.base_service import BaseService
from src.utils.device_cache import workline_device_cache
from src.workline_plugin_registry import get_workline_plugin_definition, validate_workline_plugin_assignment


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

    async def create(self, db: AsyncSession, data: dict[str, Any], cache: object | None = None) -> WorkLine | None:
        """创建工作线时仅校验插件标识，拓扑校验留到设备已关联后。"""

        self._validate_plugin_key(data.get("plugin_key"))
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
        plugin_key = data.get("plugin_key", getattr(current, "plugin_key", None))
        if not isinstance(plugin_key, str) or not plugin_key:
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
        if get_workline_plugin_definition(plugin_key) is None:
            from src.core.exceptions import BadRequestException

            raise BadRequestException(message=f"不支持的工作线插件: {plugin_key}")


# 创建单例
workline_service = WorkLineService()
