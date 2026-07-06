"""
设备上下文服务

封装设备 + 工作线的解析与验证逻辑。
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.device.models import Device
from src.app.device.repositories import DeviceRepository
from src.app.runtime.capability_catalog import get_workline_contract_version
from src.app.workline.models import WorkLine
from src.app.workline.repositories import WorkLineRepository
from src.core.logger import logger


@dataclass
class DeviceContextResult:
    """设备上下文结果"""

    device: Device
    workline: WorkLine | None
    plugin_key: str | None
    contract_version: str | None
    work_line_id: int | None
    is_workline_bound: bool


class DeviceContextService:
    """设备上下文服务 — 封装设备 + 工作线的解析与验证"""

    def __init__(self) -> None:
        self._device_repo = DeviceRepository()
        self._workline_repo = WorkLineRepository()

    def _resolve_plugin_key(self, device: Device, workline: WorkLine | None) -> str | None:
        candidate = getattr(workline, "plugin_key", None) if workline else getattr(device, "plugin_key", None)
        return candidate if isinstance(candidate, str) and candidate else None

    def _resolve_contract_version(
        self,
        device: Device,
        workline: WorkLine | None,
        plugin_key: str | None,
    ) -> str | None:
        contract_candidate = (
            getattr(workline, "contract_version", None) if workline else getattr(device, "contract_version", None)
        )
        contract_version = contract_candidate if isinstance(contract_candidate, str) and contract_candidate else None
        if contract_version:
            return contract_version
        return get_workline_contract_version(plugin_key)

    async def resolve(
        self,
        db: AsyncSession,
        device_code: str,
    ) -> tuple[DeviceContextResult, None] | tuple[None, dict[str, Any]]:
        """
        解析设备上下文，包含验证

        验证项：
        - 设备存在 (404)
        - 工作线存在 (404)
        - 工作线启用 (403)

        Returns:
            (DeviceContextResult, None) - 成功
            (None, error_response) - 失败
        """
        # 1. 查询设备
        device = await self._device_repo.get_by_device_code(db, device_code)
        if device is None:
            return None, self._build_not_found(f"未找到设备: {device_code}")

        # 2. 查询工作线
        workline: WorkLine | None = None
        work_line_id: int | None = getattr(device, "work_line_id", None)
        is_workline_bound = isinstance(work_line_id, int) and work_line_id > 0

        if is_workline_bound and work_line_id is not None:
            workline = await self._workline_repo.get_for_update(db, work_line_id)
            if workline is None:
                return None, self._build_not_found(f"设备 {device_code} 关联的工作线不存在")

            # 3. 验证工作线启用状态
            is_active = getattr(workline, "is_active", True)
            if not is_active:
                inactive_workline_id = workline.id if isinstance(workline.id, int) else work_line_id
                logger.warning(f"工作线 {inactive_workline_id} 未启用")
                return None, self._build_inactive(inactive_workline_id)

        # 4. 解析 plugin_key（唯一来源：WorkLine）
        plugin_key = self._resolve_plugin_key(device, workline)

        # 5. 解析 contract_version
        # WorkLine 优先，未绑定时兼容 device 快照，再回退 Plugin Registry
        contract_version = self._resolve_contract_version(device, workline, plugin_key)

        result = DeviceContextResult(
            device=device,
            workline=workline,
            plugin_key=plugin_key,
            contract_version=contract_version,
            work_line_id=work_line_id,
            is_workline_bound=is_workline_bound,
        )

        return result, None

    def _build_not_found(self, message: str) -> dict[str, Any]:
        return {
            "code": 404,
            "message": message,
        }

    def _build_inactive(self, workline_id: int) -> dict[str, Any]:
        return {
            "code": 403,
            "message": f"工作线 {workline_id} 未启用",
        }


# 全局实例
device_context_service = DeviceContextService()


__all__ = ["DeviceContextResult", "DeviceContextService", "device_context_service"]
