"""device_command_gateway 迁入 runtime/orchestration 后的 runtime 行为锁定(F-7)。

阶段 6 C3 把 device_command_gateway 从 workline/services/ 物理迁入
runtime/orchestration/services/。本文件锁定迁入后的 runtime 行为契约:
- 模块路径与单例符号在 runtime/orchestration/services/ 下可导入
- reserve_sandbox_command: 设备不存在返回 False(不抛)
- reserve_sandbox_command: maintenance_mode 拒绝并抛 _DeviceCommandGovernanceError
- dispatch: 设备/通信配置缺失返回 False(不抛)
- dispatch: ACK 超时转为 RuntimeError("OUTBOX_ACK_TIMEOUT")

不依赖真实 DB/httpx,用 SimpleNamespace + AsyncMock + patch 隔离。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.app.runtime.orchestration.services.device_command_gateway import (
    DeviceCommandGateway,
    _DeviceCommandGovernanceError,
    device_command_gateway,
)


def test_module_relocated_to_runtime_orchestration():
    """阶段 6 C3:gateway 必须从 runtime/orchestration/services/ 路径导出。"""
    import importlib

    module = importlib.import_module("src.app.runtime.orchestration.services.device_command_gateway")
    assert module.DeviceCommandGateway is DeviceCommandGateway
    assert module.device_command_gateway is device_command_gateway


def test_singleton_is_gateway_instance():
    """模块级单例必须是 DeviceCommandGateway 实例。"""
    assert isinstance(device_command_gateway, DeviceCommandGateway)


def _patched_get_device(device: Any | None) -> Any:
    """patch _get_device_for_command_dispatch 返回固定 device,绕过 DB 层。"""
    return patch(
        "src.app.runtime.orchestration.services.device_command_gateway._get_device_for_command_dispatch",
        new=AsyncMock(return_value=device),
    )


@pytest.mark.asyncio
async def test_reserve_sandbox_command_returns_false_when_device_missing():
    """设备不存在时 reserve_sandbox_command 返回 False,不抛。"""
    outbox = SimpleNamespace(target_code="DEV-404", payload_json={"command_code": "CMD-1"})

    with _patched_get_device(None):
        result = await device_command_gateway.reserve_sandbox_command(db=object(), outbox=outbox)

    assert result is False


@pytest.mark.asyncio
async def test_reserve_sandbox_command_rejects_maintenance_mode():
    """maintenance_mode 设备拒绝沙箱命令派发,抛 _DeviceCommandGovernanceError。"""
    device = SimpleNamespace(
        device_code="DEV-MAINT",
        device_status="idle",
        maintenance_mode=True,
        current_command_id=None,
        capabilities_json=None,
    )
    outbox = SimpleNamespace(
        target_code="DEV-MAINT",
        payload_json={"command_code": "CMD-1"},
        session_id=1,
        dispatch_key="device-command:CMD-1",
    )

    with (
        _patched_get_device(device),
        patch(
            "src.app.device.repositories.command_repository.DeviceCommandRepository.get_by_command_code",
            new=AsyncMock(return_value=None),
        ),
    ):
        with pytest.raises(_DeviceCommandGovernanceError):
            await device_command_gateway.reserve_sandbox_command(db=object(), outbox=outbox)


@pytest.mark.asyncio
async def test_dispatch_returns_false_when_device_missing():
    """设备不存在时 dispatch 返回 False,不抛。"""
    outbox = SimpleNamespace(target_code="DEV-404", payload_json={"command_code": "CMD-1"})

    with _patched_get_device(None):
        result = await device_command_gateway.dispatch(db=object(), outbox=outbox)

    assert result is False


@pytest.mark.asyncio
async def test_dispatch_returns_false_when_host_or_port_missing():
    """设备通信配置不完整时 dispatch 返回 False,不抛。"""
    device = SimpleNamespace(
        device_code="DEV-NOHOST",
        device_status="idle",
        maintenance_mode=False,
        current_command_id=None,
        capabilities_json=None,
        host=None,
        port=8080,
    )
    outbox = SimpleNamespace(
        target_code="DEV-NOHOST",
        payload_json={"command_code": "CMD-1"},
        session_id=1,
        dispatch_key="device-command:CMD-1",
    )

    with _patched_get_device(device):
        result = await device_command_gateway.dispatch(db=object(), outbox=outbox)

    assert result is False


@pytest.mark.asyncio
async def test_dispatch_raises_runtime_error_on_ack_timeout():
    """httpx ACK 超时转为 RuntimeError("OUTBOX_ACK_TIMEOUT")。"""
    device = SimpleNamespace(
        device_code="DEV-TO",
        device_status="idle",
        maintenance_mode=False,
        current_command_id=None,
        capabilities_json=None,
        host="10.0.0.1",
        port=8080,
    )
    outbox = SimpleNamespace(
        target_code="DEV-TO",
        payload_json={"command_code": "CMD-1"},
        session_id=1,
        dispatch_key="device-command:CMD-1",
    )

    async_client_mock = AsyncMock()
    async_client_mock.post = AsyncMock(side_effect=httpx.TimeoutException("ack timeout"))
    async_client_mock.__aenter__ = AsyncMock(return_value=async_client_mock)
    async_client_mock.__aexit__ = AsyncMock(return_value=None)

    with (
        _patched_get_device(device),
        patch(
            "src.app.device.repositories.command_repository.DeviceCommandRepository.get_by_command_code",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "src.app.runtime.orchestration.services.device_command_gateway._ensure_realtime_device_status_ready",
            new=AsyncMock(return_value=None),
        ),
        patch("httpx.AsyncClient", return_value=async_client_mock),
    ):
        with pytest.raises(RuntimeError, match="OUTBOX_ACK_TIMEOUT"):
            await device_command_gateway.dispatch(db=object(), outbox=outbox)
