"""PostgreSQL 对 DeviceCommand 并发不变量的最终裁决。"""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError

from src.app.device.contracts import DeviceCommandRequest, EcsDeviceEventReport
from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.device.models.device import Device
from src.app.device.repositories.command_repository import device_command_repository
from src.app.device.services.device_command_service import (
    DeviceCommandCapacityError,
    DeviceCommandIdentityConflictError,
    DeviceCommandService,
)
from src.app.device.services.device_evidence_service import (
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
        configuration_snapshot_json={},
        started_at=datetime(2026, 8, 13),
    )
    db.add(epoch)
    await db.flush()
    binding = LineRunEpochDeviceBinding(
        line_run_epoch_id=epoch.id,
        device_id=device.id,
        device_code=device.device_code,
        device_role=device.device_role,
        endpoint_base_url="http://ecs-constraints:8080",
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
        material_execution_id=None,
        contract_key=binding.contract_key,
        contract_version=binding.contract_version,
        task_type="TEST_ACTION",
        params={},
        payload_digest=code.ljust(64, "x")[:64],
        deadline_at=datetime(2026, 8, 14),
        status=status,
    )


def _manual_command(identity: str, code: str, status: CommandStatus = CommandStatus.PENDING) -> DeviceCommand:
    return DeviceCommand(
        command_code=code,
        device_code=f"RS-MOCK-PLACEMENT-{identity[-8:]}",
        line_run_epoch_id=None,
        device_binding_id=None,
        execution_ref_type="MANUAL_DEBUG",
        execution_ref_id=identity,
        material_execution_id=None,
        contract_key="rough_sorter.placement_device",
        contract_version="1.0",
        task_type="PICK_AND_PUT",
        params={"target_code": "OUTLET-1"},
        payload_digest=identity.ljust(64, "x")[:64],
        deadline_at=datetime(2026, 8, 24),
        endpoint_base_url="http://ecs-mock:8080",
        command_timeout_ms=30_000,
        status=status,
    )


def _event(device_code: str, *, marker: str) -> EcsDeviceEventReport:
    return EcsDeviceEventReport(
        device_code=device_code,
        event_type="SCAN_COMPLETED",
        timestamp=1_786_579_204_000,
        data={"location": f"STATION-{marker}", "barcode": f"BARCODE-{marker}"},
    )


class _StaticEventEpochRepository:
    def __init__(self, binding: SimpleNamespace) -> None:
        self._binding = binding

    async def get_active_binding_for_device(self, _db: object, _device_code: str) -> SimpleNamespace:
        return self._binding


class _BlockingEventEpochRepository(_StaticEventEpochRepository):
    def __init__(self, binding: SimpleNamespace, *, reached: asyncio.Event, release: asyncio.Event) -> None:
        super().__init__(binding)
        self._reached = reached
        self._release = release

    async def get_active_binding_for_device(self, _db: object, _device_code: str) -> SimpleNamespace:
        self._reached.set()
        await self._release.wait()
        return self._binding


@pytest_asyncio.fixture(autouse=True)
async def cleanup_device_command_constraint_rows(integration_session_factory):
    """共享测试库中只回收本文件创建的 DeviceCommand 约束证据。"""

    yield

    async with integration_session_factory.begin() as db:
        evidence_ids = select(InboundEvidence.id).where(InboundEvidence.device_code.like("ARM-DEVICE-COMMAND-EVENT-%"))
        epoch_ids = select(LineRunEpoch.id).where(LineRunEpoch.epoch_code.like("EPOCH-DEVICE-COMMAND-CONSTRAINT-%"))
        device_ids = select(Device.id).where(Device.device_code.like("ARM-DEVICE-COMMAND-CONSTRAINT-%"))
        line_ids = select(WorkLine.id).where(WorkLine.line_code.like("LINE-DEVICE-COMMAND-CONSTRAINT-%"))
        await db.execute(
            delete(InboundEvidenceConflict).where(InboundEvidenceConflict.first_evidence_id.in_(evidence_ids))
        )
        await db.execute(
            delete(DeviceCommand).where(
                DeviceCommand.execution_ref_type == "MANUAL_DEBUG",
                DeviceCommand.execution_ref_id.like("DEVICE-COMMAND-CONSTRAINT-MANUAL-%"),
            )
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
async def test_postgresql_accepts_complete_manual_debug_command_without_epoch_or_device_master(
    integration_session_factory,
) -> None:
    identity = f"DEVICE-COMMAND-CONSTRAINT-MANUAL-{uuid4().hex}"
    async with integration_session_factory.begin() as db:
        command = _manual_command(identity, f"CMD-{uuid4().hex}")
        db.add(command)
        await db.flush()

        assert command.id is not None
        assert command.line_run_epoch_id is None
        assert command.device_binding_id is None


@pytest.mark.asyncio
async def test_postgresql_rejects_incomplete_manual_debug_context(integration_session_factory) -> None:
    identity = f"DEVICE-COMMAND-CONSTRAINT-MANUAL-{uuid4().hex}"
    async with integration_session_factory.begin() as db:
        command = _manual_command(identity, f"CMD-{uuid4().hex}")
        command.endpoint_base_url = None
        db.add(command)
        with pytest.raises(IntegrityError):
            await db.flush()


@pytest.mark.asyncio
async def test_postgresql_manual_debug_identity_remains_unique_without_epoch(integration_session_factory) -> None:
    identity = f"DEVICE-COMMAND-CONSTRAINT-MANUAL-{uuid4().hex}"
    async with integration_session_factory.begin() as db:
        first = _manual_command(identity, f"CMD-{uuid4().hex}-1", CommandStatus.SUCCEEDED)
        second = _manual_command(identity, f"CMD-{uuid4().hex}-2", CommandStatus.PENDING)
        second.device_code = f"RS-MOCK-PLACEMENT-{uuid4().hex[:8]}"
        db.add(first)
        await db.flush()
        db.add(second)
        with pytest.raises(IntegrityError):
            await db.flush()


@pytest.mark.asyncio
async def test_postgresql_concurrent_manual_debug_same_identity_replays_original_handle(
    integration_session_factory,
) -> None:
    identity = f"DEVICE-COMMAND-CONSTRAINT-MANUAL-{uuid4().hex}"
    request = {
        "client_request_id": identity,
        "endpoint_base_url": "http://ecs-mock:8080",
        "device_code": f"RS-MOCK-PLACEMENT-{uuid4().hex[:8]}",
        "contract_key": "rough_sorter.placement_device",
        "contract_version": "1.0",
        "command_timeout_ms": 30_000,
        "task_type": "PICK_AND_PUT",
        "params": {"target_code": "OUTLET-1"},
        "trace_id": None,
    }
    first_service = DeviceCommandService(session_factory=integration_session_factory)
    second_service = DeviceCommandService(session_factory=integration_session_factory)

    results = await asyncio.gather(
        first_service.create_manual_debug_command(**request),
        second_service.create_manual_debug_command(**request),
        return_exceptions=True,
    )

    assert all(not isinstance(result, Exception) for result in results)
    assert len({result.command_code for result in results if not isinstance(result, Exception)}) == 1
    async with integration_session_factory() as db:
        commands = list(
            (
                await db.execute(
                    select(DeviceCommand).where(
                        DeviceCommand.execution_ref_type == "MANUAL_DEBUG",
                        DeviceCommand.execution_ref_id == identity,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(commands) == 1


@pytest.mark.asyncio
async def test_postgresql_manual_debug_same_identity_rejects_different_device(
    integration_session_factory,
) -> None:
    identity = f"DEVICE-COMMAND-CONSTRAINT-MANUAL-{uuid4().hex}"
    request = {
        "client_request_id": identity,
        "endpoint_base_url": "http://ecs-mock:8080",
        "contract_key": "rough_sorter.placement_device",
        "contract_version": "1.0",
        "command_timeout_ms": 30_000,
        "task_type": "PICK_AND_PUT",
        "params": {"target_code": "OUTLET-1"},
        "trace_id": None,
    }
    service = DeviceCommandService(session_factory=integration_session_factory)

    await service.create_manual_debug_command(
        **request,
        device_code=f"RS-MOCK-PLACEMENT-{uuid4().hex[:8]}",
    )
    with pytest.raises(DeviceCommandIdentityConflictError):
        await service.create_manual_debug_command(
            **request,
            device_code=f"RS-MOCK-PLACEMENT-{uuid4().hex[:8]}",
        )


@pytest.mark.asyncio
async def test_postgresql_concurrent_manual_debug_same_identity_different_devices_conflict(
    integration_session_factory,
) -> None:
    identity = f"DEVICE-COMMAND-CONSTRAINT-MANUAL-{uuid4().hex}"
    request = {
        "client_request_id": identity,
        "endpoint_base_url": "http://ecs-mock:8080",
        "contract_key": "rough_sorter.placement_device",
        "contract_version": "1.0",
        "command_timeout_ms": 30_000,
        "task_type": "PICK_AND_PUT",
        "params": {"target_code": "OUTLET-1"},
        "trace_id": None,
    }
    first_service = DeviceCommandService(session_factory=integration_session_factory)
    second_service = DeviceCommandService(session_factory=integration_session_factory)

    results = await asyncio.gather(
        first_service.create_manual_debug_command(
            **request,
            device_code=f"RS-MOCK-PLACEMENT-{uuid4().hex[:8]}",
        ),
        second_service.create_manual_debug_command(
            **request,
            device_code=f"RS-MOCK-PLACEMENT-{uuid4().hex[:8]}",
        ),
        return_exceptions=True,
    )

    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, DeviceCommandIdentityConflictError) for result in results) == 1
    async with integration_session_factory() as db:
        commands = list(
            (
                await db.execute(
                    select(DeviceCommand).where(
                        DeviceCommand.execution_ref_type == "MANUAL_DEBUG",
                        DeviceCommand.execution_ref_id == identity,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(commands) == 1


@pytest.mark.asyncio
async def test_postgresql_concurrent_manual_debug_different_identities_report_device_capacity(
    integration_session_factory,
) -> None:
    device_code = f"RS-MOCK-PLACEMENT-{uuid4().hex[:8]}"
    request = {
        "endpoint_base_url": "http://ecs-mock:8080",
        "device_code": device_code,
        "contract_key": "rough_sorter.placement_device",
        "contract_version": "1.0",
        "command_timeout_ms": 30_000,
        "task_type": "PICK_AND_PUT",
        "params": {"target_code": "OUTLET-1"},
        "trace_id": None,
    }
    first_service = DeviceCommandService(session_factory=integration_session_factory)
    second_service = DeviceCommandService(session_factory=integration_session_factory)

    results = await asyncio.gather(
        first_service.create_manual_debug_command(
            **request,
            client_request_id=f"DEVICE-COMMAND-CONSTRAINT-MANUAL-{uuid4().hex}",
        ),
        second_service.create_manual_debug_command(
            **request,
            client_request_id=f"DEVICE-COMMAND-CONSTRAINT-MANUAL-{uuid4().hex}",
        ),
        return_exceptions=True,
    )

    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, DeviceCommandCapacityError) for result in results) == 1
    async with integration_session_factory() as db:
        commands = list(
            (
                await db.execute(
                    select(DeviceCommand).where(
                        DeviceCommand.execution_ref_type == "MANUAL_DEBUG",
                        DeviceCommand.device_code == device_code,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(commands) == 1


@pytest.mark.asyncio
async def test_postgresql_concurrent_same_event_returns_one_duplicate(
    integration_session_factory,
) -> None:
    device_code = f"ARM-DEVICE-COMMAND-EVENT-{uuid4().hex[:12]}"
    event = _event(device_code, marker="SAME")
    first_service = DeviceEvidenceService(session_factory=integration_session_factory)
    second_service = DeviceEvidenceService(session_factory=integration_session_factory)

    receipts = await asyncio.gather(
        first_service.accept_event(event),
        second_service.accept_event(event),
    )

    assert {receipt.duplicate for receipt in receipts} == {False, True}
    assert len({receipt.evidence_id for receipt in receipts}) == 1


@pytest.mark.asyncio
async def test_postgresql_same_event_remains_duplicate_when_binding_contract_switches_during_ingress(
    integration_session_factory,
) -> None:
    async with integration_session_factory.begin() as db:
        _, _, first_epoch, _ = await _seed_topology(db)
        _, _, second_epoch, _ = await _seed_topology(db)
    assert first_epoch.id is not None
    assert second_epoch.id is not None

    device_code = f"ARM-DEVICE-COMMAND-EVENT-{uuid4().hex[:12]}"
    event = _event(device_code, marker="EPOCH-SWITCH")
    first_binding = SimpleNamespace(
        line_run_epoch_id=first_epoch.id,
        contract_key="arm.pick",
        contract_version="2.0",
    )
    second_binding = SimpleNamespace(
        line_run_epoch_id=second_epoch.id,
        contract_key="arm.pick",
        contract_version="3.0",
    )
    reached = asyncio.Event()
    release = asyncio.Event()
    first_service = DeviceEvidenceService(
        session_factory=integration_session_factory,
        epoch_repository=_BlockingEventEpochRepository(first_binding, reached=reached, release=release),  # type: ignore[arg-type]
    )
    second_service = DeviceEvidenceService(
        session_factory=integration_session_factory,
        epoch_repository=_StaticEventEpochRepository(second_binding),  # type: ignore[arg-type]
    )

    first_task = asyncio.create_task(first_service.accept_event(event))
    await asyncio.wait_for(reached.wait(), timeout=1)
    try:
        second_receipt = await second_service.accept_event(event)
    finally:
        release.set()
    first_receipt = await first_task

    assert {first_receipt.duplicate, second_receipt.duplicate} == {False, True}
    assert first_receipt.evidence_id == second_receipt.evidence_id
    async with integration_session_factory() as db:
        evidence = (
            await db.execute(
                select(InboundEvidence).where(InboundEvidence.source_identity == first_receipt.source_event_id)
            )
        ).scalar_one()
        conflicts = list(
            (
                await db.execute(
                    select(InboundEvidenceConflict).where(
                        InboundEvidenceConflict.source_identity == first_receipt.source_event_id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert evidence.line_run_epoch_id == second_epoch.id
    assert evidence.contract_version == "3.0"
    assert conflicts == []


@pytest.mark.asyncio
async def test_postgresql_concurrent_distinct_event_payloads_persist_independently(
    integration_session_factory,
) -> None:
    device_code = f"ARM-DEVICE-COMMAND-EVENT-{uuid4().hex[:12]}"
    first_service = DeviceEvidenceService(session_factory=integration_session_factory)
    second_service = DeviceEvidenceService(session_factory=integration_session_factory)

    results = await asyncio.gather(
        first_service.accept_event(_event(device_code, marker="FIRST")),
        second_service.accept_event(_event(device_code, marker="SECOND")),
        return_exceptions=True,
    )

    assert all(not isinstance(result, Exception) for result in results)
    async with integration_session_factory() as db:
        evidences = list(
            (await db.execute(select(InboundEvidence).where(InboundEvidence.device_code == device_code)))
            .scalars()
            .all()
        )
        conflicts = list(
            (
                await db.execute(
                    select(InboundEvidenceConflict).where(
                        InboundEvidenceConflict.source_identity.in_(
                            [result.source_event_id for result in results if not isinstance(result, Exception)]
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(evidences) == 2
    assert conflicts == []


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
                configuration_snapshot_json={},
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
            configuration_snapshot_json={},
            started_at=closed_at,
        )
        db.add(second)
        await db.flush()

        assert closed is first
        assert first.closed_at == closed_at
        assert second.id is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [CommandStatus.ACKNOWLEDGED, CommandStatus.RECONCILING])
async def test_postgresql_unclosed_result_states_block_epoch_close(
    integration_session_factory,
    status: CommandStatus,
) -> None:
    async with integration_session_factory.begin() as db:
        line, _, epoch, binding = await _seed_topology(db)
        db.add(_command(binding, f"CMD-{uuid4().hex}", status))
        await db.flush()

        with pytest.raises(ActiveLineRunEpochExistsError, match="unclosed DeviceCommand"):
            await LineRunEpochService().close_active_epoch(
                db,
                workline_id=line.id,
                closed_at=datetime(2026, 8, 13, 0, 1),
                command_repository=device_command_repository,
            )

        assert epoch.status == "ACTIVE"


@pytest.mark.asyncio
async def test_postgresql_epoch_close_waits_for_concurrent_result_terminal_transition(
    integration_session_factory,
) -> None:
    async with integration_session_factory.begin() as db:
        line, _, epoch, binding = await _seed_topology(db)
        command = _command(binding, f"CMD-{uuid4().hex}", CommandStatus.ACKNOWLEDGED)
        db.add(command)
        await db.flush()
        command_code = command.command_code

    result_holds_command = asyncio.Event()
    release_result = asyncio.Event()

    async def settle_result() -> None:
        async with integration_session_factory.begin() as db:
            locked = await device_command_repository.get_by_command_code(db, command_code, for_update=True)
            assert locked is not None
            result_holds_command.set()
            await release_result.wait()
            locked.transition_to(CommandStatus.SUCCEEDED)

    result_task = asyncio.create_task(settle_result())
    await asyncio.wait_for(result_holds_command.wait(), timeout=2)
    close_backend_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()

    async def close_epoch() -> LineRunEpoch | None:
        async with integration_session_factory.begin() as db:
            backend_pid = await db.scalar(text("SELECT pg_backend_pid()"))
            assert isinstance(backend_pid, int)
            close_backend_pid.set_result(backend_pid)
            return await LineRunEpochService().close_active_epoch(
                db,
                workline_id=line.id,
                closed_at=datetime(2026, 8, 13, 0, 1),
                command_repository=device_command_repository,
            )

    close_task = asyncio.create_task(close_epoch())
    backend_pid = await asyncio.wait_for(close_backend_pid, timeout=2)
    try:
        lock_wait_deadline = asyncio.get_running_loop().time() + 10
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
                if close_task.done():
                    pytest.fail(
                        "epoch close completed before PostgreSQL command-row lock wait; "
                        f"last_wait_state={last_wait_state!r}, exception={close_task.exception()!r}"
                    )
                if asyncio.get_running_loop().time() >= lock_wait_deadline:
                    pytest.fail(
                        "epoch close did not enter PostgreSQL lock wait before deadline; "
                        f"last_wait_state={last_wait_state!r}"
                    )
                await observer_db.rollback()
                await asyncio.sleep(0.01)

        assert last_wait_state is not None
        assert last_wait_state["state"] == "active"
        assert not close_task.done()
    finally:
        release_result.set()
    await asyncio.wait_for(result_task, timeout=2)
    closed = await asyncio.wait_for(close_task, timeout=2)

    assert closed is not None
    assert closed.id == epoch.id
    assert closed.status == "CLOSED"


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
        material_execution_id=None,
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
    with pytest.raises(ActiveLineRunEpochExistsError, match="unclosed DeviceCommand"):
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
