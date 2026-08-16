"""PostgreSQL 对 DeviceCommand 并发不变量的最终裁决。"""

from __future__ import annotations

import asyncio
from datetime import datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from src.app.device.contracts import DeviceCommandRequest, EcsDeviceEvent
from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.device.models.device import Device
from src.app.device.repositories.command_repository import device_command_repository
from src.app.device.services.device_command_service import DeviceCommandService
from src.app.device.services.device_evidence_service import (
    DeviceEvidenceConflictError,
    DeviceEvidenceService,
)
from src.app.execution.models.inbound_evidence import InboundEvidence, InboundEvidenceConflict
from src.app.workline.models.line_run_epoch import LineRunEpoch, LineRunEpochDeviceBinding
from src.app.workline.models.workline import LineType, WorkLine
from src.app.workline.repositories.line_run_epoch_repository import LineRunEpochRepository
from src.app.workline.services.line_run_epoch_service import ActiveLineRunEpochExistsError, LineRunEpochService


async def _seed_topology(db) -> tuple[WorkLine, Device, LineRunEpoch, LineRunEpochDeviceBinding]:
    identity = uuid4().hex[:12]
    line = WorkLine(
        line_code=f"LINE-DEVICE-COMMAND-CONSTRAINT-{identity}",
        line_name="DeviceCommand",
        line_type=LineType.AUTO,
    )
    db.add(line)
    await db.flush()
    device = Device(
        device_code=f"ARM-DEVICE-COMMAND-CONSTRAINT-{identity}",
        device_name="DeviceCommand Arm",
        work_line_id=line.id,
        device_role="ROBOT_ARM",
    )
    db.add(device)
    await db.flush()
    epoch = LineRunEpoch(
        epoch_code=f"EPOCH-DEVICE-COMMAND-CONSTRAINT-{identity}",
        workline_id=line.id,
        plugin_key="device_command_test",
        plugin_version="1.0.0",
        flow_mode="TEST",
        topology_digest="a" * 64,
        configuration_digest="b" * 64,
        started_at=datetime(2026, 8, 13),
    )
    db.add(epoch)
    await db.flush()
    binding = LineRunEpochDeviceBinding(
        line_run_epoch_id=epoch.id,
        device_id=device.id,
        device_code=device.device_code,
        contract_key="arm.pick",
        contract_version="2.0",
        status_max_age_ms=1_000,
        command_timeout_ms=30_000,
    )
    db.add(binding)
    await db.flush()
    return line, device, epoch, binding


def _command(binding: LineRunEpochDeviceBinding, code: str, status: CommandStatus) -> DeviceCommand:
    return DeviceCommand(
        command_code=code,
        device_code=binding.device_code,
        line_run_epoch_id=binding.line_run_epoch_id,
        device_binding_id=binding.id,
        execution_ref_type="TEST_EXECUTION",
        execution_ref_id=code,
        contract_key=binding.contract_key,
        contract_version=binding.contract_version,
        task_type="TEST_ACTION",
        params={},
        payload_digest=code.ljust(64, "x")[:64],
        deadline_at=datetime(2026, 8, 14),
        status=status,
    )


def _event(source_event_id: str, *, marker: str) -> EcsDeviceEvent:
    return EcsDeviceEvent(
        device_code=f"ARM-{uuid4().hex[:12]}",
        contract_key="arm.pick",
        contract_version="2.0",
        event_type="DEVICE_CONTRACT_EVENT",
        timestamp=1_786_579_204_000,
        source_event_id=source_event_id,
        data={"marker": marker},
        trace_id=f"TRACE-{marker}",
    )


@pytest_asyncio.fixture(autouse=True)
async def cleanup_device_command_constraint_rows(integration_session_factory):
    """共享测试库中只回收本文件创建的 DeviceCommand 约束证据。"""

    yield

    async with integration_session_factory.begin() as db:
        evidence_ids = select(InboundEvidence.id).where(
            InboundEvidence.source_identity.like("DEVICE-COMMAND-CONSTRAINT-EVENT-%")
        )
        epoch_ids = select(LineRunEpoch.id).where(LineRunEpoch.epoch_code.like("EPOCH-DEVICE-COMMAND-CONSTRAINT-%"))
        device_ids = select(Device.id).where(Device.device_code.like("ARM-DEVICE-COMMAND-CONSTRAINT-%"))
        line_ids = select(WorkLine.id).where(WorkLine.line_code.like("LINE-DEVICE-COMMAND-CONSTRAINT-%"))
        await db.execute(
            delete(InboundEvidenceConflict).where(InboundEvidenceConflict.first_evidence_id.in_(evidence_ids))
        )
        await db.execute(delete(DeviceCommand).where(DeviceCommand.line_run_epoch_id.in_(epoch_ids)))
        await db.execute(delete(InboundEvidence).where(InboundEvidence.id.in_(evidence_ids)))
        await db.execute(
            delete(LineRunEpochDeviceBinding).where(LineRunEpochDeviceBinding.line_run_epoch_id.in_(epoch_ids))
        )
        await db.execute(delete(LineRunEpoch).where(LineRunEpoch.id.in_(epoch_ids)))
        await db.execute(delete(Device).where(Device.id.in_(device_ids)))
        await db.execute(delete(WorkLine).where(WorkLine.id.in_(line_ids)))


@pytest.mark.asyncio
async def test_postgresql_execution_identity_remains_unique_after_terminal_closure(
    integration_session_factory,
) -> None:
    async with integration_session_factory.begin() as db:
        _, _, _, binding = await _seed_topology(db)
        identity = uuid4().hex
        first = _command(binding, f"CMD-{identity}-1", CommandStatus.SUCCEEDED)
        second = _command(binding, f"CMD-{identity}-2", CommandStatus.PENDING)
        second.execution_ref_id = first.execution_ref_id
        db.add(first)
        await db.flush()
        db.add(second)
        with pytest.raises(IntegrityError):
            await db.flush()


@pytest.mark.asyncio
async def test_postgresql_concurrent_same_event_returns_one_duplicate(
    integration_session_factory,
) -> None:
    source_event_id = f"DEVICE-COMMAND-CONSTRAINT-EVENT-{uuid4().hex}"
    event = _event(source_event_id, marker="SAME")
    first_service = DeviceEvidenceService(session_factory=integration_session_factory)
    second_service = DeviceEvidenceService(session_factory=integration_session_factory)

    receipts = await asyncio.gather(
        first_service.accept_event(event),
        second_service.accept_event(event),
    )

    assert {receipt.duplicate for receipt in receipts} == {False, True}
    assert len({receipt.evidence_id for receipt in receipts}) == 1


@pytest.mark.asyncio
async def test_postgresql_concurrent_conflicting_event_persists_conflict(
    integration_session_factory,
) -> None:
    source_event_id = f"DEVICE-COMMAND-CONSTRAINT-EVENT-{uuid4().hex}"
    first_service = DeviceEvidenceService(session_factory=integration_session_factory)
    second_service = DeviceEvidenceService(session_factory=integration_session_factory)

    results = await asyncio.gather(
        first_service.accept_event(_event(source_event_id, marker="FIRST")),
        second_service.accept_event(_event(source_event_id, marker="SECOND")),
        return_exceptions=True,
    )

    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, DeviceEvidenceConflictError) for result in results) == 1
    async with integration_session_factory() as db:
        evidences = list(
            (await db.execute(select(InboundEvidence).where(InboundEvidence.source_identity == source_event_id)))
            .scalars()
            .all()
        )
        conflicts = list(
            (
                await db.execute(
                    select(InboundEvidenceConflict).where(InboundEvidenceConflict.source_identity == source_event_id)
                )
            )
            .scalars()
            .all()
        )
    assert len(evidences) == 1
    assert len(conflicts) == 1
    assert conflicts[0].first_evidence_id == evidences[0].id


@pytest.mark.asyncio
async def test_postgresql_rejects_second_active_epoch(integration_session_factory) -> None:
    async with integration_session_factory.begin() as db:
        line, _, _, _ = await _seed_topology(db)
        db.add(
            LineRunEpoch(
                epoch_code=f"EPOCH-DEVICE-COMMAND-CONSTRAINT-{uuid4().hex[:12]}",
                workline_id=line.id,
                plugin_key="device_command_test",
                plugin_version="1.0.0",
                flow_mode="TEST",
                topology_digest="c" * 64,
                configuration_digest="d" * 64,
                started_at=datetime(2026, 8, 13),
            )
        )
        with pytest.raises(IntegrityError):
            await db.flush()


@pytest.mark.asyncio
async def test_postgresql_closed_epoch_releases_active_generation_slot(integration_session_factory) -> None:
    async with integration_session_factory.begin() as db:
        line, _, first, _ = await _seed_topology(db)
        closed_at = datetime(2026, 8, 13, 0, 1)
        closed = await LineRunEpochService().close_active_epoch(
            db, workline_id=line.id, closed_at=closed_at, command_repository=device_command_repository
        )
        second = LineRunEpoch(
            epoch_code=f"EPOCH-DEVICE-COMMAND-CONSTRAINT-{uuid4().hex[:12]}",
            workline_id=line.id,
            plugin_key="device_command_test",
            plugin_version="1.0.1",
            flow_mode="TEST",
            topology_digest="c" * 64,
            configuration_digest="d" * 64,
            started_at=closed_at,
        )
        db.add(second)
        await db.flush()

        assert closed is first
        assert first.closed_at == closed_at
        assert second.id is not None


@pytest.mark.asyncio
async def test_postgresql_create_and_close_serialize_on_epoch(integration_session_factory) -> None:
    creation_holds_epoch = asyncio.Event()
    release_creation = asyncio.Event()

    class PausingEpochRepository(LineRunEpochRepository):
        async def get_binding_for_command_creation(self, db, *, line_run_epoch_id, device_code):
            binding = await super().get_binding_for_command_creation(
                db, line_run_epoch_id=line_run_epoch_id, device_code=device_code
            )
            creation_holds_epoch.set()
            await release_creation.wait()
            return binding

    async with integration_session_factory.begin() as db:
        line, _, epoch, binding = await _seed_topology(db)

    request = DeviceCommandRequest(
        device_code=binding.device_code,
        line_run_epoch_id=epoch.id,
        execution_ref_type="TEST_EXECUTION",
        execution_ref_id=f"EXEC-{uuid4().hex}",
        contract_key=binding.contract_key,
        contract_version=binding.contract_version,
        task_type="TEST_ACTION",
        params={},
        deadline_at=datetime(2026, 8, 13, 0, 0, 30),
    )
    command_service = DeviceCommandService(
        session_factory=integration_session_factory,
        epoch_repository=PausingEpochRepository(),
        clock=lambda: datetime(2026, 8, 13),
    )

    create_task = asyncio.create_task(command_service.create_command(request))
    await asyncio.wait_for(creation_holds_epoch.wait(), timeout=2)

    async def close_epoch():
        async with integration_session_factory.begin() as db:
            return await LineRunEpochService().close_active_epoch(
                db,
                workline_id=line.id,
                closed_at=datetime(2026, 8, 13, 0, 1),
                command_repository=device_command_repository,
            )

    close_task = asyncio.create_task(close_epoch())
    await asyncio.sleep(0.05)
    assert not close_task.done()
    release_creation.set()
    await asyncio.wait_for(create_task, timeout=2)
    with pytest.raises(ActiveLineRunEpochExistsError, match="sendable DeviceCommand"):
        await asyncio.wait_for(close_task, timeout=2)

    async with integration_session_factory() as db:
        persisted_epoch = await db.get(LineRunEpoch, epoch.id)
        assert persisted_epoch.status == "ACTIVE"


@pytest.mark.asyncio
async def test_postgresql_dispatch_binding_read_does_not_wait_for_creation_lock(integration_session_factory) -> None:
    repository = LineRunEpochRepository()
    async with integration_session_factory.begin() as db:
        _, _, epoch, binding = await _seed_topology(db)

    lock_acquired = asyncio.Event()
    release_lock = asyncio.Event()

    async def hold_creation_lock() -> None:
        async with integration_session_factory.begin() as db:
            await repository.get_binding_for_command_creation(
                db, line_run_epoch_id=epoch.id, device_code=binding.device_code
            )
            lock_acquired.set()
            await release_lock.wait()

    holder = asyncio.create_task(hold_creation_lock())
    await asyncio.wait_for(lock_acquired.wait(), timeout=2)
    async with integration_session_factory.begin() as db:
        dispatch_binding = await asyncio.wait_for(
            repository.get_binding_for_dispatch(db, line_run_epoch_id=epoch.id, device_code=binding.device_code),
            timeout=1,
        )
    release_lock.set()
    await holder
    assert dispatch_binding.id == binding.id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        CommandStatus.PENDING,
        CommandStatus.DISPATCHING,
        CommandStatus.ACKNOWLEDGED,
        CommandStatus.RECONCILING,
    ],
)
async def test_postgresql_unclosed_statuses_hold_single_device_slot(
    integration_session_factory,
    status: CommandStatus,
) -> None:
    async with integration_session_factory.begin() as db:
        _, _, _, binding = await _seed_topology(db)
        identity = uuid4().hex
        db.add(_command(binding, f"CMD-{identity}-1", status))
        await db.flush()
        db.add(_command(binding, f"CMD-{identity}-2", CommandStatus.PENDING))
        with pytest.raises(IntegrityError):
            await db.flush()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [CommandStatus.SUCCEEDED, CommandStatus.FAILED, CommandStatus.TIMED_OUT])
async def test_postgresql_terminal_statuses_release_device_slot(
    integration_session_factory,
    status: CommandStatus,
) -> None:
    async with integration_session_factory.begin() as db:
        _, _, _, binding = await _seed_topology(db)
        identity = uuid4().hex
        db.add(_command(binding, f"CMD-{identity}-1", status))
        db.add(_command(binding, f"CMD-{identity}-2", CommandStatus.PENDING))
        await db.flush()
