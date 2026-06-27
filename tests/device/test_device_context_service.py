from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.device.services.device_context_service import DeviceContextService


@pytest.mark.asyncio
async def test_resolve_uses_device_code_lookup() -> None:
    service = DeviceContextService()

    class _DeviceRepo:
        async def get_by_device_code(self, db: object, device_code: str) -> object:
            _ = db
            return SimpleNamespace(
                id=1,
                device_code=device_code,
                work_line_id=None,
                plugin_key="test_workline_plugin",
                contract_version="1.0",
            )

    class _WorklineRepo:
        async def get_by_id(self, db: object, work_line_id: int) -> None:
            _ = db, work_line_id

        async def get_for_update(self, db: object, work_line_id: int) -> None:
            _ = db, work_line_id

    service._device_repo = _DeviceRepo()
    service._workline_repo = _WorklineRepo()

    result, error = await service.resolve(object(), "ARM_01")

    assert error is None
    assert result is not None
    assert result.device.device_code == "ARM_01"
    assert result.plugin_key == "test_workline_plugin"
    assert result.contract_version == "1.0"


@pytest.mark.asyncio
async def test_resolve_locks_bound_workline_before_accepting_callback() -> None:
    service = DeviceContextService()

    class _DeviceRepo:
        async def get_by_device_code(self, db: object, device_code: str) -> object:
            _ = db
            return SimpleNamespace(
                id=1,
                device_code=device_code,
                work_line_id=10,
                plugin_key=None,
                contract_version=None,
            )

    class _WorklineRepo:
        def __init__(self) -> None:
            self.get_for_update = AsyncMock(
                return_value=SimpleNamespace(
                    id=10,
                    line_code="WL-ACTIVE",
                    plugin_key="rough_sorter",
                    contract_version="rough_sorter.v1",
                    is_active=True,
                )
            )

        async def get_by_id(self, db: object, work_line_id: int) -> object:
            _ = db, work_line_id
            raise AssertionError("运行入口必须锁定 WorkLine，不能普通读取")

    workline_repo = _WorklineRepo()
    service._device_repo = _DeviceRepo()
    service._workline_repo = workline_repo

    db = object()
    result, error = await service.resolve(db, "ARM_01")

    assert error is None
    assert result is not None
    workline_repo.get_for_update.assert_awaited_once_with(db, 10)


@pytest.mark.asyncio
async def test_resolve_rejects_device_bound_to_inactive_workline() -> None:
    service = DeviceContextService()

    class _DeviceRepo:
        async def get_by_device_code(self, db: object, device_code: str) -> object:
            _ = db
            return SimpleNamespace(
                id=1,
                device_code=device_code,
                work_line_id=10,
                plugin_key=None,
                contract_version=None,
            )

    class _WorklineRepo:
        async def get_for_update(self, db: object, work_line_id: int) -> object:
            _ = db
            return SimpleNamespace(
                id=work_line_id,
                line_code="WL-INACTIVE",
                plugin_key="rough_sorter",
                contract_version="rough_sorter.v1",
                is_active=False,
            )

    service._device_repo = _DeviceRepo()
    service._workline_repo = _WorklineRepo()

    result, error = await service.resolve(object(), "ARM_01")

    assert result is None
    assert error == {"code": 403, "message": "工作线 10 未启用"}
