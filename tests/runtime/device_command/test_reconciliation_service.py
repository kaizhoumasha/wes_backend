"""DeviceCommand 对账扫描只根据可证明事实推进状态。"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import datetime, timedelta

import pytest

from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.device.services.device_command_service import DeviceCommandService


class FakeBegin(AbstractAsyncContextManager[object]):
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *_args: object) -> None:
        return None


class FakeSessions:
    def begin(self) -> FakeBegin:
        return FakeBegin()


class FakeRepository:
    def __init__(self, command: DeviceCommand | None) -> None:
        self.command = command

    async def claim_next_reconcilable(self, _db: object, *, now: datetime) -> DeviceCommand | None:
        return self.command


def _command(status: CommandStatus) -> DeviceCommand:
    now = datetime(2026, 8, 13)
    return DeviceCommand(
        id=31,
        command_code="CMD-001",
        device_code="ARM-01",
        line_run_epoch_id=11,
        device_binding_id=21,
        execution_ref_type="TEST",
        execution_ref_id="EXEC-001",
        material_execution_id=None,
        contract_key="arm.pick",
        contract_version="2.0",
        task_type="PICK",
        params={},
        payload_digest="a" * 64,
        deadline_at=now - timedelta(seconds=1),
        status=status,
        claim_expires_at=now - timedelta(seconds=1),
        created_at=now - timedelta(minutes=1),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("initial", "expected", "reason"),
    [
        (CommandStatus.PENDING, CommandStatus.TIMED_OUT, None),
        (CommandStatus.DISPATCHING, CommandStatus.RECONCILING, "DISPATCH_LEASE_EXPIRED"),
        (CommandStatus.ACKNOWLEDGED, CommandStatus.RECONCILING, "ACK_DEADLINE_EXPIRED"),
    ],
)
async def test_reconcile_one_distinguishes_not_sent_from_delivery_unknown(initial, expected, reason) -> None:
    command = _command(initial)
    service = DeviceCommandService(
        session_factory=FakeSessions(),  # type: ignore[arg-type]
        command_repository=FakeRepository(command),  # type: ignore[arg-type]
        epoch_repository=object(),  # type: ignore[arg-type]
    )

    assert await service.reconcile_one(now=datetime(2026, 8, 13)) is True
    assert command.status == expected
    assert command.reconciliation_reason == reason


@pytest.mark.asyncio
async def test_reconcile_one_stops_on_empty_scan() -> None:
    service = DeviceCommandService(
        session_factory=FakeSessions(),  # type: ignore[arg-type]
        command_repository=FakeRepository(None),  # type: ignore[arg-type]
        epoch_repository=object(),  # type: ignore[arg-type]
    )

    assert await service.reconcile_one(now=datetime(2026, 8, 13)) is False
