"""PostgreSQL 对 EVENT blocker 重处理事务边界的最终裁决。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text

from src.app.device.contracts import EcsCommandResultReport, EcsDeviceEvent, EcsDeviceStatus
from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.device.models.device import Device
from src.app.device.models.event_command_block import (
    DeviceEventCommandBlock,
    DeviceEventCommandBlockStatus,
)
from src.app.device.repositories.command_repository import DeviceCommandRepository
from src.app.device.services.device_command_service import (
    DeviceCommandManualReconciliationConflictError,
    DeviceCommandService,
)
from src.app.device.services.device_evidence_service import DeviceEvidenceService, EventCommandBlockConflictError
from src.app.execution.models.inbound_evidence import (
    InboundEvidence,
    InboundEvidenceApplyStatus,
    InboundEvidenceKind,
)
from src.app.execution.repositories.inbound_evidence_repository import InboundEvidenceRepository
from src.app.workline.models.line_run_epoch import LineRunEpoch, LineRunEpochDeviceBinding
from src.app.workline.models.workline import LineType, WorkLine


class _RecordingAuditService:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    async def create_audit_log(self, _db: object, **_values: object) -> object:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return object()


class _PausingIdleAdapter:
    def __init__(
        self,
        *,
        status_requested: asyncio.Event,
        release_status: asyncio.Event,
        observed_at: datetime,
    ) -> None:
        self._status_requested = status_requested
        self._release_status = release_status
        self._observed_at = observed_at

    async def fetch_status(self, device_code: str) -> EcsDeviceStatus:
        self._status_requested.set()
        await self._release_status.wait()
        return EcsDeviceStatus.model_validate(
            {
                "device": {
                    "device_code": device_code,
                    "device_name": device_code,
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
                    "updated_at": int(self._observed_at.replace(tzinfo=UTC).timestamp() * 1000),
                },
            }
        )


class _AdapterProvider:
    def __init__(self, adapter: _PausingIdleAdapter) -> None:
        self._adapter = adapter

    async def get_adapter(self, _endpoint_base_url: str) -> _PausingIdleAdapter:
        return self._adapter


class _PausingManualCommandRepository(DeviceCommandRepository):
    def __init__(
        self,
        *,
        command_code: str,
        command_locked: asyncio.Event,
        release_command: asyncio.Event,
    ) -> None:
        super().__init__()
        self._command_code = command_code
        self._command_locked = command_locked
        self._release_command = release_command

    async def get_by_command_code(self, db, command_code: str, *, for_update: bool = False):
        command = await super().get_by_command_code(db, command_code, for_update=for_update)
        if for_update and command_code == self._command_code:
            self._command_locked.set()
            await self._release_command.wait()
        return command


class _PausingResultEvidenceRepository(InboundEvidenceRepository):
    def __init__(
        self,
        *,
        source_identity: str,
        evidence_locked: asyncio.Event,
        release_evidence: asyncio.Event,
        backend_pid: asyncio.Future[int],
    ) -> None:
        super().__init__()
        self._source_identity = source_identity
        self._evidence_locked = evidence_locked
        self._release_evidence = release_evidence
        self._backend_pid = backend_pid

    async def claim_next_pending(self, db, *, kinds):
        result = await db.execute(
            select(InboundEvidence)
            .where(
                InboundEvidence.source_identity == self._source_identity,
                InboundEvidence.apply_status == InboundEvidenceApplyStatus.PENDING,
                InboundEvidence.kind.in_(kinds),
            )
            .with_for_update(skip_locked=True)
        )
        evidence = result.scalar_one_or_none()
        assert evidence is not None
        assert evidence.source_identity == self._source_identity
        pid = await db.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(pid, int)
        self._backend_pid.set_result(pid)
        self._evidence_locked.set()
        await self._release_evidence.wait()
        return evidence


async def _seed_blocked_event(db) -> tuple[InboundEvidence, DeviceEventCommandBlock]:
    identity = uuid4().hex
    device_code = f"ARM-EVENT-BLOCK-{identity[:12]}"
    source_event_id = f"EVENT:{identity}"
    evidence = InboundEvidence(
        kind=InboundEvidenceKind.DEVICE_EVENT,
        source_identity=source_event_id,
        payload_digest=identity.ljust(64, "a")[:64],
        normalized_payload=EcsDeviceEvent.model_validate(
            {
                "device_code": device_code,
                "contract_key": "third_party_integration",
                "contract_version": "1.1",
                "event_type": "SCAN_COMPLETED",
                "timestamp": 1_787_806_800_000,
                "source_event_id": source_event_id,
                "is_debug": True,
                "data": {"barcode": identity},
            }
        ).model_dump(mode="json", exclude_unset=True),
        received_at=datetime(2026, 8, 27, 10, 0),
        device_code=device_code,
        contract_key="third_party_integration",
        contract_version="1.1",
        apply_status=InboundEvidenceApplyStatus.RECONCILING,
        processed_at=datetime(2026, 8, 27, 10, 1),
    )
    db.add(evidence)
    await db.flush()
    command = DeviceCommand(
        command_code=f"CMD-EVENT-BLOCK-{identity[:12]}",
        device_code=device_code,
        line_run_epoch_id=None,
        device_binding_id=None,
        execution_ref_type="EVENT_DEBUG",
        execution_ref_id=source_event_id,
        material_execution_id=None,
        contract_key="third_party_integration",
        contract_version="1.1",
        task_type="MOVE_FORWARD",
        params={"barcode": identity},
        payload_digest=identity.ljust(64, "b")[:64],
        deadline_at=datetime(2026, 8, 27, 10, 2),
        endpoint_base_url="http://ecs-event-block:8080",
        command_timeout_ms=30_000,
        execution_reason=f"ECS_EVENT_DEBUG:{source_event_id}",
        created_by=None,
        status=CommandStatus.FAILED,
        failure_code="DEVICE_REPORTED_FAILURE",
    )
    db.add(command)
    await db.flush()
    block = DeviceEventCommandBlock(
        evidence_id=evidence.id,
        source_event_id=source_event_id,
        device_code=device_code,
        blocking_command_id=command.id,
        blocking_command_code=command.command_code,
        blocking_command_status=CommandStatus.RECONCILING,
        blocking_reconciliation_reason="DELIVERY_UNKNOWN",
        blocked_at=datetime(2026, 8, 27, 10, 1),
    )
    db.add(block)
    await db.flush()
    return evidence, block


async def _seed_result_race(db) -> tuple[InboundEvidence, DeviceEventCommandBlock, DeviceCommand]:
    identity = uuid4().hex
    line = WorkLine(
        line_code=f"LINE-EVENT-BLOCK-{identity[:12]}",
        line_name="EVENT blocker result race",
        line_type=LineType.AUTO,
    )
    db.add(line)
    await db.flush()
    device = Device(
        device_code=f"ARM-EVENT-BLOCK-{identity[:12]}",
        device_name="EVENT blocker arm",
        work_line_id=line.id,
        device_role="ROBOT_ARM",
    )
    db.add(device)
    await db.flush()
    epoch = LineRunEpoch(
        epoch_code=f"EPOCH-EVENT-BLOCK-{identity[:12]}",
        workline_id=line.id,
        plugin_key="event_block_test",
        plugin_version="1.0.0",
        flow_mode="TEST",
        topology_digest="a" * 64,
        configuration_digest="b" * 64,
        configuration_snapshot_json={},
        started_at=datetime(2026, 8, 27, 9, 0),
    )
    db.add(epoch)
    await db.flush()
    binding = LineRunEpochDeviceBinding(
        line_run_epoch_id=epoch.id,
        device_id=device.id,
        device_code=device.device_code,
        device_role=device.device_role,
        endpoint_base_url="http://ecs-event-block:8080",
        contract_key="arm.pick",
        contract_version="2.0",
        status_max_age_ms=5_000,
        command_timeout_ms=30_000,
    )
    db.add(binding)
    await db.flush()
    source_event_id = f"EVENT:{identity}"
    evidence = InboundEvidence(
        kind=InboundEvidenceKind.DEVICE_EVENT,
        source_identity=source_event_id,
        payload_digest=identity.ljust(64, "c")[:64],
        normalized_payload=EcsDeviceEvent.model_validate(
            {
                "device_code": device.device_code,
                "contract_key": binding.contract_key,
                "contract_version": binding.contract_version,
                "event_type": "SCAN_COMPLETED",
                "timestamp": 1_787_806_800_000,
                "source_event_id": source_event_id,
                "is_debug": True,
                "data": {"barcode": identity},
            }
        ).model_dump(mode="json", exclude_unset=True),
        received_at=datetime(2026, 8, 27, 10, 0),
        line_run_epoch_id=epoch.id,
        device_code=device.device_code,
        contract_key=binding.contract_key,
        contract_version=binding.contract_version,
        apply_status=InboundEvidenceApplyStatus.RECONCILING,
        processed_at=datetime(2026, 8, 27, 10, 1),
    )
    db.add(evidence)
    await db.flush()
    command = DeviceCommand(
        command_code=f"CMD-EVENT-BLOCK-{identity[:12]}",
        device_code=device.device_code,
        line_run_epoch_id=epoch.id,
        device_binding_id=binding.id,
        execution_ref_type="MATERIAL_EXECUTION",
        execution_ref_id=f"EXEC-{identity}",
        material_execution_id=None,
        contract_key=binding.contract_key,
        contract_version=binding.contract_version,
        task_type="PICK",
        params={"barcode": identity},
        payload_digest=identity.ljust(64, "d")[:64],
        deadline_at=datetime(2026, 8, 27, 10, 2),
        status=CommandStatus.RECONCILING,
        reconciliation_reason="DELIVERY_UNKNOWN",
    )
    db.add(command)
    await db.flush()
    block = DeviceEventCommandBlock(
        evidence_id=evidence.id,
        source_event_id=source_event_id,
        device_code=device.device_code,
        blocking_command_id=command.id,
        blocking_command_code=command.command_code,
        blocking_command_status=CommandStatus.RECONCILING,
        blocking_reconciliation_reason="DELIVERY_UNKNOWN",
        blocked_at=datetime(2026, 8, 27, 10, 1),
    )
    db.add(block)
    await db.flush()
    return evidence, block, command


def _result_report(command: DeviceCommand) -> EcsCommandResultReport:
    return EcsCommandResultReport.model_validate(
        {
            "command_code": command.command_code,
            "device_code": command.device_code,
            "result": "SUCCESS",
            "finish_time": 1_787_806_805_000,
            "data": {"position": "HOME"},
            "error_detail": None,
        }
    )


def _idle_manual_service(
    *,
    integration_session_factory,
    observed_at: datetime,
    audit: _RecordingAuditService | None = None,
) -> DeviceCommandService:
    status_requested = asyncio.Event()
    release_status = asyncio.Event()
    release_status.set()
    return DeviceCommandService(
        session_factory=integration_session_factory,
        adapter_provider=_AdapterProvider(
            _PausingIdleAdapter(
                status_requested=status_requested,
                release_status=release_status,
                observed_at=observed_at,
            )
        ),  # type: ignore[arg-type]
        audit_service=audit or _RecordingAuditService(),  # type: ignore[arg-type]
        clock=lambda: observed_at,
    )


def _reprocessing_service(integration_session_factory) -> DeviceEvidenceService:
    return DeviceEvidenceService(
        session_factory=integration_session_factory,
        event_debug_command_service=DeviceCommandService(session_factory=integration_session_factory),
        audit_service=_RecordingAuditService(),  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 8, 27, 13, 0),
    )


async def _event_debug_commands(db, source_event_id: str) -> list[DeviceCommand]:
    result = await db.execute(
        select(DeviceCommand).where(
            DeviceCommand.execution_ref_type == "EVENT_DEBUG",
            DeviceCommand.execution_ref_id == source_event_id,
        )
    )
    return list(result.scalars().all())


@pytest_asyncio.fixture(autouse=True)
async def cleanup_event_block_reconciliation_rows(integration_session_factory):
    yield
    async with integration_session_factory.begin() as db:
        evidence_ids = select(InboundEvidence.id).where(InboundEvidence.device_code.like("ARM-EVENT-BLOCK-%"))
        epoch_ids = select(LineRunEpoch.id).where(LineRunEpoch.epoch_code.like("EPOCH-EVENT-BLOCK-%"))
        device_ids = select(Device.id).where(Device.device_code.like("ARM-EVENT-BLOCK-%"))
        line_ids = select(WorkLine.id).where(WorkLine.line_code.like("LINE-EVENT-BLOCK-%"))
        await db.execute(delete(DeviceEventCommandBlock).where(DeviceEventCommandBlock.evidence_id.in_(evidence_ids)))
        await db.execute(delete(DeviceCommand).where(DeviceCommand.device_code.like("ARM-EVENT-BLOCK-%")))
        await db.execute(delete(InboundEvidence).where(InboundEvidence.id.in_(evidence_ids)))
        await db.execute(
            delete(LineRunEpochDeviceBinding).where(LineRunEpochDeviceBinding.line_run_epoch_id.in_(epoch_ids))
        )
        await db.execute(delete(LineRunEpoch).where(LineRunEpoch.id.in_(epoch_ids)))
        await db.execute(delete(Device).where(Device.id.in_(device_ids)))
        await db.execute(delete(WorkLine).where(WorkLine.id.in_(line_ids)))


@pytest.mark.asyncio
async def test_postgresql_reprocess_keeps_latest_blocker_history_and_original_event_identity(
    integration_session_factory,
) -> None:
    async with integration_session_factory.begin() as db:
        evidence, block = await _seed_blocked_event(db)
        evidence_id = evidence.id
        block_id = block.id
        source_event_id = evidence.source_identity
        frozen_payload = evidence.normalized_payload.copy()
        frozen_digest = evidence.payload_digest

    audit = _RecordingAuditService()
    service = DeviceEvidenceService(session_factory=integration_session_factory, audit_service=audit)  # type: ignore[arg-type]
    result = await service.reprocess_blocked_event(
        source_event_id=source_event_id,
        block_id=block_id,
        reason="结果已闭合",
        actor_id=42,
    )

    async with integration_session_factory() as db:
        persisted_evidence = await db.get(InboundEvidence, evidence_id)
        persisted_block = await db.get(DeviceEventCommandBlock, block_id)
    assert result.apply_status is InboundEvidenceApplyStatus.PENDING
    assert persisted_evidence is not None
    assert InboundEvidenceApplyStatus(persisted_evidence.apply_status) is InboundEvidenceApplyStatus.PENDING
    assert persisted_evidence.processed_at is None
    assert persisted_evidence.source_identity == source_event_id
    assert persisted_evidence.payload_digest == frozen_digest
    assert persisted_evidence.normalized_payload == frozen_payload
    assert persisted_block is not None
    assert DeviceEventCommandBlockStatus(persisted_block.status) is DeviceEventCommandBlockStatus.REQUEUED
    assert audit.calls == 1

    snapshot = await service.get_event_command_block(source_event_id)
    assert snapshot.block_id == block_id
    assert snapshot.status is DeviceEventCommandBlockStatus.REQUEUED
    assert snapshot.blocking_command_terminal is True


@pytest.mark.asyncio
async def test_postgresql_audit_failure_rolls_back_block_and_evidence_requeue(integration_session_factory) -> None:
    async with integration_session_factory.begin() as db:
        evidence, block = await _seed_blocked_event(db)
        evidence_id = evidence.id
        block_id = block.id
        source_event_id = evidence.source_identity

    service = DeviceEvidenceService(
        session_factory=integration_session_factory,
        audit_service=_RecordingAuditService(error=RuntimeError("audit unavailable")),  # type: ignore[arg-type]
    )
    with pytest.raises(RuntimeError, match="audit unavailable"):
        await service.reprocess_blocked_event(
            source_event_id=source_event_id,
            block_id=block_id,
            reason="结果已闭合",
            actor_id=42,
        )

    async with integration_session_factory() as db:
        persisted_evidence = await db.get(InboundEvidence, evidence_id)
        persisted_block = await db.get(DeviceEventCommandBlock, block_id)
    assert persisted_evidence is not None
    assert InboundEvidenceApplyStatus(persisted_evidence.apply_status) is InboundEvidenceApplyStatus.RECONCILING
    assert persisted_evidence.processed_at is not None
    assert persisted_block is not None
    assert DeviceEventCommandBlockStatus(persisted_block.status) is DeviceEventCommandBlockStatus.BLOCKED
    assert persisted_block.requeued_at is None


@pytest.mark.asyncio
async def test_postgresql_result_closure_reprocesses_original_event_into_one_debug_command(
    integration_session_factory,
) -> None:
    async with integration_session_factory.begin() as db:
        event_evidence, block, command = await _seed_result_race(db)
        event_evidence_id = event_evidence.id
        block_id = block.id
        source_event_id = event_evidence.source_identity
        command_id = command.id
        report = _result_report(command)

    service = DeviceEvidenceService(session_factory=integration_session_factory)
    result_receipt = await service.accept_result(report)
    assert await service.process_one() is True

    async with integration_session_factory() as db:
        persisted_command = await db.get(DeviceCommand, command_id)
        event_debug_commands = await _event_debug_commands(db, source_event_id)
    assert persisted_command is not None
    assert CommandStatus(persisted_command.status) is CommandStatus.SUCCEEDED
    assert persisted_command.result_evidence_id == result_receipt.evidence_id
    assert event_debug_commands == []

    reprocess_service = _reprocessing_service(integration_session_factory)
    await reprocess_service.reprocess_blocked_event(
        source_event_id=source_event_id,
        block_id=block_id,
        reason="Result 已闭合旧命令",
        actor_id=42,
    )
    assert await reprocess_service.process_one() is True

    async with integration_session_factory() as db:
        persisted_event = await db.get(InboundEvidence, event_evidence_id)
        event_debug_commands = await _event_debug_commands(db, source_event_id)
    assert persisted_event is not None
    assert InboundEvidenceApplyStatus(persisted_event.apply_status) is InboundEvidenceApplyStatus.IGNORED
    assert len(event_debug_commands) == 1


@pytest.mark.asyncio
async def test_postgresql_manual_closure_keeps_late_result_and_allows_explicit_reprocess(
    integration_session_factory,
) -> None:
    observed_at = datetime(2026, 8, 27, 12, 0)
    async with integration_session_factory.begin() as db:
        event_evidence, block, command = await _seed_result_race(db)
        event_evidence_id = event_evidence.id
        block_id = block.id
        source_event_id = event_evidence.source_identity
        command_id = command.id
        report = _result_report(command)

    manual_service = _idle_manual_service(
        integration_session_factory=integration_session_factory,
        observed_at=observed_at,
    )
    handle = await manual_service.reconcile_delivery_unknown_as_device_idle(
        source_event_id=source_event_id,
        block_id=block_id,
        reason="现场确认设备空闲",
        actor_id=42,
    )
    assert handle.status is CommandStatus.FAILED

    evidence_service = DeviceEvidenceService(session_factory=integration_session_factory)
    late_result = await evidence_service.accept_result(report)
    assert await evidence_service.process_one() is True

    async with integration_session_factory() as db:
        persisted_command = await db.get(DeviceCommand, command_id)
        persisted_result = await db.get(InboundEvidence, late_result.evidence_id)
    assert persisted_command is not None
    assert CommandStatus(persisted_command.status) is CommandStatus.FAILED
    assert persisted_command.failure_code == "MANUAL_RECONCILIATION_DEVICE_IDLE"
    assert persisted_command.result_evidence_id is None
    assert persisted_result is not None
    assert InboundEvidenceApplyStatus(persisted_result.apply_status) is InboundEvidenceApplyStatus.RECONCILING

    reprocess_service = _reprocessing_service(integration_session_factory)
    await reprocess_service.reprocess_blocked_event(
        source_event_id=source_event_id,
        block_id=block_id,
        reason="人工对账已闭合旧命令",
        actor_id=42,
    )
    assert await reprocess_service.process_one() is True

    async with integration_session_factory() as db:
        persisted_event = await db.get(InboundEvidence, event_evidence_id)
        event_debug_commands = await _event_debug_commands(db, source_event_id)
    assert persisted_event is not None
    assert InboundEvidenceApplyStatus(persisted_event.apply_status) is InboundEvidenceApplyStatus.IGNORED
    assert len(event_debug_commands) == 1


@pytest.mark.asyncio
async def test_postgresql_manual_closure_audit_failure_rolls_back_command(integration_session_factory) -> None:
    observed_at = datetime(2026, 8, 27, 12, 0)
    async with integration_session_factory.begin() as db:
        _event_evidence, block, command = await _seed_result_race(db)
        block_id = block.id
        source_event_id = block.source_event_id
        command_id = command.id

    audit = _RecordingAuditService(error=RuntimeError("audit unavailable"))
    manual_service = _idle_manual_service(
        integration_session_factory=integration_session_factory,
        observed_at=observed_at,
        audit=audit,
    )
    with pytest.raises(RuntimeError, match="audit unavailable"):
        await manual_service.reconcile_delivery_unknown_as_device_idle(
            source_event_id=source_event_id,
            block_id=block_id,
            reason="现场确认设备空闲",
            actor_id=42,
        )

    async with integration_session_factory() as db:
        persisted_command = await db.get(DeviceCommand, command_id)
    assert persisted_command is not None
    assert CommandStatus(persisted_command.status) is CommandStatus.RECONCILING
    assert persisted_command.failure_code is None
    assert audit.calls == 1


@pytest.mark.asyncio
async def test_postgresql_old_block_id_cannot_reprocess_new_blocker_generation(integration_session_factory) -> None:
    async with integration_session_factory.begin() as db:
        evidence, old_block, blocking_command = await _seed_result_race(db)
        evidence_id = evidence.id
        old_block_id = old_block.id
        old_block.blocked_at = datetime(2026, 8, 26, 10, 1)
        source_event_id = evidence.source_identity
        device_code = evidence.device_code
        result_report = _result_report(blocking_command)

    service = _reprocessing_service(integration_session_factory)
    await service.accept_result(result_report)
    assert await service.process_one() is True
    await service.reprocess_blocked_event(
        source_event_id=source_event_id,
        block_id=old_block_id,
        reason="旧命令已终结",
        actor_id=42,
    )

    async with integration_session_factory.begin() as db:
        active_command = DeviceCommand(
            command_code=f"CMD-ACTIVE-{uuid4().hex[:12]}",
            device_code=device_code,
            line_run_epoch_id=None,
            device_binding_id=None,
            execution_ref_type="MANUAL_DEBUG",
            execution_ref_id=f"MANUAL:{uuid4().hex}",
            material_execution_id=None,
            contract_key="third_party_integration",
            contract_version="1.1",
            task_type="MOVE_FORWARD",
            params={},
            payload_digest="e" * 64,
            deadline_at=datetime(2026, 8, 27, 13, 5),
            endpoint_base_url="http://ecs-event-block:8080",
            command_timeout_ms=30_000,
            execution_reason="test active device slot",
            created_by=42,
            status=CommandStatus.PENDING,
        )
        db.add(active_command)
        await db.flush()
        active_command_id = active_command.id

    assert await service.process_one() is True

    async with integration_session_factory() as db:
        blocks = (
            (
                await db.execute(
                    select(DeviceEventCommandBlock)
                    .where(DeviceEventCommandBlock.evidence_id == evidence_id)
                    .order_by(DeviceEventCommandBlock.id)
                )
            )
            .scalars()
            .all()
        )
        event_debug_commands = await _event_debug_commands(db, source_event_id)
    assert len(blocks) == 2
    assert DeviceEventCommandBlockStatus(blocks[0].status) is DeviceEventCommandBlockStatus.REQUEUED
    assert DeviceEventCommandBlockStatus(blocks[1].status) is DeviceEventCommandBlockStatus.BLOCKED
    assert blocks[1].blocking_command_id == active_command_id
    assert event_debug_commands == []

    with pytest.raises(EventCommandBlockConflictError, match="不是当前 BLOCKED"):
        await service.reprocess_blocked_event(
            source_event_id=source_event_id,
            block_id=old_block_id,
            reason="错误重放旧 blocker",
            actor_id=42,
        )

    snapshot = await service.get_event_command_block(source_event_id)
    assert snapshot.block_id == blocks[1].id
    assert snapshot.status is DeviceEventCommandBlockStatus.BLOCKED


@pytest.mark.asyncio
async def test_postgresql_pending_result_wins_manual_closure_without_lock_cycle(integration_session_factory) -> None:
    observed_at = datetime(2026, 8, 27, 12, 0)
    async with integration_session_factory.begin() as db:
        event_evidence, block, command = await _seed_result_race(db)
        event_evidence_id = event_evidence.id
        block_id = block.id
        source_event_id = event_evidence.source_identity
        command_id = command.id
        command_code = command.command_code
        device_code = command.device_code

    status_requested = asyncio.Event()
    release_status = asyncio.Event()
    manual_holds_command = asyncio.Event()
    release_manual_command = asyncio.Event()
    result_holds_evidence = asyncio.Event()
    release_result_evidence = asyncio.Event()
    worker_backend_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()
    manual_audit = _RecordingAuditService()
    manual_service = DeviceCommandService(
        session_factory=integration_session_factory,
        command_repository=_PausingManualCommandRepository(
            command_code=command_code,
            command_locked=manual_holds_command,
            release_command=release_manual_command,
        ),
        adapter_provider=_AdapterProvider(
            _PausingIdleAdapter(
                status_requested=status_requested,
                release_status=release_status,
                observed_at=observed_at,
            )
        ),  # type: ignore[arg-type]
        audit_service=manual_audit,  # type: ignore[arg-type]
        clock=lambda: observed_at,
    )
    manual_task = asyncio.create_task(
        manual_service.reconcile_delivery_unknown_as_device_idle(
            source_event_id=source_event_id,
            block_id=block_id,
            reason="现场确认设备空闲",
            actor_id=42,
        )
    )
    await asyncio.wait_for(status_requested.wait(), timeout=2)

    ingress_service = DeviceEvidenceService(session_factory=integration_session_factory)
    result_receipt = await ingress_service.accept_result(
        EcsCommandResultReport.model_validate(
            {
                "command_code": command_code,
                "device_code": device_code,
                "result": "SUCCESS",
                "finish_time": 1_787_806_805_000,
                "data": {"position": "HOME"},
                "error_detail": None,
            }
        )
    )
    worker_service = DeviceEvidenceService(
        session_factory=integration_session_factory,
        processing_repository=_PausingResultEvidenceRepository(
            source_identity=result_receipt.source_event_id,
            evidence_locked=result_holds_evidence,
            release_evidence=release_result_evidence,
            backend_pid=worker_backend_pid,
        ),
    )
    worker_task = asyncio.create_task(worker_service.process_one())
    await asyncio.wait_for(result_holds_evidence.wait(), timeout=2)

    release_status.set()
    await asyncio.wait_for(manual_holds_command.wait(), timeout=2)
    backend_pid = await asyncio.wait_for(worker_backend_pid, timeout=2)
    release_result_evidence.set()
    try:
        wait_deadline = asyncio.get_running_loop().time() + 10
        last_wait_state: dict[str, object] | None = None
        async with integration_session_factory() as observer_db:
            while True:
                wait_state = (
                    (
                        await observer_db.execute(
                            text(
                                "SELECT state, wait_event_type, wait_event "
                                "FROM pg_stat_activity WHERE pid = :backend_pid"
                            ),
                            {"backend_pid": backend_pid},
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                last_wait_state = dict(wait_state) if wait_state is not None else None
                if last_wait_state is not None and last_wait_state["wait_event_type"] == "Lock":
                    break
                if worker_task.done():
                    pytest.fail(
                        "result worker completed before PostgreSQL command-row lock wait; "
                        f"last_wait_state={last_wait_state!r}, exception={worker_task.exception()!r}"
                    )
                if asyncio.get_running_loop().time() >= wait_deadline:
                    pytest.fail(
                        "result worker did not enter PostgreSQL lock wait before deadline; "
                        f"last_wait_state={last_wait_state!r}"
                    )
                await observer_db.rollback()
                await asyncio.sleep(0.01)
        assert last_wait_state is not None
        assert last_wait_state["state"] == "active"
        assert not worker_task.done()
    finally:
        release_manual_command.set()
        release_result_evidence.set()

    with pytest.raises(DeviceCommandManualReconciliationConflictError, match="已有 DEVICE_RESULT"):
        await asyncio.wait_for(manual_task, timeout=2)
    assert await asyncio.wait_for(worker_task, timeout=2) is True

    async with integration_session_factory() as db:
        persisted_command = await db.get(DeviceCommand, command_id)
        persisted_result = await db.get(InboundEvidence, result_receipt.evidence_id)
        persisted_event = await db.get(InboundEvidence, event_evidence_id)
        persisted_block = await db.get(DeviceEventCommandBlock, block_id)
    assert persisted_command is not None
    assert CommandStatus(persisted_command.status) is CommandStatus.SUCCEEDED
    assert persisted_command.result_evidence_id == result_receipt.evidence_id
    assert persisted_command.failure_code is None
    assert persisted_result is not None
    assert InboundEvidenceApplyStatus(persisted_result.apply_status) is InboundEvidenceApplyStatus.APPLIED
    assert persisted_event is not None
    assert InboundEvidenceApplyStatus(persisted_event.apply_status) is InboundEvidenceApplyStatus.RECONCILING
    assert persisted_block is not None
    assert DeviceEventCommandBlockStatus(persisted_block.status) is DeviceEventCommandBlockStatus.BLOCKED
    assert manual_audit.calls == 0
