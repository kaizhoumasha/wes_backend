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

from datetime import datetime, timedelta
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
from src.utils.timezone import timezone


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


@pytest.mark.asyncio
async def test_dispatch_uses_policy_wait_for_running_device_before_http_post():
    """RUNNING 设备未到 deadline 时必须走 DeviceDispatchPolicy 的有界等待决策。"""

    now = timezone.now_for_db()
    device = SimpleNamespace(
        device_code="DEV-BUSY",
        device_status="running",
        maintenance_mode=False,
        current_command_id=777,
        capabilities_json={
            "supported_command_types": ["SCAN"],
            "status_snapshot_ttl_ms": 1000,
        },
        host="10.0.0.2",
        port=8080,
        updated_at=now,
    )
    outbox = SimpleNamespace(
        target_code="DEV-BUSY",
        payload_json={
            "command_code": "CMD-BUSY",
            "task_type": "SCAN",
            "dispatch_deadline_at": (now + timedelta(seconds=10)).isoformat(),
        },
        session_id=1,
        dispatch_key="device-command:CMD-BUSY",
        trace_id="trace-policy",
        correlation_id="corr-policy",
    )

    async_client_mock = AsyncMock()
    async_client_mock.get = AsyncMock(side_effect=AssertionError("policy wait should happen before ECS status probe"))
    async_client_mock.post = AsyncMock(return_value=SimpleNamespace(status_code=200, text=""))
    async_client_mock.__aenter__ = AsyncMock(return_value=async_client_mock)
    async_client_mock.__aexit__ = AsyncMock(return_value=None)

    with (
        _patched_get_device(device),
        patch(
            "src.app.device.repositories.command_repository.DeviceCommandRepository.get_by_command_code",
            new=AsyncMock(return_value=None),
        ),
        patch("httpx.AsyncClient", return_value=async_client_mock),
        patch("src.app.runtime.orchestration.observability.runtime_observability_registry.emit") as emit,
    ):
        with pytest.raises(_DeviceCommandGovernanceError) as exc_info:
            await device_command_gateway.dispatch(db=object(), outbox=outbox)

    error = exc_info.value
    assert error.code == "DEVICE_STATUS_PRECHECK_WAIT"
    assert error.detail["policy_decision"] == "WAIT_FOR_IDLE"
    assert error.detail["reason"] == "DEVICE_BUSY"
    emit.assert_called_once()
    assert emit.call_args.args[0] == "device_command.dispatch_policy"
    assert emit.call_args.args[1] == {
        "trace_id": "trace-policy",
        "correlation_id": "corr-policy",
        "command_code": "CMD-BUSY",
        "device_code": "DEV-BUSY",
        "provider_code": "ECS",
        "policy_decision": "WAIT_FOR_IDLE",
        "reason": "DEVICE_BUSY",
        "dispatch_allowed": False,
        "runtime_hold_required": False,
    }
    async_client_mock.post.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_revalidates_stale_idle_snapshot_with_ecs_status_before_http_post():
    """过期 IDLE 快照不能直接派发，必须先重查 ECS 实时状态。"""

    now = timezone.now_for_db()
    device = SimpleNamespace(
        device_code="DEV-STALE",
        device_status="idle",
        maintenance_mode=False,
        current_command_id=None,
        capabilities_json={
            "supported_command_types": ["SCAN"],
            "status_snapshot_ttl_ms": 1,
        },
        host="10.0.0.4",
        port=8080,
        updated_at=now - timedelta(seconds=2),
    )
    outbox = SimpleNamespace(
        target_code="DEV-STALE",
        payload_json={"command_code": "CMD-STALE", "task_type": "SCAN"},
        session_id=1,
        dispatch_key="device-command:CMD-STALE",
    )
    events: list[str] = []

    async def status_probe(*_args, **_kwargs):
        events.append("status")
        return SimpleNamespace(
            status_code=200,
            text="",
            json=lambda: {"mode": "AUTO", "status": "IDLE", "current_command_id": None},
        )

    async def command_post(*_args, **_kwargs):
        events.append("post")
        return SimpleNamespace(status_code=200, text="")

    async_client_mock = AsyncMock()
    async_client_mock.get = AsyncMock(side_effect=status_probe)
    async_client_mock.post = AsyncMock(side_effect=command_post)
    async_client_mock.__aenter__ = AsyncMock(return_value=async_client_mock)
    async_client_mock.__aexit__ = AsyncMock(return_value=None)

    with (
        _patched_get_device(device),
        patch(
            "src.app.device.repositories.command_repository.DeviceCommandRepository.get_by_command_code",
            new=AsyncMock(return_value=None),
        ),
        patch("httpx.AsyncClient", return_value=async_client_mock),
    ):
        result = await device_command_gateway.dispatch(db=object(), outbox=outbox)

    assert result is True
    assert events == ["status", "post"]


@pytest.mark.asyncio
async def test_dispatch_emits_device_command_ack_observability_event() -> None:
    """设备命令 ACK 成功后必须发出稳定 ack age 观测事件。"""

    sent_at = datetime(2026, 7, 2, 9, 0, 0)
    ack_received_at = sent_at + timedelta(milliseconds=1234)
    device = SimpleNamespace(
        id=10,
        device_code="DEV-ACK",
        device_status="idle",
        maintenance_mode=False,
        current_command_id=None,
        capabilities_json={
            "supported_command_types": ["SCAN"],
            "status_snapshot_ttl_ms": 1000,
        },
        vendor_type="ECS",
        host="10.0.0.3",
        port=8080,
        updated_at=ack_received_at,
    )
    command = SimpleNamespace(
        id=55,
        command_code="CMD-ACK",
        status="PENDING",
        sent_at=sent_at,
        ack_received_at=None,
        ack_code=None,
        ack_message=None,
        trace_id="trace-ack",
        correlation_id="corr-ack",
        workline_id=7,
    )
    outbox = SimpleNamespace(
        target_code="DEV-ACK",
        payload_json={"command_code": "CMD-ACK", "task_type": "SCAN"},
        session_id=77,
        dispatch_key="device-command:CMD-ACK",
    )
    db = SimpleNamespace(refresh=AsyncMock())
    async_client_mock = AsyncMock()
    async_client_mock.post = AsyncMock(return_value=SimpleNamespace(status_code=200, text=""))
    async_client_mock.__aenter__ = AsyncMock(return_value=async_client_mock)
    async_client_mock.__aexit__ = AsyncMock(return_value=None)

    from src.app.device import services as device_services

    with (
        _patched_get_device(device),
        patch(
            "src.app.device.repositories.command_repository.DeviceCommandRepository.get_by_command_code",
            new=AsyncMock(return_value=command),
        ),
        patch(
            "src.app.runtime.orchestration.services.device_command_gateway._ensure_realtime_device_status_ready",
            new=AsyncMock(return_value=None),
        ),
        patch("httpx.AsyncClient", return_value=async_client_mock),
        patch(
            "src.app.runtime.orchestration.services.device_command_gateway.timezone.now_for_db",
            return_value=ack_received_at,
        ),
        patch.object(
            device_services.device_service,
            "mark_command_dispatched",
            new=AsyncMock(return_value=SimpleNamespace()),
        ),
        patch(
            "src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl."
            "workline_runtime_reconciliation_service.activate_execution_deadline_after_ack",
            new=AsyncMock(return_value=SimpleNamespace()),
        ),
        patch("src.app.sys.services.event_stream_service.defer_command_status_changed_event"),
        patch("src.app.runtime.orchestration.observability.runtime_observability_registry.emit") as emit,
    ):
        result = await device_command_gateway.dispatch(db=db, outbox=outbox)

    assert result is True
    assert [call.args[0] for call in emit.call_args_list] == [
        "device_command.dispatch_policy",
        "device_command.ack",
    ]
    assert emit.call_args_list[0].args[1] == {
        "trace_id": "trace-ack",
        "correlation_id": "corr-ack",
        "command_code": "CMD-ACK",
        "device_code": "DEV-ACK",
        "provider_code": "ECS",
        "policy_decision": "ALLOW_DISPATCH",
        "reason": "DEVICE_IDLE",
        "dispatch_allowed": True,
        "runtime_hold_required": False,
    }
    assert emit.call_args_list[1].args[1] == {
        "trace_id": "trace-ack",
        "correlation_id": "corr-ack",
        "command_code": "CMD-ACK",
        "provider_code": "ECS",
        "ack_age_ms": 1234,
    }
