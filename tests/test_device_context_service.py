from types import SimpleNamespace

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

    service._device_repo = _DeviceRepo()
    service._workline_repo = _WorklineRepo()

    result, error = await service.resolve(object(), "ARM_01")

    assert error is None
    assert result is not None
    assert result.device.device_code == "ARM_01"
    assert result.plugin_key == "test_workline_plugin"
    assert result.contract_version == "1.0"
