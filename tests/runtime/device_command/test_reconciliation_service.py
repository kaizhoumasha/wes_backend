"""DeviceCommand 对账扫描只根据可证明事实推进状态。"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.device.services.device_command_service import DeviceCommandService


class FakeBegin(AbstractAsyncContextManager[object]):
    def __init__(self, events: list[str] | None = None, *, fail_commit: bool = False) -> None:
        self.events = events
        self.fail_commit = fail_commit

    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *_args: object) -> None:
        if self.fail_commit:
            raise RuntimeError("transaction commit failed")
        if self.events is not None:
            self.events.append("commit")


class FakeSessions:
    def __init__(self, events: list[str] | None = None, *, fail_commit: bool = False) -> None:
        self.events = events
        self.fail_commit = fail_commit

    def begin(self) -> FakeBegin:
        return FakeBegin(self.events, fail_commit=self.fail_commit)


class FakeRepository:
    def __init__(self, command: DeviceCommand | None) -> None:
        self.command = command

    async def claim_next_reconcilable(self, _db: object, *, now: datetime) -> DeviceCommand | None:
        return self.command


class FakeEvidenceService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.identities: dict[str, dict[str, object]] = {}

    async def record_device_observation(self, _db: object, **values: object) -> object:
        self.calls.append(values)
        source_identity = f"device:{values['command_code']}:observation:{values['observation']}"
        existing = self.identities.setdefault(source_identity, values)
        assert existing == values
        evidence = SimpleNamespace(
            id=len(self.identities),
            kind="DEVICE_OBSERVATION",
            source_identity=source_identity,
            device_code=values["device_code"],
            command_code=values["command_code"],
            normalized_payload={
                "command_code": values["command_code"],
                "device_code": values["device_code"],
                "observation": values["observation"],
                "observed_at": values["observed_at"].isoformat(),
                "reason_code": values["reason_code"],
            },
            apply_status="PENDING",
            processed_at=None,
        )
        return SimpleNamespace(evidence=evidence)


class FakePublisher:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.published: list[tuple[str, str, dict[str, object]]] = []

    async def publish_to(self, channel: str, event_type: str, payload: dict[str, object]) -> bool:
        self.events.append("publish")
        self.published.append((channel, event_type, payload))
        return True


def _command(status: CommandStatus) -> DeviceCommand:
    now = datetime(2026, 8, 13)
    return DeviceCommand(
        id=31,
        command_code="CMD-001",
        device_code="ARM-01",
        workline_id=11,
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
    ("initial", "expected", "reason", "observation"),
    [
        (CommandStatus.PENDING, CommandStatus.TIMED_OUT, None, "NOT_ACCEPTED"),
        (
            CommandStatus.DISPATCHING,
            CommandStatus.RECONCILING,
            "DISPATCH_LEASE_EXPIRED",
            "RESULT_UNKNOWN",
        ),
        (CommandStatus.ACKNOWLEDGED, CommandStatus.RECONCILING, "ACK_DEADLINE_EXPIRED", "RESULT_UNKNOWN"),
    ],
)
async def test_reconcile_one_records_observation_with_state_transition(initial, expected, reason, observation) -> None:
    command = _command(initial)
    evidences = FakeEvidenceService()
    service = DeviceCommandService(
        session_factory=FakeSessions(),  # type: ignore[arg-type]
        command_repository=FakeRepository(command),  # type: ignore[arg-type]
        workline_repository=object(),  # type: ignore[arg-type]
        evidence_service=evidences,  # type: ignore[arg-type]
    )

    assert await service.reconcile_one(now=datetime(2026, 8, 13)) is True
    assert command.status == expected
    assert command.reconciliation_reason == reason
    assert len(evidences.calls) == 1
    recorded = evidences.calls[0]
    assert recorded["device_code"] == "ARM-01"
    assert recorded["command_code"] == "CMD-001"
    assert recorded["contract_key"] == "arm.pick"
    assert recorded["contract_version"] == "2.0"
    assert recorded["observation"] == observation
    assert recorded["observed_at"] == datetime(2026, 8, 12, 23, 59, 59)
    assert recorded["reason_code"] == (reason or "COMMAND_DEADLINE_EXPIRED")


@pytest.mark.asyncio
async def test_reconcile_one_publishes_observation_only_after_transaction_commit() -> None:
    events: list[str] = []
    publisher = FakePublisher(events)
    service = DeviceCommandService(
        session_factory=FakeSessions(events),  # type: ignore[arg-type]
        command_repository=FakeRepository(_command(CommandStatus.ACKNOWLEDGED)),  # type: ignore[arg-type]
        workline_repository=object(),  # type: ignore[arg-type]
        evidence_service=FakeEvidenceService(),  # type: ignore[arg-type]
        event_publisher=publisher,  # type: ignore[arg-type]
    )

    assert await service.reconcile_one(now=datetime(2026, 8, 13)) is True

    assert events == ["commit", "publish"]
    channel, event_type, payload = publisher.published[0]
    assert channel == "device:evidence:stream"
    assert event_type == "device_evidence.updated"
    assert payload["kind"] == "DEVICE_OBSERVATION"
    assert payload["observation"] == "RESULT_UNKNOWN"
    assert payload["apply_status"] == "PENDING"


@pytest.mark.asyncio
async def test_reconcile_one_does_not_publish_observation_when_transaction_rolls_back() -> None:
    events: list[str] = []
    publisher = FakePublisher(events)
    service = DeviceCommandService(
        session_factory=FakeSessions(events, fail_commit=True),  # type: ignore[arg-type]
        command_repository=FakeRepository(_command(CommandStatus.PENDING)),  # type: ignore[arg-type]
        workline_repository=object(),  # type: ignore[arg-type]
        evidence_service=FakeEvidenceService(),  # type: ignore[arg-type]
        event_publisher=publisher,  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="transaction commit failed"):
        await service.reconcile_one(now=datetime(2026, 8, 13))

    assert publisher.published == []


@pytest.mark.asyncio
async def test_repeated_reconciliation_scan_reuses_observation_identity() -> None:
    evidences = FakeEvidenceService()
    for _ in range(2):
        service = DeviceCommandService(
            session_factory=FakeSessions(),  # type: ignore[arg-type]
            command_repository=FakeRepository(_command(CommandStatus.PENDING)),  # type: ignore[arg-type]
            workline_repository=object(),  # type: ignore[arg-type]
            evidence_service=evidences,  # type: ignore[arg-type]
        )
        assert await service.reconcile_one(now=datetime(2026, 8, 13)) is True

    assert len(evidences.calls) == 2
    assert len(evidences.identities) == 1


@pytest.mark.asyncio
async def test_reconcile_one_stops_on_empty_scan() -> None:
    service = DeviceCommandService(
        session_factory=FakeSessions(),  # type: ignore[arg-type]
        command_repository=FakeRepository(None),  # type: ignore[arg-type]
        workline_repository=object(),  # type: ignore[arg-type]
    )

    assert await service.reconcile_one(now=datetime(2026, 8, 13)) is False
