"""PostgreSQL 对 DeviceCommand 并发不变量的最终裁决。"""

from __future__ import annotations

import asyncio
from datetime import datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from src.app.device.contracts import EcsDeviceEvent
from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.device.models.device import Device
from src.app.device.models.evidence import DeviceEvidence, DeviceEvidenceConflict
from src.app.device.services.device_evidence_service import (
    DeviceEvidenceConflictError,
    DeviceEvidenceService,
)
from src.app.workline.models.line_run_epoch import LineRunEpoch, LineRunEpochDeviceBinding
from src.app.workline.models.workline import LineType, WorkLine


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
        evidence_ids = select(DeviceEvidence.id).where(
            DeviceEvidence.source_event_id.like("DEVICE-COMMAND-CONSTRAINT-EVENT-%")
        )
        epoch_ids = select(LineRunEpoch.id).where(LineRunEpoch.epoch_code.like("EPOCH-DEVICE-COMMAND-CONSTRAINT-%"))
        device_ids = select(Device.id).where(Device.device_code.like("ARM-DEVICE-COMMAND-CONSTRAINT-%"))
        line_ids = select(WorkLine.id).where(WorkLine.line_code.like("LINE-DEVICE-COMMAND-CONSTRAINT-%"))
        await db.execute(
            delete(DeviceEvidenceConflict).where(DeviceEvidenceConflict.first_evidence_id.in_(evidence_ids))
        )
        await db.execute(delete(DeviceCommand).where(DeviceCommand.line_run_epoch_id.in_(epoch_ids)))
        await db.execute(delete(DeviceEvidence).where(DeviceEvidence.id.in_(evidence_ids)))
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
            (await db.execute(select(DeviceEvidence).where(DeviceEvidence.source_event_id == source_event_id)))
            .scalars()
            .all()
        )
        conflicts = list(
            (
                await db.execute(
                    select(DeviceEvidenceConflict).where(DeviceEvidenceConflict.source_event_id == source_event_id)
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
                topology_digest="c" * 64,
                configuration_digest="d" * 64,
                started_at=datetime(2026, 8, 13),
            )
        )
        with pytest.raises(IntegrityError):
            await db.flush()


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
