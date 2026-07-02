"""DeviceCommand RESULT production observability contract tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest

from src.app.device.models.command import CommandCallbackResult, CommandResult, CommandStatus
from src.app.device.services.device_command_service import DeviceCommandService


class _CommandRepo:
    def __init__(self, command: SimpleNamespace) -> None:
        self.command = command
        self.update_calls: list[tuple[int, dict[str, Any]]] = []

    async def get_by_command_code(self, _db: object, command_code: str) -> SimpleNamespace | None:
        if self.command.command_code == command_code:
            return self.command
        return None

    async def update(self, _db: object, id: int, data: dict[str, Any]) -> SimpleNamespace | None:
        self.update_calls.append((id, dict(data)))
        for key, value in data.items():
            setattr(self.command, key, value)
        return self.command


@pytest.mark.asyncio
async def test_handle_callback_result_emits_device_command_result_observability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """设备命令结果被接受后必须发出稳定 result 观测事件。"""

    command = SimpleNamespace(
        id=55,
        command_code="CMD-RESULT-001",
        device_id=10,
        status=CommandStatus.ACK_RECEIVED,
        trace_id="trace-command",
        correlation_id="corr-command",
        event_id="evt-command",
        get_duration_ms=lambda: 456,
    )
    repo = _CommandRepo(command)
    service = DeviceCommandService()
    service.repo = repo  # type: ignore[assignment]
    service._invalidate_command_cache = AsyncMock()  # type: ignore[method-assign]
    db = SimpleNamespace(commit=AsyncMock())

    from src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl import (
        workline_runtime_reconciliation_service,
    )

    monkeypatch.setattr(
        workline_runtime_reconciliation_service,
        "record_late_callback_if_pending",
        AsyncMock(return_value=False),
    )
    emit = Mock()
    monkeypatch.setattr(
        "src.app.runtime.orchestration.observability.runtime_observability_registry.emit",
        emit,
    )

    callback = CommandCallbackResult(
        command_code=command.command_code,
        device_code="DEV-RESULT",
        result=CommandResult.SUCCESS,
        finish_time=1760000000123,
        trace_id="trace-callback",
        event_id="evt-result-001",
        causation_id="evt-ack-001",
    )

    outcome = await service.handle_callback_result(cast("Any", db), callback)

    assert outcome.command is command
    emit.assert_called_once_with(
        "device_command.result",
        {
            "trace_id": "trace-callback",
            "correlation_id": "corr-command",
            "command_code": "CMD-RESULT-001",
            "source_event_id": "evt-result-001",
        },
    )
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_callback_result_uses_stable_legacy_fallbacks_for_observability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """legacy 设备结果缺少 trace/event 时仍必须生成稳定观测锚点。"""

    command = SimpleNamespace(
        id=56,
        command_code="CMD-LEGACY-RESULT",
        device_id=10,
        status=CommandStatus.ACK_RECEIVED,
        trace_id=None,
        correlation_id=None,
        event_id=None,
        get_duration_ms=lambda: 0,
    )
    repo = _CommandRepo(command)
    service = DeviceCommandService()
    service.repo = repo  # type: ignore[assignment]
    service._invalidate_command_cache = AsyncMock()  # type: ignore[method-assign]
    db = SimpleNamespace(commit=AsyncMock())

    from src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl import (
        workline_runtime_reconciliation_service,
    )

    monkeypatch.setattr(
        workline_runtime_reconciliation_service,
        "record_late_callback_if_pending",
        AsyncMock(return_value=False),
    )
    emit = Mock()
    monkeypatch.setattr(
        "src.app.runtime.orchestration.observability.runtime_observability_registry.emit",
        emit,
    )

    callback = CommandCallbackResult(
        command_code=command.command_code,
        device_code="DEV-LEGACY",
        result=CommandResult.SUCCESS,
        finish_time=1760000000456,
    )

    _ = await service.handle_callback_result(cast("Any", db), callback)

    stable_source_event_id = "command_result:CMD-LEGACY-RESULT:1760000000456"
    emit.assert_called_once_with(
        "device_command.result",
        {
            "trace_id": stable_source_event_id,
            "correlation_id": "command:CMD-LEGACY-RESULT",
            "command_code": "CMD-LEGACY-RESULT",
            "source_event_id": stable_source_event_id,
        },
    )
