"""DeviceCommand 派发的事务边界与状态写回。"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import datetime, timedelta

import pytest

from src.app.device.contracts import EcsDeviceStatus, EcsSubmitDisposition, EcsSubmitResult
from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.device.services.device_dispatch_service import DeviceDispatchService
from src.app.workline.models.line_run_epoch import LineRunEpochDeviceBinding


class FakeBegin(AbstractAsyncContextManager[object]):
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *_args: object) -> None:
        return None


class FakeSessions:
    def begin(self) -> FakeBegin:
        return FakeBegin()


class FakeCommandRepository:
    def __init__(self, command: DeviceCommand) -> None:
        self.command = command

    async def claim_next_pending(self, _db, *, token, now, claim_expires_at):
        if self.command.status != CommandStatus.PENDING:
            return None
        self.command.transition_to(CommandStatus.DISPATCHING)
        self.command.claim_token = token
        self.command.claimed_at = now
        self.command.claim_expires_at = claim_expires_at
        self.command.attempt_count += 1
        return self.command

    async def get_claimed_for_update(self, _db, *, command_code, claim_token):
        if self.command.command_code == command_code and self.command.claim_token == claim_token:
            return self.command
        return None

    async def release_retryable(self, _db, command, *, next_attempt_at):
        command.transition_to(CommandStatus.PENDING)
        command.next_attempt_at = next_attempt_at
        command.claim_token = None

    async def mark_acknowledged(self, _db, command, *, acknowledged_at):
        command.transition_to(CommandStatus.ACKNOWLEDGED)
        command.ack_received_at = acknowledged_at
        command.claim_token = None

    async def mark_failed(self, _db, command, *, failure_code):
        command.failure_code = failure_code
        command.transition_to(CommandStatus.FAILED)
        command.claim_token = None

    async def mark_timed_out(self, _db, command):
        command.transition_to(CommandStatus.TIMED_OUT)
        command.claim_token = None

    async def mark_reconciling(self, _db, command, *, reason):
        command.reconciliation_reason = reason
        command.transition_to(CommandStatus.RECONCILING)
        command.claim_token = None

    async def mark_late_ack_reconciling(self, _db, command, *, acknowledged_at):
        command.ack_received_at = acknowledged_at
        command.reconciliation_reason = "ACK_AFTER_DEADLINE"
        command.transition_to(CommandStatus.RECONCILING)
        command.claim_token = None


class FakeEpochRepository:
    def __init__(self, binding: LineRunEpochDeviceBinding | None, events: list[str] | None = None) -> None:
        self.binding = binding
        self.events = events

    async def get_binding_for_dispatch(self, _db, *, line_run_epoch_id, device_code):
        if self.events is not None:
            self.events.append("binding")
        if self.binding is None:
            return None
        if (line_run_epoch_id, device_code) == (self.binding.line_run_epoch_id, self.binding.device_code):
            return self.binding
        return None


class FakeObservationRepository:
    def __init__(self) -> None:
        self.created = []

    async def add_status_observation(self, _db, observation):
        self.created.append(observation)
        return observation


class FakeAdapter:
    def __init__(self, result: EcsSubmitResult) -> None:
        self.result = result
        self.submitted: list[str] = []
        self.status_requests: list[str] = []

    async def fetch_status(self, device_code: str) -> EcsDeviceStatus:
        self.status_requests.append(device_code)
        return EcsDeviceStatus.model_validate(
            {
                "device_code": device_code,
                "contract_key": "arm.pick",
                "contract_version": "2.0",
                "mode": "AUTO",
                "status": "IDLE",
                "current_command_code": None,
                "error_detail": None,
                "timestamp": 1_786_579_200_000,
            }
        )

    async def submit_command(self, **values):
        self.submitted.append(values["command_code"])
        return self.result


class UnavailableStatusAdapter(FakeAdapter):
    async def fetch_status(self, device_code: str) -> EcsDeviceStatus:
        raise ConnectionError(device_code)


class FakeAdapterProvider:
    def __init__(
        self,
        adapter: FakeAdapter,
        *,
        error: Exception | None = None,
        events: list[str] | None = None,
    ) -> None:
        self.adapter = adapter
        self.error = error
        self.events = events
        self.requested: list[str] = []

    async def get_adapter(self, endpoint_base_url: str) -> FakeAdapter:
        if self.events is not None:
            self.events.append("provider")
        self.requested.append(endpoint_base_url)
        if self.error is not None:
            raise self.error
        return self.adapter


def _command() -> DeviceCommand:
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
        deadline_at=now + timedelta(minutes=1),
        created_at=now,
    )


def _binding() -> LineRunEpochDeviceBinding:
    return LineRunEpochDeviceBinding(
        id=21,
        line_run_epoch_id=11,
        device_id=7,
        device_code="ARM-01",
        device_role="PLACEMENT_DEVICE",
        endpoint_base_url="http://ecs-dispatch:8080",
        contract_key="arm.pick",
        contract_version="2.0",
        status_max_age_ms=1_000,
        command_timeout_ms=30_000,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("disposition", "expected"),
    [
        (EcsSubmitDisposition.ACKNOWLEDGED, CommandStatus.ACKNOWLEDGED),
        (EcsSubmitDisposition.RETRYABLE_NOT_ACCEPTED, CommandStatus.PENDING),
        (EcsSubmitDisposition.CONTRACT_REJECTED, CommandStatus.FAILED),
        (EcsSubmitDisposition.RECONCILING, CommandStatus.RECONCILING),
    ],
)
async def test_dispatch_result_is_fenced_into_reliable_state(disposition, expected) -> None:
    command = _command()
    adapter = FakeAdapter(EcsSubmitResult(disposition))
    events: list[str] = []
    provider = FakeAdapterProvider(adapter, events=events)
    observations = FakeObservationRepository()
    service = DeviceDispatchService(
        session_factory=FakeSessions(),  # type: ignore[arg-type]
        command_repository=FakeCommandRepository(command),  # type: ignore[arg-type]
        epoch_repository=FakeEpochRepository(_binding(), events),  # type: ignore[arg-type]
        observation_repository=observations,  # type: ignore[arg-type]
        adapter_provider=provider,  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 8, 13, 0, 0, 0, 500_000),
    )

    processed = await service.dispatch_one(now=datetime(2026, 8, 13, 0, 0, 0, 500_000))

    assert processed is True
    assert command.status == expected
    assert adapter.submitted == ["CMD-001"]
    assert len(observations.created) == 1
    assert provider.requested == ["http://ecs-dispatch:8080"]
    assert events[:2] == ["binding", "provider"]


@pytest.mark.asyncio
async def test_missing_or_invalid_binding_endpoint_never_reaches_http() -> None:
    for binding, provider_error in (
        (None, None),
        (_binding(), ValueError("invalid endpoint")),
    ):
        command = _command()
        adapter = FakeAdapter(EcsSubmitResult(EcsSubmitDisposition.ACKNOWLEDGED))
        provider = FakeAdapterProvider(adapter, error=provider_error)
        service = DeviceDispatchService(
            session_factory=FakeSessions(),  # type: ignore[arg-type]
            command_repository=FakeCommandRepository(command),  # type: ignore[arg-type]
            epoch_repository=FakeEpochRepository(binding),  # type: ignore[arg-type]
            observation_repository=FakeObservationRepository(),  # type: ignore[arg-type]
            adapter_provider=provider,  # type: ignore[arg-type]
        )

        assert await service.dispatch_one(now=datetime(2026, 8, 13)) is True
        assert command.status == CommandStatus.RECONCILING
        assert adapter.submitted == []
        assert adapter.status_requests == []


@pytest.mark.asyncio
async def test_status_probe_failure_returns_to_pending_because_command_was_not_sent() -> None:
    command = _command()
    adapter = UnavailableStatusAdapter(EcsSubmitResult(EcsSubmitDisposition.ACKNOWLEDGED))
    service = DeviceDispatchService(
        session_factory=FakeSessions(),  # type: ignore[arg-type]
        command_repository=FakeCommandRepository(command),  # type: ignore[arg-type]
        epoch_repository=FakeEpochRepository(_binding()),  # type: ignore[arg-type]
        observation_repository=FakeObservationRepository(),  # type: ignore[arg-type]
        adapter_provider=FakeAdapterProvider(adapter),  # type: ignore[arg-type]
    )

    assert await service.dispatch_one(now=datetime(2026, 8, 13)) is True
    assert command.status == CommandStatus.PENDING
    assert adapter.submitted == []


@pytest.mark.asyncio
async def test_command_crossing_deadline_during_status_probe_is_timed_out_before_submit() -> None:
    command = _command()
    adapter = FakeAdapter(EcsSubmitResult(EcsSubmitDisposition.ACKNOWLEDGED))
    service = DeviceDispatchService(
        session_factory=FakeSessions(),  # type: ignore[arg-type]
        command_repository=FakeCommandRepository(command),  # type: ignore[arg-type]
        epoch_repository=FakeEpochRepository(_binding()),  # type: ignore[arg-type]
        observation_repository=FakeObservationRepository(),  # type: ignore[arg-type]
        adapter_provider=FakeAdapterProvider(adapter),  # type: ignore[arg-type]
        clock=lambda: command.deadline_at,
    )

    assert await service.dispatch_one(now=command.deadline_at - timedelta(microseconds=1)) is True
    assert command.status == CommandStatus.TIMED_OUT
    assert adapter.submitted == []


@pytest.mark.asyncio
async def test_command_crossing_deadline_after_admission_is_timed_out_at_final_send_fence() -> None:
    command = _command()
    command.deadline_at = datetime(2026, 8, 13, 0, 0, 1)
    adapter = FakeAdapter(EcsSubmitResult(EcsSubmitDisposition.ACKNOWLEDGED))
    service = DeviceDispatchService(
        session_factory=FakeSessions(),  # type: ignore[arg-type]
        command_repository=FakeCommandRepository(command),  # type: ignore[arg-type]
        epoch_repository=FakeEpochRepository(_binding()),  # type: ignore[arg-type]
        observation_repository=FakeObservationRepository(),  # type: ignore[arg-type]
        adapter_provider=FakeAdapterProvider(adapter),  # type: ignore[arg-type]
        clock=iter([command.deadline_at - timedelta(microseconds=1), command.deadline_at]).__next__,
    )

    assert await service.dispatch_one(now=datetime(2026, 8, 13)) is True
    assert command.status == CommandStatus.TIMED_OUT
    assert adapter.submitted == []


@pytest.mark.asyncio
async def test_retryable_response_uses_retry_after_delay() -> None:
    command = _command()
    adapter = FakeAdapter(EcsSubmitResult(EcsSubmitDisposition.RETRYABLE_NOT_ACCEPTED, retry_after_seconds=60))
    service = DeviceDispatchService(
        session_factory=FakeSessions(),  # type: ignore[arg-type]
        command_repository=FakeCommandRepository(command),  # type: ignore[arg-type]
        epoch_repository=FakeEpochRepository(_binding()),  # type: ignore[arg-type]
        observation_repository=FakeObservationRepository(),  # type: ignore[arg-type]
        adapter_provider=FakeAdapterProvider(adapter),  # type: ignore[arg-type]
        clock=iter(
            [
                datetime(2026, 8, 13, 0, 0, 0, 500_000),
                datetime(2026, 8, 13, 0, 0, 10, 500_000),
                datetime(2026, 8, 13, 0, 0, 10, 500_000),
                datetime(2026, 8, 13, 0, 0, 10, 500_000),
            ]
        ).__next__,
    )

    now = datetime(2026, 8, 13, 0, 0, 0, 500_000)
    assert await service.dispatch_one(now=now) is True
    assert command.next_attempt_at == command.deadline_at


@pytest.mark.asyncio
async def test_huge_retry_after_is_fenced_by_command_deadline() -> None:
    command = _command()
    adapter = FakeAdapter(EcsSubmitResult(EcsSubmitDisposition.RETRYABLE_NOT_ACCEPTED, retry_after_seconds=10**100))
    response_at = datetime(2026, 8, 13, 0, 0, 10)
    service = DeviceDispatchService(
        session_factory=FakeSessions(),  # type: ignore[arg-type]
        command_repository=FakeCommandRepository(command),  # type: ignore[arg-type]
        epoch_repository=FakeEpochRepository(_binding()),  # type: ignore[arg-type]
        observation_repository=FakeObservationRepository(),  # type: ignore[arg-type]
        adapter_provider=FakeAdapterProvider(adapter),  # type: ignore[arg-type]
        clock=iter([datetime(2026, 8, 13, 0, 0, 0, 500_000), response_at, response_at, response_at]).__next__,
    )

    assert await service.dispatch_one(now=datetime(2026, 8, 13)) is True
    assert command.next_attempt_at == command.deadline_at
    assert command.status == CommandStatus.PENDING


@pytest.mark.asyncio
async def test_ack_received_after_deadline_enters_reconciliation_with_response_time() -> None:
    command = _command()
    command.deadline_at = datetime(2026, 8, 13, 0, 0, 1)
    adapter = FakeAdapter(EcsSubmitResult(EcsSubmitDisposition.ACKNOWLEDGED))
    response_at = command.deadline_at + timedelta(seconds=1)
    service = DeviceDispatchService(
        session_factory=FakeSessions(),
        command_repository=FakeCommandRepository(command),
        epoch_repository=FakeEpochRepository(_binding()),
        observation_repository=FakeObservationRepository(),
        adapter_provider=FakeAdapterProvider(adapter),
        clock=iter(
            [datetime(2026, 8, 13, 0, 0, 0, 500_000), datetime(2026, 8, 13, 0, 0, 0, 750_000), response_at]
        ).__next__,
    )

    assert await service.dispatch_one(now=datetime(2026, 8, 13)) is True
    assert command.status == CommandStatus.RECONCILING
    assert command.reconciliation_reason == "ACK_AFTER_DEADLINE"
    assert command.ack_received_at == response_at
