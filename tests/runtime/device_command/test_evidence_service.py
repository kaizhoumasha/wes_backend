"""Device evidence 接收与应用的可靠性边界。"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from src.app.device.contracts import (
    DeviceEvidenceReceipt,
    EcsCommandResultReport,
    EcsDeviceEvent,
    EcsDeviceEventReport,
)
from src.app.device.event_debug_contracts import EventDebugCommandReady
from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.device.services.device_evidence_service import (
    DeviceEventNotAdmittedError,
    DeviceEvidenceService,
    DeviceResultConflictError,
    DeviceResultOutOfOrderError,
)
from src.app.execution.models.inbound_evidence import (
    InboundEvidence,
    InboundEvidenceApplyStatus,
    InboundEvidenceConflict,
    InboundEvidenceKind,
)
from src.app.execution.services.inbound_evidence_service import InboundEvidenceService
from src.utils.timezone import timezone


class FakeBegin(AbstractAsyncContextManager[object]):
    def __init__(
        self,
        *,
        calls: list[str] | None = None,
        exit_error: Exception | None = None,
    ) -> None:
        self.calls = calls
        self.exit_error = exit_error

    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, exc_type: object, *_args: object) -> None:
        if exc_type is not None:
            if self.calls is not None:
                self.calls.append("rollback")
            return
        if self.exit_error is not None:
            if self.calls is not None:
                self.calls.append("rollback")
            raise self.exit_error
        if self.calls is not None:
            self.calls.append("commit")


class FakeSessionFactory:
    def __init__(
        self,
        *,
        calls: list[str] | None = None,
        fail_on_exit_number: int | None = None,
    ) -> None:
        self.calls = calls
        self.fail_on_exit_number = fail_on_exit_number
        self.begin_count = 0

    def begin(self) -> FakeBegin:
        self.begin_count += 1
        exit_error = RuntimeError("transaction commit failed") if self.begin_count == self.fail_on_exit_number else None
        return FakeBegin(calls=self.calls, exit_error=exit_error)


class FakeEvidenceRepository:
    def __init__(self) -> None:
        self.evidences: dict[str, InboundEvidence] = {}
        self.conflicts: list[InboundEvidenceConflict] = []
        self.identity_locks: list[str] = []
        self.next_id = 1

    async def lock_source_identity(self, _db: object, source_identity: str) -> None:
        self.identity_locks.append(source_identity)

    async def get_by_source_identity_for_update(self, _db: object, source_identity: str) -> InboundEvidence | None:
        return self.evidences.get(source_identity)

    async def get_device_result_for_command_for_update(self, _db: object, command_code: str) -> InboundEvidence | None:
        return next(
            (
                item
                for item in self.evidences.values()
                if item.command_code == command_code and getattr(item.kind, "value", item.kind) == "DEVICE_RESULT"
            ),
            None,
        )

    async def add(self, _db: object, evidence: InboundEvidence) -> InboundEvidence:
        evidence.id = self.next_id
        self.next_id += 1
        self.evidences[evidence.source_identity] = evidence
        return evidence

    async def add_conflict(self, _db: object, conflict: InboundEvidenceConflict) -> InboundEvidenceConflict:
        self.conflicts.append(conflict)
        return conflict

    async def claim_next_pending(
        self,
        _db: object,
        *,
        kinds: tuple[object, ...],
    ) -> InboundEvidence | None:
        return next(
            (item for item in self.evidences.values() if item.apply_status == "PENDING" and item.kind in kinds),
            None,
        )

    async def mark_applied(self, _db: object, evidence: InboundEvidence, *, processed_at: datetime) -> None:
        evidence.apply_status = "APPLIED"
        evidence.processed_at = processed_at

    async def mark_ignored(self, _db: object, evidence: InboundEvidence, *, processed_at: datetime) -> None:
        evidence.apply_status = "IGNORED"
        evidence.processed_at = processed_at

    async def mark_reconciling(self, _db: object, evidence: InboundEvidence, *, processed_at: datetime) -> None:
        evidence.apply_status = "RECONCILING"
        evidence.processed_at = processed_at


class FakeCommandRepository:
    def __init__(self, command: DeviceCommand | None) -> None:
        self.command = command
        self.creation_locks: list[str] = []

    async def lock_creation_for_device(self, _db: object, device_code: str) -> None:
        self.creation_locks.append(device_code)

    async def get_by_command_code(
        self,
        _db: object,
        command_code: str,
        *,
        for_update: bool = False,
    ) -> DeviceCommand | None:
        if self.command is None or self.command.command_code != command_code:
            return None
        return self.command


class FakeWorkLineRepository:
    def __init__(self, event_workline_id: int | None = None, *, workline_id: int = 7) -> None:
        self.event_workline_id = event_workline_id
        self.workline_id = workline_id

    async def get_active_binding_for_device(self, _db: object, device_code: str):
        if self.event_workline_id is None:
            return None
        return type(
            "Binding",
            (),
            {
                "workline_id": self.event_workline_id,
                "device_code": device_code,
                "contract_key": "arm.pick",
                "contract_version": "2.0",
            },
        )()

    async def get_by_id(self, _db: object, id: int):
        if self.event_workline_id != id:
            return None
        return type("WorkLine", (), {"id": id, "workline_id": self.workline_id})()


class FakeTaskQueue:
    def __init__(
        self,
        *,
        error: Exception | None = None,
        dispatch_error: Exception | None = None,
        calls: list[str] | None = None,
    ) -> None:
        self.device_evidence_wakes = 0
        self.transport_debug_wakes = 0
        self.execution_wakes = 0
        self.device_command_wakes = 0
        self.error = error
        self.dispatch_error = dispatch_error
        self.calls = calls

    def enqueue_device_evidence(self) -> None:
        self.device_evidence_wakes += 1

    def enqueue_transport_debug(self) -> None:
        self.transport_debug_wakes += 1

    def enqueue_execution_facts(self) -> None:
        self.execution_wakes += 1
        if self.error is not None:
            raise self.error

    def enqueue_device_commands(self) -> None:
        self.device_command_wakes += 1
        if self.calls is not None:
            self.calls.append("wake_device_commands")
        if self.dispatch_error is not None:
            raise self.dispatch_error


class FakeEventDebugCommandService:
    def __init__(self, *, outcome: EventDebugCommandReady | None = None) -> None:
        self.evidences: list[InboundEvidence] = []
        self.outcome = outcome

    async def create_event_debug_command_in_session(
        self,
        _db: object,
        *,
        evidence: InboundEvidence,
    ) -> EventDebugCommandReady:
        self.evidences.append(evidence)
        if self.outcome is not None:
            return self.outcome
        return EventDebugCommandReady(
            command_code="EVENT-DEBUG-CMD-001",
            status=CommandStatus.PENDING,
            created=len(self.evidences) == 1,
        )


class FakeEventDebugModePolicy:
    def __init__(self, enabled: bool, *, suppress_command: bool = False) -> None:
        self.enabled = enabled
        self.suppress_command = suppress_command
        self.calls: list[tuple[str, str]] = []
        self.suppression_calls: list[tuple[int | None, str]] = []

    async def is_event_debug_enabled_in_session(
        self,
        _db: object,
        *,
        event_type: str,
        device_code: str,
    ) -> bool:
        self.calls.append((event_type, device_code))
        return self.enabled

    async def should_suppress_event_debug_command_in_session(
        self,
        _db: object,
        *,
        workline_id: int | None,
        device_code: str,
    ) -> bool:
        self.suppression_calls.append((workline_id, device_code))
        return self.suppress_command


class FakePublisher:
    def __init__(self, *, error: Exception | None = None, calls: list[str] | None = None) -> None:
        self.events: list[tuple[str, str, dict[str, object]]] = []
        self.error = error
        self.calls = calls

    async def publish_to(self, channel: str, event_type: str, payload: dict[str, object]) -> bool:
        if self.calls is not None:
            self.calls.append("publish_update")
        self.events.append((channel, event_type, payload))
        if self.error is not None:
            raise self.error
        return True


def _command() -> DeviceCommand:
    now = datetime(2026, 8, 13)
    return DeviceCommand(
        id=31,
        command_code="CMD-001",
        device_code="ARM-01",
        workline_id=11,
        execution_ref_type="MATERIAL_EXECUTION",
        execution_ref_id="EXEC-001",
        material_execution_id=21,
        contract_key="arm.pick",
        contract_version="2.0",
        task_type="PICK",
        params={},
        payload_digest="a" * 64,
        deadline_at=datetime(2026, 8, 13, 0, 1),
        status=CommandStatus.ACKNOWLEDGED,
        created_at=now,
        updated_at=now,
    )


def _result(**overrides: object) -> EcsCommandResultReport:
    payload: dict[str, object] = {
        "command_code": "CMD-001",
        "device_code": "ARM-01",
        "result": "SUCCESS",
        "finish_time": 1_786_579_204_000,
        "data": {},
        "error_detail": None,
    }
    payload.update(overrides)
    return EcsCommandResultReport.model_validate(payload)


def _result_identity(report: EcsCommandResultReport) -> str:
    from src.utils.canonical_json import canonical_json_digest

    return f"RESULT:{canonical_json_digest(report.model_dump(mode='json'))}"


@pytest.mark.asyncio
async def test_duplicate_result_locks_exact_evidence_before_command(monkeypatch):
    command = _command()
    service, repository = _service(command)
    receipt = await service.accept_result(_result())
    calls = []
    evidence_lookup = repository.get_by_source_identity_for_update
    command_lookup = service._commands.get_by_command_code

    async def lock_evidence(db, identity):
        calls.append(("evidence", identity))
        return await evidence_lookup(db, identity)

    async def lock_command(db, code, **kwargs):
        assert calls == [("evidence", receipt.source_event_id)]
        calls.append(("command", code))
        return await command_lookup(db, code, **kwargs)

    monkeypatch.setattr(repository, "get_by_source_identity_for_update", lock_evidence)
    monkeypatch.setattr(service._commands, "get_by_command_code", lock_command)
    duplicate = await service.accept_result(_result())
    assert duplicate.duplicate is True
    assert len(repository.evidences) == 1


@pytest.mark.asyncio
async def test_registered_pre_dispatch_result_is_processed_once_without_business_advancement():
    command = _command()
    queue = FakeTaskQueue()
    service, repository = _service(command, task_queue=queue)
    receipt = await service.accept_result(_result())
    evidence = repository.evidences[receipt.source_event_id]
    command.status = CommandStatus.RECONCILING
    command.reconciliation_reason = "RESULT_BEFORE_DISPATCH"
    assert await service.process_one() is True
    assert evidence.apply_status == "RECONCILING"
    assert evidence.normalized_payload["result"] == "SUCCESS"
    assert command.result_evidence_id is None
    assert command.status == CommandStatus.RECONCILING
    assert queue.execution_wakes == 0
    assert await service.process_one() is False


def _event(**overrides: object) -> EcsDeviceEventReport:
    payload: dict[str, object] = {
        "device_code": "ARM-01",
        "event_type": "SCAN_COMPLETED",
        "timestamp": 1_786_579_204_000,
        "data": {"event_id": "EVENT-001", "location": "STATION_SCAN1", "barcode": "PKG12345678"},
    }
    payload.update(overrides)
    return EcsDeviceEventReport.model_validate(payload)


def _persisted_event_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "device_code": "ARM-01",
        "contract_key": "arm.pick",
        "contract_version": "2.0",
        "event_type": "ESTOP_PRESSED",
        "timestamp": 1_786_579_204_000,
        "source_event_id": "EVENT:legacy-estop",
        "is_debug": False,
        "data": {},
    }
    payload.update(overrides)
    return payload


def test_persisted_device_event_contract_rejects_retired_estop() -> None:
    with pytest.raises(ValidationError, match="ECS-owned ESTOP"):
        EcsDeviceEvent.model_validate(_persisted_event_payload())


def _service(
    command: DeviceCommand | None,
    *,
    event_workline_id: int | None = 11,
    task_queue: FakeTaskQueue | None = None,
    publisher: FakePublisher | None = None,
    event_debug_commands: FakeEventDebugCommandService | None = None,
    command_repository: FakeCommandRepository | None = None,
    session_factory: FakeSessionFactory | None = None,
    event_debug_mode_policy: FakeEventDebugModePolicy | None = None,
) -> tuple[DeviceEvidenceService, FakeEvidenceRepository]:
    evidences = FakeEvidenceRepository()
    return (
        DeviceEvidenceService(
            session_factory=session_factory or FakeSessionFactory(),  # type: ignore[arg-type]
            inbound_evidence_service=InboundEvidenceService(repository=evidences),
            processing_repository=evidences,  # type: ignore[arg-type]
            command_repository=command_repository or FakeCommandRepository(command),  # type: ignore[arg-type]
            workline_repository=FakeWorkLineRepository(event_workline_id),  # type: ignore[arg-type]
            task_queue_gateway=task_queue,  # type: ignore[arg-type]
            event_publisher=publisher,  # type: ignore[arg-type]
            event_debug_command_service=event_debug_commands,  # type: ignore[arg-type]
            event_debug_mode_policy=event_debug_mode_policy,  # type: ignore[arg-type]
        ),
        evidences,
    )


@pytest.mark.asyncio
async def test_result_is_persisted_before_receipt_and_duplicate_is_idempotent() -> None:
    service, repository = _service(_command())

    first = await service.accept_result(_result())
    duplicate = await service.accept_result(_result())

    assert first.evidence_id == duplicate.evidence_id
    assert first.duplicate is False
    assert duplicate.duplicate is True
    assert list(repository.evidences) == [_result_identity(_result())]
    assert repository.identity_locks == ["device-result:CMD-001", _result_identity(_result()), "device-result:CMD-001"]
    assert repository.evidences[_result_identity(_result())].contract_key == "arm.pick"
    assert repository.evidences[_result_identity(_result())].contract_version == "2.0"
    assert repository.evidences[_result_identity(_result())].normalized_payload["finish_time"] == 1_786_579_204_000


@pytest.mark.asyncio
async def test_ingress_writes_only_through_inbound_evidence_application() -> None:
    application_repository = FakeEvidenceRepository()
    processing_repository = FakeEvidenceRepository()
    service = DeviceEvidenceService(
        session_factory=FakeSessionFactory(),  # type: ignore[arg-type]
        inbound_evidence_service=InboundEvidenceService(repository=application_repository),
        processing_repository=processing_repository,  # type: ignore[arg-type]
        command_repository=FakeCommandRepository(_command()),  # type: ignore[arg-type]
        workline_repository=FakeWorkLineRepository(),  # type: ignore[arg-type]
    )

    receipt = await service.accept_result(_result())

    assert receipt.evidence_id == application_repository.evidences[_result_identity(_result())].id
    assert processing_repository.evidences == {}


@pytest.mark.asyncio
async def test_different_result_messages_for_one_command_are_retained() -> None:
    service, repository = _service(_command())
    await service.accept_result(_result())

    await service.accept_result(_result(data={"position": "OTHER"}))
    assert len(repository.evidences) == 2
    assert repository.conflicts == []


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", [CommandStatus.SUCCEEDED, CommandStatus.FAILED, CommandStatus.TIMED_OUT])
async def test_later_unordered_result_does_not_replace_terminal_or_wake_business(terminal) -> None:
    command = _command()
    command.status = terminal
    command.result_evidence_id = 99
    queue = FakeTaskQueue()
    service, repository = _service(command, task_queue=queue)
    await service.accept_result(_result())
    await service.accept_result(_result(result="FAILED", error_detail={"code": "FAIL", "msg": "failure"}))
    assert await service.process_one() is True
    assert await service.process_one() is True
    assert await service.process_one() is False
    assert command.status == terminal
    assert command.result_evidence_id == 99
    assert all(item.apply_status == "IGNORED" for item in repository.evidences.values())
    assert queue.execution_wakes == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("command_code", ["CMD-001", "C" * 160])
async def test_unknown_command_is_durably_retained_without_active_retry(command_code: str) -> None:
    queue = FakeTaskQueue()
    service, repository = _service(None, task_queue=queue)

    receipt = await service.accept_result(_result(command_code=command_code))
    evidence = repository.evidences[receipt.source_event_id]
    assert evidence.command_code == command_code
    assert evidence.workline_id is None
    assert evidence.material_execution_id is None
    assert evidence.apply_status == "IGNORED"
    assert await service.process_one() is False
    assert queue.device_evidence_wakes == 0
    assert queue.execution_wakes == 0


@pytest.mark.asyncio
async def test_unknown_command_duplicate_preserves_original_evidence_after_command_appears() -> None:
    service, repository = _service(None)
    first = await service.accept_result(_result())

    service._commands.command = _command()
    receipt = await service.accept_result(_result())

    accepted = repository.evidences[receipt.source_event_id]
    assert receipt.duplicate is True
    assert receipt.evidence_id == first.evidence_id
    assert accepted.apply_status == "IGNORED"
    assert accepted.command_code == "CMD-001"
    assert accepted.workline_id is None


@pytest.mark.asyncio
async def test_result_identity_mismatch_is_frozen_before_rejection() -> None:
    service, repository = _service(_command())

    with pytest.raises(DeviceResultConflictError) as mismatch:
        await service.accept_result(_result(device_code="ARM-OTHER"))

    rejected = repository.evidences[_result_identity(_result(device_code="ARM-OTHER"))]
    assert rejected.apply_status == "IGNORED"
    assert rejected.workline_id == 11
    assert mismatch.value.receipt == DeviceEvidenceReceipt(
        evidence_id=rejected.id,
        source_event_id=rejected.source_identity,
        duplicate=False,
        trace_id=None,
        apply_status="IGNORED",
    )
    with pytest.raises(DeviceResultConflictError) as conflict:
        await service.accept_result(_result(device_code="ARM-THIRD"))
    assert conflict.value.receipt.evidence_id != rejected.id
    assert conflict.value.receipt.source_event_id == _result_identity(_result(device_code="ARM-THIRD"))
    assert conflict.value.receipt.apply_status == "IGNORED"


@pytest.mark.asyncio
async def test_result_before_dispatch_fences_command_and_is_rejected() -> None:
    command = _command()
    command.status = CommandStatus.PENDING
    service, repository = _service(command)

    with pytest.raises(DeviceResultOutOfOrderError) as out_of_order:
        await service.accept_result(_result())

    rejected = repository.evidences[_result_identity(_result())]
    assert command.status == CommandStatus.RECONCILING
    assert command.reconciliation_reason == "RESULT_BEFORE_DISPATCH"
    assert rejected.apply_status == "IGNORED"
    assert rejected.command_code is None
    assert out_of_order.value.receipt == DeviceEvidenceReceipt(
        evidence_id=rejected.id,
        source_event_id=_result_identity(_result()),
        duplicate=False,
        trace_id=None,
        apply_status="IGNORED",
    )

    with pytest.raises(DeviceResultOutOfOrderError) as duplicate:
        await service.accept_result(_result())

    assert duplicate.value.receipt == DeviceEvidenceReceipt(
        evidence_id=rejected.id,
        source_event_id=_result_identity(_result()),
        duplicate=True,
        trace_id=None,
        apply_status="IGNORED",
    )
    assert len(repository.evidences) == 1


@pytest.mark.asyncio
async def test_new_unbound_business_event_is_recorded_and_rejected() -> None:
    service, repository = _service(None, event_workline_id=None)
    with pytest.raises(DeviceEventNotAdmittedError):
        await service.accept_event(_event())
    evidence = next(iter(repository.evidences.values()))
    assert evidence.workline_id is None
    assert evidence.apply_status == InboundEvidenceApplyStatus.IGNORED


@pytest.mark.asyncio
async def test_debug_flag_changes_event_identity() -> None:
    service, repository = _service(None)

    normal = await service.accept_event(_event())
    explicit_false = await service.accept_event(_event(is_debug=False))
    debug = await service.accept_event(_event(is_debug=True))

    assert explicit_false.source_event_id == normal.source_event_id
    assert explicit_false.duplicate is True
    assert debug.source_event_id != normal.source_event_id
    assert repository.evidences[debug.source_event_id].normalized_payload["is_debug"] is True


@pytest.mark.asyncio
async def test_debug_event_creates_command_without_waking_business_processing() -> None:
    queue = FakeTaskQueue()
    publisher = FakePublisher()
    debug_commands = FakeEventDebugCommandService()
    service, repository = _service(
        None,
        event_workline_id=11,
        task_queue=queue,
        publisher=publisher,
        event_debug_commands=debug_commands,
    )
    receipt = await service.accept_event(_event(is_debug=True))

    assert await service.process_one() is True

    evidence = repository.evidences[receipt.source_event_id]
    assert evidence.apply_status == "IGNORED"
    assert evidence.workline_id == 11
    assert debug_commands.evidences == [evidence]
    assert queue.execution_wakes == 0
    assert queue.device_command_wakes == 1
    assert publisher.events[0][2]["command_code"] == "EVENT-DEBUG-CMD-001"


@pytest.mark.asyncio
async def test_manual_outbound_run_ignores_debug_event_without_creating_command() -> None:
    queue = FakeTaskQueue()
    debug_commands = FakeEventDebugCommandService()
    policy = FakeEventDebugModePolicy(enabled=False, suppress_command=True)
    commands = FakeCommandRepository(None)
    service, repository = _service(
        None,
        event_workline_id=11,
        task_queue=queue,
        event_debug_commands=debug_commands,
        event_debug_mode_policy=policy,
        command_repository=commands,
    )
    receipt = await service.accept_event(_event(is_debug=True))

    assert await service.process_one() is True

    evidence = repository.evidences[receipt.source_event_id]
    assert evidence.apply_status == "IGNORED"
    assert debug_commands.evidences == []
    assert queue.device_command_wakes == 0
    assert commands.creation_locks == ["ARM-01"]
    assert policy.suppression_calls == [(11, "ARM-01")]


@pytest.mark.asyncio
async def test_unbound_debug_event_still_uses_device_identity_for_manual_run_suppression() -> None:
    commands = FakeCommandRepository(None)
    debug_commands = FakeEventDebugCommandService()
    policy = FakeEventDebugModePolicy(enabled=False, suppress_command=True)
    service, repository = _service(
        None,
        event_workline_id=None,
        event_debug_commands=debug_commands,
        event_debug_mode_policy=policy,
        command_repository=commands,
    )
    receipt = await service.accept_event(_event(is_debug=True))

    assert await service.process_one() is True

    assert repository.evidences[receipt.source_event_id].apply_status == "IGNORED"
    assert commands.creation_locks == ["ARM-01"]
    assert policy.suppression_calls == [(None, "ARM-01")]
    assert debug_commands.evidences == []


@pytest.mark.asyncio
async def test_transport_test_mode_promotes_unbound_station_scan_to_existing_debug_command_flow() -> None:
    queue = FakeTaskQueue()
    debug_commands = FakeEventDebugCommandService()
    policy = FakeEventDebugModePolicy(enabled=True)
    service, repository = _service(
        None,
        event_workline_id=None,
        task_queue=queue,
        event_debug_commands=debug_commands,
        event_debug_mode_policy=policy,
    )

    receipt = await service.accept_event(_event(device_code="STATION_SCAN11"))
    assert await service.process_one() is True

    evidence = repository.evidences[receipt.source_event_id]
    assert evidence.normalized_payload["is_debug"] is True
    assert evidence.workline_id is None
    assert evidence.apply_status == "IGNORED"
    assert debug_commands.evidences == [evidence]
    assert queue.device_command_wakes == 1
    assert policy.calls == [("SCAN_COMPLETED", "STATION_SCAN11")]


@pytest.mark.asyncio
async def test_debug_event_dispatch_wake_failure_does_not_change_committed_state() -> None:
    queue = FakeTaskQueue(dispatch_error=RuntimeError("broker unavailable"))
    publisher = FakePublisher()
    debug_commands = FakeEventDebugCommandService()
    service, repository = _service(
        None,
        task_queue=queue,
        publisher=publisher,
        event_debug_commands=debug_commands,
    )
    receipt = await service.accept_event(_event(is_debug=True))

    assert await service.process_one() is True

    evidence = repository.evidences[receipt.source_event_id]
    assert evidence.apply_status == "IGNORED"
    assert queue.device_command_wakes == 1
    assert publisher.events[0][2]["command_code"] == "EVENT-DEBUG-CMD-001"


@pytest.mark.asyncio
async def test_new_debug_command_is_woken_before_best_effort_evidence_publish() -> None:
    calls: list[str] = []
    transactions = FakeSessionFactory(calls=calls)
    queue = FakeTaskQueue(calls=calls)
    publisher = FakePublisher(calls=calls)
    service, _repository = _service(
        None,
        task_queue=queue,
        publisher=publisher,
        event_debug_commands=FakeEventDebugCommandService(),
        session_factory=transactions,
    )
    await service.accept_event(_event(is_debug=True))
    calls.clear()

    assert await service.process_one() is True

    assert calls == ["commit", "wake_device_commands", "publish_update"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    [
        EventDebugCommandReady(
            command_code="EVENT-DEBUG-CMD-001",
            status=CommandStatus.PENDING,
            created=False,
        ),
        EventDebugCommandReady(
            command_code="EVENT-DEBUG-CMD-001",
            status=CommandStatus.ACKNOWLEDGED,
            created=True,
        ),
    ],
)
async def test_reused_or_non_pending_debug_command_does_not_wake_dispatch(
    outcome: EventDebugCommandReady,
) -> None:
    queue = FakeTaskQueue()
    service, _repository = _service(
        None,
        task_queue=queue,
        event_debug_commands=FakeEventDebugCommandService(outcome=outcome),
    )
    await service.accept_event(_event(is_debug=True))

    assert await service.process_one() is True

    assert queue.device_command_wakes == 0


@pytest.mark.asyncio
async def test_debug_command_transaction_rollback_does_not_wake_or_publish() -> None:
    calls: list[str] = []
    transactions = FakeSessionFactory(calls=calls, fail_on_exit_number=2)
    queue = FakeTaskQueue(calls=calls)
    publisher = FakePublisher(calls=calls)
    service, _repository = _service(
        None,
        task_queue=queue,
        publisher=publisher,
        event_debug_commands=FakeEventDebugCommandService(),
        session_factory=transactions,
    )
    await service.accept_event(_event(is_debug=True))
    calls.clear()

    with pytest.raises(RuntimeError, match="transaction commit failed"):
        await service.process_one()

    assert calls == ["rollback"]
    assert queue.device_command_wakes == 0
    assert publisher.events == []


@pytest.mark.asyncio
async def test_normal_event_still_wakes_business_processing() -> None:
    queue = FakeTaskQueue()
    debug_commands = FakeEventDebugCommandService()
    service, repository = _service(None, task_queue=queue, event_debug_commands=debug_commands)
    receipt = await service.accept_event(_event())

    assert await service.process_one() is True

    assert repository.evidences[receipt.source_event_id].apply_status == "APPLIED"
    assert debug_commands.evidences == []
    assert queue.execution_wakes == 1


@pytest.mark.asyncio
async def test_event_freezes_active_epoch_when_contract_matches() -> None:
    service, repository = _service(None, event_workline_id=11)

    receipt = await service.accept_event(_event())

    assert repository.evidences[receipt.source_event_id].workline_id == 11


@pytest.mark.asyncio
async def test_accepted_event_retry_after_epoch_switch_reuses_frozen_evidence() -> None:
    service, repository = _service(None, event_workline_id=11)
    first = await service.accept_event(_event())

    service._worklines.event_workline_id = 12
    duplicate = await service.accept_event(_event())

    assert duplicate.evidence_id == first.evidence_id
    assert duplicate.duplicate is True
    assert repository.evidences[duplicate.source_event_id].workline_id == 11
    assert repository.conflicts == []


@pytest.mark.asyncio
async def test_processed_debug_event_retry_after_epoch_switch_reuses_frozen_evidence() -> None:
    epochs = FakeWorkLineRepository(event_workline_id=11)
    evidences = FakeEvidenceRepository()
    debug_commands = FakeEventDebugCommandService()
    service = DeviceEvidenceService(
        session_factory=FakeSessionFactory(),  # type: ignore[arg-type]
        inbound_evidence_service=InboundEvidenceService(repository=evidences),
        processing_repository=evidences,  # type: ignore[arg-type]
        command_repository=FakeCommandRepository(None),  # type: ignore[arg-type]
        workline_repository=epochs,  # type: ignore[arg-type]
        event_debug_command_service=debug_commands,
    )
    event = _event(is_debug=True)
    first = await service.accept_event(event)
    assert await service.process_one() is True

    epochs.event_workline_id = 12
    duplicate = await service.accept_event(event)

    assert duplicate.evidence_id == first.evidence_id
    assert duplicate.duplicate is True
    assert evidences.evidences[first.source_event_id].workline_id == 11
    assert evidences.conflicts == []
    assert len(debug_commands.evidences) == 1


@pytest.mark.asyncio
async def test_event_contract_metadata_uses_active_wes_binding() -> None:
    service, repository = _service(None, event_workline_id=11)

    receipt = await service.accept_event(_event())

    accepted = repository.evidences[receipt.source_event_id]
    assert accepted.apply_status == "PENDING"
    assert accepted.workline_id == 11
    assert accepted.contract_key == "arm.pick"
    assert accepted.contract_version == "2.0"


@pytest.mark.asyncio
async def test_same_event_payload_remains_idempotent_after_original_epoch_closes() -> None:
    epochs = FakeWorkLineRepository(event_workline_id=11)
    evidences = FakeEvidenceRepository()
    service = DeviceEvidenceService(
        session_factory=FakeSessionFactory(),  # type: ignore[arg-type]
        inbound_evidence_service=InboundEvidenceService(repository=evidences),
        processing_repository=evidences,  # type: ignore[arg-type]
        command_repository=FakeCommandRepository(None),  # type: ignore[arg-type]
        workline_repository=epochs,  # type: ignore[arg-type]
    )
    event = _event()
    first = await service.accept_event(event)

    epochs.event_workline_id = None
    duplicate = await service.accept_event(event)

    assert duplicate.evidence_id == first.evidence_id
    assert duplicate.duplicate is True
    assert evidences.evidences[first.source_event_id].workline_id == 11
    assert evidences.evidences[first.source_event_id].contract_key == "arm.pick"
    assert evidences.evidences[first.source_event_id].contract_version == "2.0"


@pytest.mark.asyncio
async def test_result_evidence_is_only_authority_that_closes_acknowledged_command() -> None:
    command = _command()
    queue = FakeTaskQueue()
    service, repository = _service(command, task_queue=queue)
    receipt = await service.accept_result(_result())

    assert command.status == CommandStatus.ACKNOWLEDGED
    assert await service.process_one() is True
    assert command.status == CommandStatus.SUCCEEDED
    assert command.result_evidence_id == receipt.evidence_id
    persisted_evidence = repository.evidences[receipt.source_event_id]
    assert persisted_evidence.material_execution_id == command.material_execution_id
    assert persisted_evidence.apply_status == "APPLIED"
    assert queue.execution_wakes == 1


@pytest.mark.asyncio
async def test_applied_evidence_update_is_published_after_processing() -> None:
    command = _command()
    publisher = FakePublisher()
    service, repository = _service(command, publisher=publisher)
    receipt = await service.accept_result(_result())

    assert await service.process_one() is True

    assert repository.evidences[receipt.source_event_id].apply_status == "APPLIED"
    channel, event_type, payload = publisher.events[0]
    assert (channel, event_type) == ("device:evidence:stream", "device_evidence.updated")
    assert payload == {
        "evidence_id": receipt.evidence_id,
        "kind": "DEVICE_RESULT",
        "source_event_id": receipt.source_event_id,
        "device_code": "ARM-01",
        "command_code": "CMD-001",
        "event_type": None,
        "observation": None,
        "reason_code": None,
        "observed_at": None,
        "apply_status": "APPLIED",
        "processed_at": timezone.to_utc(repository.evidences[receipt.source_event_id].processed_at).isoformat(),
    }


@pytest.mark.asyncio
async def test_evidence_update_publish_failure_does_not_rollback_processing() -> None:
    command = _command()
    publisher = FakePublisher(error=RuntimeError("redis down"))
    service, repository = _service(command, publisher=publisher)
    receipt = await service.accept_result(_result())

    assert await service.process_one() is True
    assert repository.evidences[receipt.source_event_id].apply_status == "APPLIED"
    assert command.status == CommandStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_failed_result_normalizes_whitepaper_error_and_closes_failed() -> None:
    command = _command()
    service, repository = _service(command)

    receipt = await service.accept_result(
        _result(
            result="FAILED",
            error_detail={"code": "E-MOTOR-01", "msg": "Servo motor timeout"},
        )
    )

    normalized = repository.evidences[receipt.source_event_id].normalized_payload
    assert normalized["error_detail"] == {"code": "E-MOTOR-01", "message": "Servo motor timeout"}
    assert await service.process_one() is True
    assert command.status == CommandStatus.FAILED
    assert command.failure_code == "DEVICE_REPORTED_FAILURE"


@pytest.mark.asyncio
async def test_execution_wake_failure_does_not_rollback_applied_device_evidence() -> None:
    command = _command()
    queue = FakeTaskQueue(error=RuntimeError("queue unavailable"))
    service, repository = _service(command, task_queue=queue)
    receipt = await service.accept_result(_result())

    assert await service.process_one() is True

    assert repository.evidences[receipt.source_event_id].apply_status == "APPLIED"
    assert command.status == CommandStatus.SUCCEEDED
    assert queue.execution_wakes == 1


@pytest.mark.asyncio
async def test_foundation_result_closes_command_and_stays_applied_without_business_identity() -> None:
    command = _command()
    command.material_execution_id = None
    queue = FakeTaskQueue()
    service, repository = _service(command, task_queue=queue)
    receipt = await service.accept_result(_result())

    assert await service.process_one() is True

    evidence = repository.evidences[receipt.source_event_id]
    assert command.status == CommandStatus.SUCCEEDED
    assert evidence.apply_status == "APPLIED"
    assert evidence.material_execution_id is None
    assert evidence.published_at is None
    assert evidence.decision_digest is None
    assert queue.execution_wakes == 0


@pytest.mark.asyncio
async def test_result_without_optional_fields_keeps_omission_through_async_apply() -> None:
    command = _command()
    service, repository = _service(command)
    result = _result()

    receipt = await service.accept_result(result)

    assert "trace_id" not in repository.evidences[receipt.source_event_id].normalized_payload
    assert await service.process_one() is True
    assert command.status == CommandStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_result_evidence_can_close_dispatching_command_before_ack_writeback() -> None:
    command = _command()
    command.status = CommandStatus.DISPATCHING
    command.claim_token = "dispatch-claim"
    command.claimed_at = datetime(2026, 8, 13)
    command.claim_expires_at = datetime(2026, 8, 13, 0, 0, 30)
    service, repository = _service(command)
    receipt = await service.accept_result(_result())

    assert await service.process_one() is True
    assert command.status == CommandStatus.SUCCEEDED
    assert command.result_evidence_id == receipt.evidence_id
    assert command.claim_token is None
    assert command.claimed_at is None
    assert command.claim_expires_at is None
    assert repository.evidences[receipt.source_event_id].apply_status == "APPLIED"


@pytest.mark.asyncio
async def test_result_for_terminal_command_is_retained_without_flipping_terminal() -> None:
    command = _command()
    command.status = CommandStatus.SUCCEEDED
    service, repository = _service(command)
    receipt = await service.accept_result(_result())

    assert await service.process_one() is True
    assert command.status == CommandStatus.SUCCEEDED
    assert repository.evidences[receipt.source_event_id].apply_status == "IGNORED"


@pytest.mark.asyncio
async def test_ignored_late_evidence_update_is_published_after_processing() -> None:
    command = _command()
    command.status = CommandStatus.SUCCEEDED
    publisher = FakePublisher()
    service, repository = _service(command, publisher=publisher)
    receipt = await service.accept_result(_result())

    assert await service.process_one() is True

    assert repository.evidences[receipt.source_event_id].apply_status == "IGNORED"
    assert publisher.events[0][1] == "device_evidence.updated"
    assert publisher.events[0][2]["apply_status"] == "IGNORED"


@pytest.mark.asyncio
async def test_device_evidence_worker_does_not_claim_wms_evidence() -> None:
    service, repository = _service(_command())
    repository.evidences["WMS-1"] = InboundEvidence(
        id=99,
        kind=InboundEvidenceKind.WMS_RESULT,
        source_identity="op:WMS-1",
        payload_digest="a" * 64,
        normalized_payload={"data": {}},
        received_at=datetime(2026, 8, 17),
        material_execution_id=21,
        operation="op",
        operation_id="WMS-1",
    )

    assert await service.process_one() is False
    assert repository.evidences["WMS-1"].apply_status == "PENDING"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "persisted_payload",
    [
        _persisted_event_payload(),
        _persisted_event_payload(is_debug=True),
        [],
        "invalid-event-payload",
        None,
    ],
    ids=("dict", "debug-dict", "list", "scalar", "null"),
)
async def test_worker_fail_closes_invalid_persisted_event_without_any_wake(persisted_payload: object) -> None:
    queue = FakeTaskQueue()
    publisher = FakePublisher()
    service, repository = _service(None, task_queue=queue, publisher=publisher)
    evidence = InboundEvidence(
        id=99,
        kind=InboundEvidenceKind.DEVICE_EVENT,
        source_identity="EVENT:legacy-estop",
        payload_digest="a" * 64,
        normalized_payload={},
        received_at=datetime(2026, 9, 12),
        device_code="ARM-01",
        workline_id=11,
    )
    object.__setattr__(evidence, "normalized_payload", persisted_payload)
    repository.evidences[evidence.source_identity] = evidence

    assert await service.process_one() is True

    assert evidence.apply_status == "IGNORED"
    assert queue.device_evidence_wakes == 0
    assert queue.device_command_wakes == 0
    assert queue.execution_wakes == 0
    assert queue.transport_debug_wakes == 0
    assert publisher.events[0][2]["event_type"] is None or isinstance(persisted_payload, dict)
    assert await service.process_one() is False


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [False, True])
async def test_device_ingress_wakes_processor_after_real_transaction_commit(result):
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from src.core import transaction_wakeup

    service, _ = _service(_command())
    service._sessions = async_sessionmaker()
    service._task_queue = FakeTaskQueue()
    if result:
        await service.accept_result(_result())
    else:
        await service.accept_event(_event(is_debug=True))
    await asyncio.gather(*tuple(transaction_wakeup._pending))
    assert service._task_queue.device_evidence_wakes == 1
