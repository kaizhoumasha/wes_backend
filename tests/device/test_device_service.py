"""设备主数据写入边界。"""

import pytest

from src.app.device.services.device_service import DeviceService
from src.core.exceptions import BusinessException


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["create", "update"])
async def test_device_crud_rejects_workline_ownership_even_when_called_without_api_validation(method: str) -> None:
    service = DeviceService()

    with pytest.raises(BusinessException, match="工作线配置"):
        if method == "create":
            await service.create(object(), {"device_code": "D-1", "work_line_id": 7})  # type: ignore[arg-type]
        else:
            await service.update(object(), 1, {"version": 1, "work_line_id": 7})  # type: ignore[arg-type]
