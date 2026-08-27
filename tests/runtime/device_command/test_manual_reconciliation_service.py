"""DELIVERY_UNKNOWN 人工闭合的封闭安全门禁。"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.app.device.contracts import EcsDeviceStatus
from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.device.models.event_command_block import DeviceEventCommandBlockStatus
from src.app.device.services.device_command_admission import DeviceCommandAdmissionError
from src.app.device.services.device_command_service import (
    DeviceCommandManualReconciliationConflictError,
    DeviceCommandManualReconciliationNotFoundError,
    DeviceCommandService,
)

NOW = datetime(2026, 8, 27, 12, 0, 0)
_DEFAULT_BINDING = object()


class FakeBegin(AbstractAsyncContextManager[object]):
    def __init__(self, command: DeviceCommand) -> None:
        self.command = command
        self.snapshot: tuple[object, ...] | None = None

    async def __aenter__(self) -> object:
        self.snapshot = (self.command.status, self.command.failure_code, self.command.completed_at)
        return object()

    async def __aexit__(self, exc_type, *_args: object) -> None:
        if exc_type is not None and self.snapshot is not None:
            self.command.status, self.command.failure_code, self.command.completed_at = self.snapshot


class FakeSessions:
    def __init__(self, command: DeviceCommand) -> None:
        self.command = command
        self.begin_count = 0

    def begin(self) -> FakeBegin:
        self.begin_count += 1
        return FakeBegin(self.command)


class FakeCommandRepository:
    def __init__(self, command: DeviceCommand | None) -> None:
        self.command = command
        self.locks: list[str] = []

    async def get_by_command_code(self, _db: object, command_code: str, *, for_update: bool = False):
        if for_update:
            self.locks.append("command")
        if self.command is None or self.command.command_code != command_code:
            return None
        return self.command

    async def lock_creation_for_device(self, _db: object, device_code: str) -> None:
        self.locks.append(f"device:{device_code}")


class FakeEvidenceRepository:
    def __init__(self, evidence: object | None, *, result: object | None = None) -> None:
        self.evidence = evidence
        self.result = result
        self.result_reads = 0

    async def get_by_source_identity_for_update(self, _db: object, source_identity: str):
        if self.evidence is None or self.evidence.source_identity != source_identity:
            return None
        return self.evidence

    async def get_device_result_for_command(self, _db: object, _command_code: str):
        self.result_reads += 1
        return self.result


class FakeBlockRepository:
    def __init__(self, block: object | None, *, latest: object | None = None) -> None:
        self.block = block
        self.latest = block if latest is None else latest

    async def get_by_id_for_update(self, _db: object, *, block_id: int, evidence_id: int):
        if self.block is None or self.block.id != block_id or self.block.evidence_id != evidence_id:
            return None
        return self.block

    async def get_latest_for_evidence(self, _db: object, *, evidence_id: int):
        if self.latest is None or self.latest.evidence_id != evidence_id:
            return None
        return self.latest


class FakeEpochRepository:
    def __init__(self, binding: object | None) -> None:
        self.binding = binding

    async def get_binding_for_dispatch(self, _db: object, *, line_run_epoch_id: int, device_code: str):
        if self.binding is None:
            return None
        assert self.binding.line_run_epoch_id == line_run_epoch_id
        assert self.binding.device_code == device_code
        return self.binding


class FakeAdapter:
    def __init__(self, status: EcsDeviceStatus, *, before_return=None, error: Exception | None = None) -> None:
        self.status = status
        self.before_return = before_return
        self.error = error
        self.calls = 0

    async def fetch_status(self, _device_code: str) -> EcsDeviceStatus:
        self.calls += 1
        if self.before_return is not None:
            self.before_return()
        if self.error is not None:
            raise self.error
        return self.status


class FakeAdapterProvider:
    def __init__(self, adapter: FakeAdapter) -> None:
        self.adapter = adapter

    async def get_adapter(self, _endpoint: str) -> FakeAdapter:
        return self.adapter


class FakeAuditService:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def create_audit_log(self, _db: object, **values: object) -> object:
        if self.error is not None:
            raise self.error
        self.calls.append(values)
        return object()


def _command() -> DeviceCommand:
    return DeviceCommand(
        id=31,
        command_code="CMD-DELIVERY-UNKNOWN-001",
        device_code="ARM-01",
        line_run_epoch_id=11,
        device_binding_id=21,
        execution_ref_type="MATERIAL_EXECUTION",
        execution_ref_id="EXEC-001",
        material_execution_id=None,
        contract_key="arm.pick",
        contract_version="2.0",
        task_type="PICK",
        params={},
        payload_digest="a" * 64,
        deadline_at=NOW - timedelta(seconds=1),
        status=CommandStatus.RECONCILING,
        reconciliation_reason="DELIVERY_UNKNOWN",
        created_at=NOW - timedelta(minutes=1),
        version=7,
    )


def _status(*, updated_at: int | None = None) -> EcsDeviceStatus:
    return EcsDeviceStatus.model_validate(
        {
            "device": {
                "device_code": "ARM-01",
                "device_name": "机械臂 1",
                "device_type": "ROBOTIC_ARM",
                "role": "PLACEMENT_DEVICE",
                "supported_commands": ["PICK"],
                "supported_events": [],
            },
            "state": {
                "device_code": "ARM-01",
                "mode": "AUTO",
                "status": "IDLE",
                "is_online": True,
                "current_command_code": None,
                "scenario": "success",
                "updated_at": (
                    updated_at if updated_at is not None else int(NOW.replace(tzinfo=UTC).timestamp() * 1000)
                ),
            },
        }
    )


def _service(
    *,
    command: DeviceCommand | None = None,
    evidence: object | None = None,
    block: object | None = None,
    latest: object | None = None,
    result: object | None = None,
    adapter: FakeAdapter | None = None,
    binding: object | None = _DEFAULT_BINDING,
    audit: FakeAuditService | None = None,
):
    actual_command = command or _command()
    actual_evidence = evidence or SimpleNamespace(id=41, source_identity="EVENT-001")
    actual_block = block or SimpleNamespace(
        id=51,
        evidence_id=41,
        status=DeviceEventCommandBlockStatus.BLOCKED,
        blocking_command_id=31,
        blocking_command_code="CMD-DELIVERY-UNKNOWN-001",
    )
    actual_binding = (
        SimpleNamespace(
            line_run_epoch_id=11,
            device_code="ARM-01",
            endpoint_base_url="http://ecs-mock:8080",
            status_max_age_ms=5_000,
        )
        if binding is _DEFAULT_BINDING
        else binding
    )
    actual_adapter = adapter or FakeAdapter(_status())
    audit_service = audit or FakeAuditService()
    evidence_repo = FakeEvidenceRepository(actual_evidence, result=result)
    command_repo = FakeCommandRepository(actual_command)
    service = DeviceCommandService(
        session_factory=FakeSessions(actual_command),  # type: ignore[arg-type]
        command_repository=command_repo,  # type: ignore[arg-type]
        epoch_repository=FakeEpochRepository(actual_binding),  # type: ignore[arg-type]
        evidence_repository=evidence_repo,  # type: ignore[arg-type]
        adapter_provider=FakeAdapterProvider(actual_adapter),  # type: ignore[arg-type]
        event_command_block_repository=FakeBlockRepository(actual_block, latest=latest),  # type: ignore[arg-type]
        audit_service=audit_service,  # type: ignore[arg-type]
        clock=lambda: NOW,
    )
    return service, actual_command, actual_adapter, evidence_repo, audit_service


@pytest.mark.asyncio
async def test_manual_reconciliation_closes_only_exact_latest_blocked_delivery_unknown_command() -> None:
    service, command, adapter, evidence_repo, audit = _service()

    handle = await service.reconcile_delivery_unknown_as_device_idle(
        source_event_id="EVENT-001", block_id=51, reason="现场确认设备空闲", actor_id=42
    )

    assert handle.command_code == command.command_code
    assert handle.status is CommandStatus.FAILED
    assert CommandStatus(command.status) is CommandStatus.FAILED
    assert command.failure_code == "MANUAL_RECONCILIATION_DEVICE_IDLE"
    assert command.reconciliation_reason == "DELIVERY_UNKNOWN"
    assert command.result_evidence_id is None
    assert adapter.calls == 1
    assert evidence_repo.result_reads == 2
    assert len(audit.calls) == 1
    args = audit.calls[0]["args"]
    assert args["operation"] == "manual_reconcile_device_idle"
    assert args["block_id"] == 51
    assert args["actor_id"] == 42
    assert "endpoint_base_url" not in args
    assert "params" not in args


@pytest.mark.asyncio
async def test_manual_reconciliation_unknown_event_or_mismatched_block_is_not_found_without_ecs() -> None:
    service, _command_value, adapter, _evidence_repo, _audit = _service(
        evidence=SimpleNamespace(id=41, source_identity="OTHER")
    )

    with pytest.raises(DeviceCommandManualReconciliationNotFoundError):
        await service.reconcile_delivery_unknown_as_device_idle(
            source_event_id="EVENT-001", block_id=51, reason="确认空闲", actor_id=42
        )

    assert adapter.calls == 0


@pytest.mark.asyncio
async def test_manual_reconciliation_rejects_non_latest_blocker_before_ecs() -> None:
    block = SimpleNamespace(
        id=51,
        evidence_id=41,
        status=DeviceEventCommandBlockStatus.BLOCKED,
        blocking_command_id=31,
        blocking_command_code="CMD-DELIVERY-UNKNOWN-001",
    )
    latest = SimpleNamespace(id=52, evidence_id=41, status=DeviceEventCommandBlockStatus.BLOCKED)
    service, command, adapter, _evidence_repo, _audit = _service(block=block, latest=latest)

    with pytest.raises(DeviceCommandManualReconciliationConflictError, match="不是当前 BLOCKED 因果"):
        await service.reconcile_delivery_unknown_as_device_idle(
            source_event_id="EVENT-001", block_id=51, reason="确认空闲", actor_id=42
        )

    assert CommandStatus(command.status) is CommandStatus.RECONCILING
    assert adapter.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutate_command",
    [
        pytest.param(lambda command: setattr(command, "command_code", "OTHER-COMMAND"), id="command-missing"),
        pytest.param(lambda command: setattr(command, "id", 32), id="command-id-mismatch"),
    ],
)
async def test_manual_reconciliation_rejects_missing_or_mismatched_blocking_command(mutate_command) -> None:
    command = _command()
    mutate_command(command)
    service, command, adapter, _evidence_repo, _audit = _service(command=command)

    with pytest.raises(DeviceCommandManualReconciliationConflictError, match="blocker 指向的命令不存在"):
        await service.reconcile_delivery_unknown_as_device_idle(
            source_event_id="EVENT-001", block_id=51, reason="确认空闲", actor_id=42
        )

    assert CommandStatus(command.status) is CommandStatus.RECONCILING
    assert adapter.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "binding",
    [
        pytest.param(None, id="binding-missing"),
        pytest.param(
            SimpleNamespace(
                line_run_epoch_id=11,
                device_code="ARM-01",
                endpoint_base_url="http://ecs-mock:8080",
                status_max_age_ms=0,
            ),
            id="status-max-age-invalid",
        ),
        pytest.param(
            SimpleNamespace(
                line_run_epoch_id=11,
                device_code="ARM-01",
                endpoint_base_url="https://ecs-mock:8080",
                status_max_age_ms=5_000,
            ),
            id="endpoint-invalid",
        ),
    ],
)
async def test_manual_reconciliation_rejects_unresolvable_frozen_binding(binding) -> None:
    service, command, adapter, _evidence_repo, _audit = _service(binding=binding)

    with pytest.raises(DeviceCommandManualReconciliationConflictError, match="不可解析"):
        await service.reconcile_delivery_unknown_as_device_idle(
            source_event_id="EVENT-001", block_id=51, reason="确认空闲", actor_id=42
        )

    assert CommandStatus(command.status) is CommandStatus.RECONCILING
    assert adapter.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutate", "result"),
    [
        (lambda command, block: setattr(block, "status", DeviceEventCommandBlockStatus.REQUEUED), None),
        (lambda command, block: setattr(command, "status", CommandStatus.ACKNOWLEDGED), None),
        (lambda command, block: setattr(command, "reconciliation_reason", "ACK_DEADLINE_EXPIRED"), None),
        (lambda command, block: None, object()),
    ],
)
async def test_manual_reconciliation_rejects_invalid_causal_state_before_ecs(mutate, result) -> None:
    command = _command()
    block = SimpleNamespace(
        id=51,
        evidence_id=41,
        status=DeviceEventCommandBlockStatus.BLOCKED,
        blocking_command_id=31,
        blocking_command_code=command.command_code,
    )
    mutate(command, block)
    service, command, adapter, _evidence_repo, _audit = _service(command=command, block=block, result=result)

    with pytest.raises(DeviceCommandManualReconciliationConflictError):
        await service.reconcile_delivery_unknown_as_device_idle(
            source_event_id="EVENT-001", block_id=51, reason="确认空闲", actor_id=42
        )

    assert CommandStatus(command.status) is not CommandStatus.FAILED
    assert adapter.calls == 0


@pytest.mark.asyncio
async def test_manual_reconciliation_diagnostic_command_fails_closed() -> None:
    command = _command()
    command.execution_ref_type = "EVENT_DEBUG"
    command.line_run_epoch_id = None
    service, command, adapter, _evidence_repo, _audit = _service(command=command)

    with pytest.raises(DeviceCommandManualReconciliationConflictError):
        await service.reconcile_delivery_unknown_as_device_idle(
            source_event_id="EVENT-001", block_id=51, reason="确认空闲", actor_id=42
        )

    assert CommandStatus(command.status) is CommandStatus.RECONCILING
    assert adapter.calls == 0


@pytest.mark.asyncio
async def test_manual_reconciliation_rejects_stale_status() -> None:
    adapter = FakeAdapter(_status(updated_at=int((NOW - timedelta(seconds=6)).replace(tzinfo=UTC).timestamp() * 1000)))
    service, command, adapter, _evidence_repo, _audit = _service(adapter=adapter)

    with pytest.raises(DeviceCommandAdmissionError, match="DEVICE_STATUS_STALE"):
        await service.reconcile_delivery_unknown_as_device_idle(
            source_event_id="EVENT-001", block_id=51, reason="确认空闲", actor_id=42
        )

    assert CommandStatus(command.status) is CommandStatus.RECONCILING
    assert adapter.calls == 1


@pytest.mark.asyncio
async def test_manual_reconciliation_preserves_command_when_ecs_status_is_unavailable() -> None:
    adapter = FakeAdapter(_status(), error=RuntimeError("ecs status unavailable"))
    service, command, adapter, evidence_repo, audit = _service(adapter=adapter)

    with pytest.raises(RuntimeError, match="ecs status unavailable"):
        await service.reconcile_delivery_unknown_as_device_idle(
            source_event_id="EVENT-001", block_id=51, reason="确认空闲", actor_id=42
        )

    assert CommandStatus(command.status) is CommandStatus.RECONCILING
    assert command.failure_code is None
    assert adapter.calls == 1
    assert evidence_repo.result_reads == 1
    assert audit.calls == []


@pytest.mark.asyncio
async def test_manual_reconciliation_rejects_next_generation_blocker_after_status_probe() -> None:
    block = SimpleNamespace(
        id=51,
        evidence_id=41,
        status=DeviceEventCommandBlockStatus.BLOCKED,
        blocking_command_id=31,
        blocking_command_code="CMD-DELIVERY-UNKNOWN-001",
    )
    latest = SimpleNamespace(id=51, evidence_id=41, status=DeviceEventCommandBlockStatus.BLOCKED)

    def requeue_with_next_generation() -> None:
        block.status = DeviceEventCommandBlockStatus.REQUEUED
        latest.id = 52

    adapter = FakeAdapter(_status(), before_return=requeue_with_next_generation)
    service, command, adapter, _evidence_repo, audit = _service(block=block, latest=latest, adapter=adapter)

    with pytest.raises(DeviceCommandManualReconciliationConflictError, match="目标 blocker 已漂移"):
        await service.reconcile_delivery_unknown_as_device_idle(
            source_event_id="EVENT-001", block_id=51, reason="确认空闲", actor_id=42
        )

    assert CommandStatus(command.status) is CommandStatus.RECONCILING
    assert command.failure_code is None
    assert adapter.calls == 1
    assert audit.calls == []


@pytest.mark.asyncio
async def test_manual_reconciliation_rechecks_version_after_status_probe() -> None:
    command = _command()

    def drift() -> None:
        command.version += 1

    adapter = FakeAdapter(_status(), before_return=drift)
    service, command, _adapter, _evidence_repo, _audit = _service(command=command, adapter=adapter)

    with pytest.raises(DeviceCommandManualReconciliationConflictError):
        await service.reconcile_delivery_unknown_as_device_idle(
            source_event_id="EVENT-001", block_id=51, reason="确认空闲", actor_id=42
        )

    assert CommandStatus(command.status) is CommandStatus.RECONCILING


@pytest.mark.asyncio
async def test_manual_reconciliation_rejects_result_arriving_after_status_probe() -> None:
    evidence_repo_holder: dict[str, FakeEvidenceRepository] = {}

    def receive_result() -> None:
        evidence_repo_holder["repo"].result = object()

    adapter = FakeAdapter(_status(), before_return=receive_result)
    service, command, _adapter, evidence_repo, audit = _service(adapter=adapter)
    evidence_repo_holder["repo"] = evidence_repo

    with pytest.raises(DeviceCommandManualReconciliationConflictError, match="已有 DEVICE_RESULT"):
        await service.reconcile_delivery_unknown_as_device_idle(
            source_event_id="EVENT-001", block_id=51, reason="确认空闲", actor_id=42
        )

    assert CommandStatus(command.status) is CommandStatus.RECONCILING
    assert command.failure_code is None
    assert evidence_repo.result_reads == 2
    assert audit.calls == []


@pytest.mark.asyncio
async def test_manual_reconciliation_audit_failure_rolls_back_command() -> None:
    service, command, _adapter, _evidence_repo, _audit = _service(
        audit=FakeAuditService(error=RuntimeError("audit unavailable"))
    )

    with pytest.raises(RuntimeError, match="audit unavailable"):
        await service.reconcile_delivery_unknown_as_device_idle(
            source_event_id="EVENT-001", block_id=51, reason="确认空闲", actor_id=42
        )

    assert CommandStatus(command.status) is CommandStatus.RECONCILING
    assert command.failure_code is None
