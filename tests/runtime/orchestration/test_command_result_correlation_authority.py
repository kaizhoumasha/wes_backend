"""设备命令结果必须服从命令创建时固定的 ExecutionCorrelation。"""

from importlib import import_module
from types import SimpleNamespace

import pytest

from src.app.runtime.orchestration.services.device_command_gateway import prepare_runtime_device_command_effect


@pytest.mark.asyncio
async def test_device_command_gateway_persists_execution_correlation_separately_from_idempotency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    class DeviceCommandServiceStub:
        async def prepare_runtime_effect(self, _db: object, **kwargs: object) -> tuple[object, object]:
            calls.append(kwargs)
            return object(), object()

    command_service_module = import_module("src.app.device.services.device_command_service")
    monkeypatch.setattr(command_service_module, "device_command_service", DeviceCommandServiceStub())
    intent_log = SimpleNamespace(dispatch_key="device-command:CMD-CORRELATION")
    execution = SimpleNamespace(
        idempotency_key="system-capability:device-command:pick-1",
        intent_log=intent_log,
    )
    ctx = {
        "db": object(),
        "session": SimpleNamespace(),
        "workline": SimpleNamespace(),
        "trace_id": "trace-command-owner",
        "correlation_id": "corr-command-owner",
    }

    await prepare_runtime_device_command_effect(
        ctx,
        SimpleNamespace(),
        target_device_id=71,
        target_device_code=None,
        expected_workline_id=3,
        admission=SimpleNamespace(
            fact_version="device:v2",
            precondition=SimpleNamespace(expected_available=True),
        ),
        execution=execution,
        intent_log=intent_log,
    )

    assert calls[0]["idempotency_key"] == execution.idempotency_key
    assert calls[0]["execution_correlation_id"] == "corr-command-owner"
