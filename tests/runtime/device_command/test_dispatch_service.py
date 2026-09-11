"""DeviceCommand 派发的事务边界与状态写回。"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from src.app.device.contracts import (
    EcsDeviceMode,
    EcsDeviceState,
    EcsDeviceStatus,
    EcsSubmitDisposition,
    EcsSubmitResult,
)
from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.device.services.device_dispatch_service import DeviceDispatchService


class FakeBegin(AbstractAsyncContextManager[object]):
    def __init__(self, events: list[str] | None = None) -> None:
        self.events = events

    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *_args: object) -> None:
        if self.events is not None:
            self.events.append("commit")


class FakeSessions:
    def __init__(self, events: list[str] | None = None) -> None:
        self.events = events

    def begin(self) -> FakeBegin:
        return FakeBegin(self.events)


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


class FakeObservationRepository:
    def __init__(self) -> None:
        self.created = []

    async def add_status_observation(self, _db, observation):
        self.created.append(observation)
        return observation


class FakeEvidenceService:
    def __init__(self) -> None:
        self.observations: list[dict[str, object]] = []

    async def record_device_observation(self, _db: object, **values: object) -> object:
        self.observations.append(values)
        source_identity = f"device:{values['command_code']}:observation:{values['observation']}"
        evidence = SimpleNamespace(
            id=len(self.observations),
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


class FakeAdapter:
    def __init__(self, result: EcsSubmitResult) -> None:
        self.result = result
        self.submitted: list[dict[str, object]] = []
        self.status_requests: list[str] = []

    async def fetch_status(self, device_code: str) -> EcsDeviceStatus:
        self.status_requests.append(device_code)
        return EcsDeviceStatus.model_validate(
            {
                "device": {
                    "device_code": device_code,
                    "device_name": "机械臂 1",
                    "device_type": "ROBOTIC_ARM",
                    "role": "PLACEMENT_DEVICE",
                    "supported_commands": ["PICK"],
                    "supported_events": [],
                },
                "state": {
                    "device_code": device_code,
                    "mode": "AUTO",
                    "status": "IDLE",
                    "is_online": True,
                    "current_command_code": None,
                    "scenario": "success",
                    "updated_at": 1_786_579_200_000,
                },
            }
        )

    async def submit_command(self, **values):
        self.submitted.append(values)
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
        workline_id=11,
        endpoint_base_url="http://ecs-dispatch:8080",
        command_timeout_ms=30_000,
        status_max_age_ms=1_000,
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
    evidences = FakeEvidenceService()
    service = DeviceDispatchService(
        session_factory=FakeSessions(),  # type: ignore[arg-type]
        command_repository=FakeCommandRepository(command),  # type: ignore[arg-type]
        observation_repository=observations,  # type: ignore[arg-type]
        evidence_service=evidences,  # type: ignore[arg-type]
        adapter_provider=provider,  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 8, 13, 0, 0, 0, 500_000),
    )

    processed = await service.dispatch_one(now=datetime(2026, 8, 13, 0, 0, 0, 500_000))

    assert processed is True
    assert command.status == expected
    assert [item["command_code"] for item in adapter.submitted] == ["CMD-001"]
    assert len(observations.created) == 1
    assert provider.requested == ["http://ecs-dispatch:8080"]
    assert events == ["provider"]
    expected_observation = {
        EcsSubmitDisposition.CONTRACT_REJECTED: ("NOT_ACCEPTED", "ECS_CONTRACT_REJECTED"),
        EcsSubmitDisposition.RECONCILING: ("RESULT_UNKNOWN", "DELIVERY_UNKNOWN"),
    }.get(disposition)
    assert [(item["observation"], item["reason_code"]) for item in evidences.observations] == (
        [] if expected_observation is None else [expected_observation]
    )


@pytest.mark.asyncio
async def test_dispatch_publishes_observation_after_state_and_evidence_commit() -> None:
    command = _command()
    events: list[str] = []
    publisher = FakePublisher(events)
    service = DeviceDispatchService(
        session_factory=FakeSessions(events),  # type: ignore[arg-type]
        command_repository=FakeCommandRepository(command),  # type: ignore[arg-type]
        observation_repository=FakeObservationRepository(),  # type: ignore[arg-type]
        evidence_service=FakeEvidenceService(),  # type: ignore[arg-type]
        adapter_provider=FakeAdapterProvider(FakeAdapter(EcsSubmitResult(EcsSubmitDisposition.RECONCILING))),
        event_publisher=publisher,  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 8, 13, 0, 0, 0, 500_000),
    )

    assert await service.dispatch_one(now=datetime(2026, 8, 13, 0, 0, 0, 500_000)) is True

    assert events == ["commit", "commit", "commit", "publish"]
    channel, event_type, payload = publisher.published[0]
    assert channel == "device:evidence:stream"
    assert event_type == "device_evidence.updated"
    assert payload["observation"] == "RESULT_UNKNOWN"
    assert payload["reason_code"] == "DELIVERY_UNKNOWN"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state_updates", "observed_at"),
    [
        ({"is_online": False}, datetime(2026, 8, 13, 0, 0, 0, 500_000)),
        ({"mode": EcsDeviceMode.MANUAL}, datetime(2026, 8, 13, 0, 0, 0, 500_000)),
        ({"status": EcsDeviceState.RUNNING}, datetime(2026, 8, 13, 0, 0, 0, 500_000)),
        ({"current_command_code": "CMD-EARLIER"}, datetime(2026, 8, 13, 0, 0, 0, 500_000)),
        ({}, datetime(2026, 8, 13, 0, 0, 2)),
    ],
)
async def test_dynamic_device_status_keeps_business_command_pending_without_submit(
    state_updates: dict[str, object],
    observed_at: datetime,
) -> None:
    command = _command()
    adapter = FakeAdapter(EcsSubmitResult(EcsSubmitDisposition.ACKNOWLEDGED))

    async def busy_status(device_code: str) -> EcsDeviceStatus:
        status = await FakeAdapter.fetch_status(adapter, device_code)
        return status.model_copy(update={"state": status.state.model_copy(update=state_updates)})

    adapter.fetch_status = busy_status  # type: ignore[method-assign]
    service = DeviceDispatchService(
        session_factory=FakeSessions(),  # type: ignore[arg-type]
        command_repository=FakeCommandRepository(command),  # type: ignore[arg-type]
        observation_repository=FakeObservationRepository(),  # type: ignore[arg-type]
        evidence_service=FakeEvidenceService(),  # type: ignore[arg-type]
        adapter_provider=FakeAdapterProvider(adapter),  # type: ignore[arg-type]
        clock=lambda: observed_at,
    )

    assert await service.dispatch_one(now=datetime(2026, 8, 13)) is True
    assert command.status == CommandStatus.PENDING
    assert command.next_attempt_at == observed_at + timedelta(seconds=5)
    assert adapter.submitted == []


@pytest.mark.asyncio
async def test_manual_debug_dispatch_uses_frozen_command_endpoint_without_epoch_lookup() -> None:
    command = _command()
    command.execution_ref_type = "MANUAL_DEBUG"
    command.workline_id = None
    object.__setattr__(command, "endpoint_base_url", "http://ecs-mock:8080")
    object.__setattr__(command, "command_timeout_ms", 30_000)
    adapter = FakeAdapter(EcsSubmitResult(EcsSubmitDisposition.ACKNOWLEDGED))
    provider = FakeAdapterProvider(adapter)
    observations = FakeObservationRepository()
    service = DeviceDispatchService(
        session_factory=FakeSessions(),  # type: ignore[arg-type]
        command_repository=FakeCommandRepository(command),  # type: ignore[arg-type]
        observation_repository=observations,  # type: ignore[arg-type]
        evidence_service=FakeEvidenceService(),  # type: ignore[arg-type]
        adapter_provider=provider,  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 8, 13, 0, 0, 0, 500_000),
    )

    assert await service.dispatch_one(now=datetime(2026, 8, 13, 0, 0, 0, 500_000)) is True
    assert command.status == CommandStatus.ACKNOWLEDGED
    assert provider.requested == ["http://ecs-mock:8080"]
    assert adapter.status_requests == ["ARM-01"]
    assert observations.created == []
    assert adapter.submitted == [
        {
            "device_code": "ARM-01",
            "command_code": "CMD-001",
            "task_type": "PICK",
            "priority": 1,
            "timeout_ms": 30_000,
            "timestamp": 1_786_579_200_000,
            "params": {},
            "deadline_at": datetime(2026, 8, 13, 0, 1),
        }
    ]


@pytest.mark.asyncio
async def test_manual_debug_unsupported_task_is_failed_before_submit() -> None:
    command = _command()
    command.execution_ref_type = "MANUAL_DEBUG"
    command.workline_id = None
    object.__setattr__(command, "endpoint_base_url", "http://ecs-mock:8080")
    object.__setattr__(command, "command_timeout_ms", 30_000)
    adapter = FakeAdapter(EcsSubmitResult(EcsSubmitDisposition.ACKNOWLEDGED))

    async def unsupported_status(device_code: str) -> EcsDeviceStatus:
        status = await FakeAdapter.fetch_status(adapter, device_code)
        return status.model_copy(update={"device": status.device.model_copy(update={"supported_commands": ("MOVE",)})})

    adapter.fetch_status = unsupported_status  # type: ignore[method-assign]
    service = DeviceDispatchService(
        session_factory=FakeSessions(),  # type: ignore[arg-type]
        command_repository=FakeCommandRepository(command),  # type: ignore[arg-type]
        observation_repository=FakeObservationRepository(),  # type: ignore[arg-type]
        evidence_service=FakeEvidenceService(),  # type: ignore[arg-type]
        adapter_provider=FakeAdapterProvider(adapter),  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 8, 13, 0, 0, 0, 500_000),
    )

    assert await service.dispatch_one(now=datetime(2026, 8, 13)) is True
    assert command.status == CommandStatus.FAILED
    assert command.failure_code == "DEVICE_TASK_TYPE_UNSUPPORTED"
    assert adapter.submitted == []


@pytest.mark.asyncio
async def test_manual_debug_status_failure_is_retryable_without_submit() -> None:
    command = _command()
    command.execution_ref_type = "MANUAL_DEBUG"
    command.workline_id = None
    object.__setattr__(command, "endpoint_base_url", "http://ecs-mock:8080")
    object.__setattr__(command, "command_timeout_ms", 30_000)
    adapter = UnavailableStatusAdapter(EcsSubmitResult(EcsSubmitDisposition.ACKNOWLEDGED))
    service = DeviceDispatchService(
        session_factory=FakeSessions(),  # type: ignore[arg-type]
        command_repository=FakeCommandRepository(command),  # type: ignore[arg-type]
        observation_repository=FakeObservationRepository(),  # type: ignore[arg-type]
        evidence_service=FakeEvidenceService(),  # type: ignore[arg-type]
        adapter_provider=FakeAdapterProvider(adapter),  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 8, 13, 0, 0, 0, 500_000),
    )

    assert await service.dispatch_one(now=datetime(2026, 8, 13)) is True
    assert command.status == CommandStatus.PENDING
    assert adapter.submitted == []


@pytest.mark.asyncio
async def test_event_debug_status_failure_is_terminal_without_delayed_retry() -> None:
    command = _command()
    command.execution_ref_type = "EVENT_DEBUG"
    command.workline_id = None
    object.__setattr__(command, "endpoint_base_url", "http://10.24.209.26:8080")
    object.__setattr__(command, "command_timeout_ms", 30_000)
    adapter = UnavailableStatusAdapter(EcsSubmitResult(EcsSubmitDisposition.ACKNOWLEDGED))
    service = DeviceDispatchService(
        session_factory=FakeSessions(),  # type: ignore[arg-type]
        command_repository=FakeCommandRepository(command),  # type: ignore[arg-type]
        observation_repository=FakeObservationRepository(),  # type: ignore[arg-type]
        evidence_service=FakeEvidenceService(),  # type: ignore[arg-type]
        adapter_provider=FakeAdapterProvider(adapter),  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 8, 13, 0, 0, 0, 500_000),
    )

    assert await service.dispatch_one(now=datetime(2026, 8, 13)) is True
    assert command.status == CommandStatus.FAILED
    assert command.failure_code == "DEVICE_STATUS_UNAVAILABLE"
    assert adapter.submitted == []


@pytest.mark.asyncio
async def test_event_debug_retryable_rejection_is_terminal_without_delayed_retry() -> None:
    command = _command()
    command.execution_ref_type = "EVENT_DEBUG"
    command.workline_id = None
    object.__setattr__(command, "endpoint_base_url", "http://10.24.209.26:8080")
    object.__setattr__(command, "command_timeout_ms", 30_000)
    adapter = FakeAdapter(EcsSubmitResult(EcsSubmitDisposition.RETRYABLE_NOT_ACCEPTED))
    service = DeviceDispatchService(
        session_factory=FakeSessions(),  # type: ignore[arg-type]
        command_repository=FakeCommandRepository(command),  # type: ignore[arg-type]
        observation_repository=FakeObservationRepository(),  # type: ignore[arg-type]
        evidence_service=FakeEvidenceService(),  # type: ignore[arg-type]
        adapter_provider=FakeAdapterProvider(adapter),  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 8, 13, 0, 0, 0, 500_000),
    )

    assert await service.dispatch_one(now=datetime(2026, 8, 13)) is True
    assert command.status == CommandStatus.FAILED
    assert command.failure_code == "ECS_RETRYABLE_NOT_ACCEPTED"


@pytest.mark.asyncio
async def test_missing_or_invalid_binding_endpoint_never_reaches_http() -> None:
    for endpoint, provider_error in (
        (None, None),
        ("http://ecs-dispatch:8080", ValueError("invalid endpoint")),
    ):
        command = _command()
        command.endpoint_base_url = endpoint
        adapter = FakeAdapter(EcsSubmitResult(EcsSubmitDisposition.ACKNOWLEDGED))
        provider = FakeAdapterProvider(adapter, error=provider_error)
        service = DeviceDispatchService(
            session_factory=FakeSessions(),  # type: ignore[arg-type]
            command_repository=FakeCommandRepository(command),  # type: ignore[arg-type]
            observation_repository=FakeObservationRepository(),  # type: ignore[arg-type]
            evidence_service=FakeEvidenceService(),  # type: ignore[arg-type]
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
        observation_repository=FakeObservationRepository(),  # type: ignore[arg-type]
        evidence_service=FakeEvidenceService(),  # type: ignore[arg-type]
        adapter_provider=FakeAdapterProvider(adapter),  # type: ignore[arg-type]
    )

    assert await service.dispatch_one(now=datetime(2026, 8, 13)) is True
    assert command.status == CommandStatus.PENDING
    assert adapter.submitted == []


@pytest.mark.asyncio
async def test_command_crossing_deadline_during_status_probe_is_timed_out_before_submit() -> None:
    command = _command()
    adapter = FakeAdapter(EcsSubmitResult(EcsSubmitDisposition.ACKNOWLEDGED))
    evidences = FakeEvidenceService()
    service = DeviceDispatchService(
        session_factory=FakeSessions(),  # type: ignore[arg-type]
        command_repository=FakeCommandRepository(command),  # type: ignore[arg-type]
        observation_repository=FakeObservationRepository(),  # type: ignore[arg-type]
        evidence_service=evidences,  # type: ignore[arg-type]
        adapter_provider=FakeAdapterProvider(adapter),  # type: ignore[arg-type]
        clock=lambda: command.deadline_at,
    )

    assert await service.dispatch_one(now=command.deadline_at - timedelta(microseconds=1)) is True
    assert command.status == CommandStatus.TIMED_OUT
    assert adapter.submitted == []
    assert [(item["observation"], item["reason_code"]) for item in evidences.observations] == [
        ("NOT_ACCEPTED", "COMMAND_DEADLINE_EXPIRED")
    ]


@pytest.mark.asyncio
async def test_command_crossing_deadline_after_admission_is_timed_out_at_final_send_fence() -> None:
    command = _command()
    command.deadline_at = datetime(2026, 8, 13, 0, 0, 1)
    adapter = FakeAdapter(EcsSubmitResult(EcsSubmitDisposition.ACKNOWLEDGED))
    service = DeviceDispatchService(
        session_factory=FakeSessions(),  # type: ignore[arg-type]
        command_repository=FakeCommandRepository(command),  # type: ignore[arg-type]
        observation_repository=FakeObservationRepository(),  # type: ignore[arg-type]
        evidence_service=FakeEvidenceService(),  # type: ignore[arg-type]
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
        observation_repository=FakeObservationRepository(),  # type: ignore[arg-type]
        evidence_service=FakeEvidenceService(),  # type: ignore[arg-type]
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
        observation_repository=FakeObservationRepository(),  # type: ignore[arg-type]
        evidence_service=FakeEvidenceService(),  # type: ignore[arg-type]
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
    evidences = FakeEvidenceService()
    service = DeviceDispatchService(
        session_factory=FakeSessions(),
        command_repository=FakeCommandRepository(command),
        observation_repository=FakeObservationRepository(),
        evidence_service=evidences,  # type: ignore[arg-type]
        adapter_provider=FakeAdapterProvider(adapter),
        clock=iter(
            [datetime(2026, 8, 13, 0, 0, 0, 500_000), datetime(2026, 8, 13, 0, 0, 0, 750_000), response_at]
        ).__next__,
    )

    assert await service.dispatch_one(now=datetime(2026, 8, 13)) is True
    assert command.status == CommandStatus.RECONCILING
    assert command.reconciliation_reason == "ACK_AFTER_DEADLINE"
    assert command.ack_received_at == response_at
    assert [(item["observation"], item["reason_code"]) for item in evidences.observations] == [
        ("RESULT_UNKNOWN", "ACK_AFTER_DEADLINE")
    ]
