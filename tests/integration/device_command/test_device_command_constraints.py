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
from wes_plugin_sdk import PluginDefinition

from src.app.device.contracts import DeviceCommandRequest, EcsDeviceEventReport, EcsDeviceStatus
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
from src.app.workline.activation import WorkLineDeviceBinding
from src.app.workline.models.workline import LineType, WorkLine
from src.app.workline.repositories.workline_repository import WorkLineRepository
from src.app.workline.services.workline_configuration_service import WorkLineConfigurationService
from src.core.exceptions import BusinessException


async def _seed_topology(db) -> tuple[WorkLine, Device, WorkLineDeviceBinding]:
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
    )
    db.add(device)
    await db.flush()
    line.plugin_key = "device_command_test"
    line.plugin_version = "1.0.0"
    line.flow_mode = "TEST"
    line.is_active = True
    line.config = {"device_bindings": {"ROBOT_ARM": device.device_code}}
    binding = WorkLineDeviceBinding(
        workline_id=line.id,
        device_id=device.id,
        device_code=device.device_code,
        device_role="ROBOT_ARM",
        endpoint_base_url="http://ecs-constraints:8080",
        contract_key="arm.pick",
        contract_version="2.0",
        status_max_age_ms=1000,
        command_timeout_ms=30000,
    )
    line.device_contracts = {
        device.device_code: {
            "device_id": device.id,
            "endpoint_base_url": binding.endpoint_base_url,
            "contract_key": binding.contract_key,
            "contract_version": binding.contract_version,
            "status_max_age_ms": binding.status_max_age_ms,
            "command_timeout_ms": binding.command_timeout_ms,
        }
    }
    await db.flush()
    return line, device, binding


def _command(binding: WorkLineDeviceBinding, code: str, status: CommandStatus) -> DeviceCommand:
    return DeviceCommand(
        command_code=code,
        device_code=binding.device_code,
        workline_id=binding.workline_id,
        endpoint_base_url=binding.endpoint_base_url,
        command_timeout_ms=binding.command_timeout_ms,
        status_max_age_ms=binding.status_max_age_ms,
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
        workline_id=None,
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
        execution_reason="现场供应商联调",
        created_by=42,
        status=status,
    )


def _event_debug_command(identity: str, code: str, status: CommandStatus = CommandStatus.PENDING) -> DeviceCommand:
    return DeviceCommand(
        command_code=code,
        device_code=f"STATION-SCAN-{identity[-8:]}",
        workline_id=None,
        execution_ref_type="EVENT_DEBUG",
        execution_ref_id=identity,
        material_execution_id=None,
        contract_key="third_party_integration",
        contract_version="1.1",
        task_type="MOVE_FORWARD",
        params={"barcode": "NHW002069-B"},
        payload_digest=identity.ljust(64, "x")[:64],
        deadline_at=datetime(2026, 8, 25),
        endpoint_base_url="http://10.24.209.26:8080",
        command_timeout_ms=30_000,
        execution_reason=f"ECS_EVENT_DEBUG:{identity}",
        created_by=None,
        status=status,
    )


def _event(device_code: str, *, marker: str) -> EcsDeviceEventReport:
    return EcsDeviceEventReport(
        device_code=device_code,
        event_type="SCAN_COMPLETED",
        timestamp=1_786_579_204_000,
        data={"location": f"STATION-{marker}", "barcode": f"BARCODE-{marker}"},
    )


class _StaticEventWorkLineRepository:
    def __init__(self, binding: SimpleNamespace) -> None:
        self._binding = binding

    async def get_active_binding_for_device(self, _db: object, _device_code: str) -> SimpleNamespace:
        return self._binding


class _BlockingEventWorkLineRepository(_StaticEventWorkLineRepository):
    def __init__(self, binding: SimpleNamespace, *, reached: asyncio.Event, release: asyncio.Event) -> None:
        super().__init__(binding)
        self._reached = reached
        self._release = release

    async def get_active_binding_for_device(self, _db: object, _device_code: str) -> SimpleNamespace:
        self._reached.set()
        await self._release.wait()
        return self._binding


class _ManualDebugAdapter:
    async def fetch_status(self, device_code: str) -> EcsDeviceStatus:
        return EcsDeviceStatus.model_validate(
            {
                "device": {
                    "device_code": device_code,
                    "device_name": device_code,
                    "device_type": "ROBOTIC_ARM",
                    "role": "PLACEMENT_DEVICE",
                    "supported_commands": ["PICK_AND_PUT"],
                    "supported_events": [],
                },
                "state": {
                    "device_code": device_code,
                    "mode": "AUTO",
                    "status": "IDLE",
                    "is_online": True,
                    "current_command_code": None,
                    "scenario": "success",
                    "updated_at": 1_787_475_600_000,
                },
            }
        )


class _ManualDebugAdapterProvider:
    async def get_adapter(self, _endpoint_base_url: str) -> _ManualDebugAdapter:
        return _ManualDebugAdapter()


def _manual_debug_service(session_factory) -> DeviceCommandService:
    return DeviceCommandService(
        session_factory=session_factory,
        adapter_provider=_ManualDebugAdapterProvider(),  # type: ignore[arg-type]
    )


@pytest_asyncio.fixture(autouse=True)
async def cleanup_device_command_constraint_rows(integration_session_factory):
    """共享测试库中只回收本文件创建的 DeviceCommand 约束证据。"""

    yield

    async with integration_session_factory.begin() as db:
        evidence_ids = select(InboundEvidence.id).where(InboundEvidence.device_code.like("ARM-DEVICE-COMMAND-%"))
        device_ids = select(Device.id).where(Device.device_code.like("ARM-DEVICE-COMMAND-CONSTRAINT-%"))
        line_ids = select(WorkLine.id).where(WorkLine.line_code.like("LINE-DEVICE-COMMAND-CONSTRAINT-%"))
        await db.execute(
            delete(InboundEvidenceConflict).where(InboundEvidenceConflict.first_evidence_id.in_(evidence_ids))
        )
        await db.execute(
            delete(DeviceCommand).where(
                DeviceCommand.execution_ref_type.in_(["MANUAL_DEBUG", "EVENT_DEBUG"]),
                DeviceCommand.execution_ref_id.like("DEVICE-COMMAND-CONSTRAINT-%"),
            )
        )
        await db.execute(delete(DeviceCommand).where(DeviceCommand.workline_id.in_(line_ids)))
        await db.execute(delete(InboundEvidence).where(InboundEvidence.id.in_(evidence_ids)))
        await db.execute(delete(Device).where(Device.id.in_(device_ids)))
        await db.execute(delete(WorkLine).where(WorkLine.id.in_(line_ids)))


@pytest.mark.asyncio
async def test_postgresql_execution_identity_remains_unique_after_terminal_closure(
    integration_session_factory,
) -> None:
    async with integration_session_factory.begin() as db:
        _, _, binding = await _seed_topology(db)
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
async def test_postgresql_accepts_complete_manual_debug_command_without_workline_or_device_master(
    integration_session_factory,
) -> None:
    identity = f"DEVICE-COMMAND-CONSTRAINT-MANUAL-{uuid4().hex}"
    async with integration_session_factory.begin() as db:
        command = _manual_command(identity, f"CMD-{uuid4().hex}")
        db.add(command)
        await db.flush()

        assert command.id is not None
        assert command.workline_id is None


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
async def test_postgresql_rejects_manual_debug_material_execution_binding(integration_session_factory) -> None:
    identity = f"DEVICE-COMMAND-CONSTRAINT-MANUAL-{uuid4().hex}"
    async with integration_session_factory.begin() as db:
        command = _manual_command(identity, f"CMD-{uuid4().hex}")
        command.material_execution_id = 2**31 - 1
        db.add(command)

        with pytest.raises(IntegrityError) as error:
            await db.flush()

        assert "device_command_execution_context_complete" in str(error.value.orig)


@pytest.mark.asyncio
@pytest.mark.parametrize(("reason", "created_by"), [(None, 42), ("现场供应商联调", None)])
async def test_postgresql_rejects_incomplete_manual_debug_audit(
    integration_session_factory,
    reason: str | None,
    created_by: int | None,
) -> None:
    identity = f"DEVICE-COMMAND-CONSTRAINT-MANUAL-{uuid4().hex}"
    async with integration_session_factory.begin() as db:
        command = _manual_command(identity, f"CMD-{uuid4().hex}")
        command.execution_reason = reason
        command.created_by = created_by
        db.add(command)
        with pytest.raises(IntegrityError):
            await db.flush()


@pytest.mark.asyncio
async def test_postgresql_rejects_reason_on_non_manual_command(integration_session_factory) -> None:
    async with integration_session_factory.begin() as db:
        _, _, binding = await _seed_topology(db)
        command = _command(binding, f"CMD-{uuid4().hex}", CommandStatus.PENDING)
        command.execution_reason = "不允许"
        db.add(command)
        with pytest.raises(IntegrityError):
            await db.flush()


@pytest.mark.asyncio
async def test_postgresql_accepts_event_debug_command_without_workline_or_creator(integration_session_factory) -> None:
    identity = f"DEVICE-COMMAND-CONSTRAINT-EVENT-{uuid4().hex}"
    async with integration_session_factory.begin() as db:
        command = _event_debug_command(identity, f"CMD-{uuid4().hex}")
        db.add(command)
        await db.flush()

        assert command.id is not None
        assert command.workline_id is None
        assert command.created_by is None


@pytest.mark.asyncio
@pytest.mark.parametrize(("reason", "created_by"), [(None, None), ("ECS_EVENT_DEBUG:EVT-1", 42)])
async def test_postgresql_rejects_incomplete_event_debug_audit(
    integration_session_factory,
    reason: str | None,
    created_by: int | None,
) -> None:
    identity = f"DEVICE-COMMAND-CONSTRAINT-EVENT-{uuid4().hex}"
    async with integration_session_factory.begin() as db:
        command = _event_debug_command(identity, f"CMD-{uuid4().hex}")
        command.execution_reason = reason
        command.created_by = created_by
        db.add(command)
        with pytest.raises(IntegrityError):
            await db.flush()


@pytest.mark.asyncio
async def test_postgresql_event_debug_identity_remains_unique_without_workline(integration_session_factory) -> None:
    identity = f"DEVICE-COMMAND-CONSTRAINT-EVENT-{uuid4().hex}"
    async with integration_session_factory.begin() as db:
        first = _event_debug_command(identity, f"CMD-{uuid4().hex}-1", CommandStatus.SUCCEEDED)
        second = _event_debug_command(identity, f"CMD-{uuid4().hex}-2", CommandStatus.PENDING)
        second.device_code = f"STATION-SCAN-{uuid4().hex[:8]}"
        db.add(first)
        await db.flush()
        db.add(second)
        with pytest.raises(IntegrityError):
            await db.flush()


@pytest.mark.asyncio
async def test_postgresql_manual_debug_identity_remains_unique_without_workline(integration_session_factory) -> None:
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
        "execution_reason": "现场供应商联调",
        "created_by": 42,
    }
    first_service = _manual_debug_service(integration_session_factory)
    second_service = _manual_debug_service(integration_session_factory)

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
        "execution_reason": "现场供应商联调",
        "created_by": 42,
    }
    service = _manual_debug_service(integration_session_factory)

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
        "execution_reason": "现场供应商联调",
        "created_by": 42,
    }
    first_service = _manual_debug_service(integration_session_factory)
    second_service = _manual_debug_service(integration_session_factory)

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
        "execution_reason": "现场供应商联调",
        "created_by": 42,
    }
    first_service = _manual_debug_service(integration_session_factory)
    second_service = _manual_debug_service(integration_session_factory)

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
    async with integration_session_factory.begin() as db:
        _, device, _ = await _seed_topology(db)
        device_code = device.device_code
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
        first_line, _, _ = await _seed_topology(db)
        second_line, _, _ = await _seed_topology(db)
    assert first_line.id is not None
    assert second_line.id is not None

    device_code = f"ARM-DEVICE-COMMAND-EVENT-{uuid4().hex[:12]}"
    event = _event(device_code, marker="EPOCH-SWITCH")
    first_binding = SimpleNamespace(
        workline_id=first_line.id,
        contract_key="arm.pick",
        contract_version="2.0",
    )
    second_binding = SimpleNamespace(
        workline_id=second_line.id,
        contract_key="arm.pick",
        contract_version="3.0",
    )
    reached = asyncio.Event()
    release = asyncio.Event()
    first_service = DeviceEvidenceService(
        session_factory=integration_session_factory,
        workline_repository=_BlockingEventWorkLineRepository(first_binding, reached=reached, release=release),  # type: ignore[arg-type]
    )
    second_service = DeviceEvidenceService(
        session_factory=integration_session_factory,
        workline_repository=_StaticEventWorkLineRepository(second_binding),  # type: ignore[arg-type]
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
    assert evidence.workline_id == second_line.id
    assert evidence.contract_version == "3.0"
    assert conflicts == []


@pytest.mark.asyncio
async def test_postgresql_concurrent_distinct_event_payloads_persist_independently(
    integration_session_factory,
) -> None:
    async with integration_session_factory.begin() as db:
        _, device, _ = await _seed_topology(db)
        device_code = device.device_code
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
@pytest.mark.parametrize("status", [CommandStatus.ACKNOWLEDGED, CommandStatus.RECONCILING])
async def test_postgresql_unclosed_result_states_block_workline_close(
    integration_session_factory,
    status: CommandStatus,
) -> None:
    async with integration_session_factory.begin() as db:
        line, _, binding = await _seed_topology(db)
        db.add(_command(binding, f"CMD-{uuid4().hex}", status))
        await db.flush()

        with pytest.raises(BusinessException):
            await WorkLineConfigurationService(
                definitions=(
                    PluginDefinition(
                        plugin_key="device_command_test",
                        plugin_version="1.0.0",
                        display_name="Test",
                        supported_line_types=("AUTO",),
                    ),
                )
            ).deactivate(db, workline_id=line.id, version=line.version)

        assert line.is_active


@pytest.mark.asyncio
async def test_postgresql_stop_rejects_pending_terminal_result_then_allows_after_commit(integration_session_factory):
    async with integration_session_factory.begin() as db:
        line, _, binding = await _seed_topology(db)
        command = _command(binding, f"CMD-{uuid4().hex}", CommandStatus.ACKNOWLEDGED)
        db.add(command)
    async with integration_session_factory() as db:
        with pytest.raises(BusinessException):
            await WorkLineConfigurationService(
                definitions=(
                    PluginDefinition(
                        plugin_key="device_command_test",
                        plugin_version="1.0.0",
                        display_name="Test",
                        supported_line_types=("AUTO",),
                    ),
                )
            ).deactivate(db, workline_id=line.id, version=line.version)
    async with integration_session_factory.begin() as db:
        persisted = await device_command_repository.get_by_command_code(db, command.command_code, for_update=True)
        persisted.transition_to(CommandStatus.SUCCEEDED)
    async with integration_session_factory() as db:
        stopped = await WorkLineConfigurationService(
            definitions=(
                PluginDefinition(
                    plugin_key="device_command_test",
                    plugin_version="1.0.0",
                    display_name="Test",
                    supported_line_types=("AUTO",),
                ),
            )
        ).deactivate(db, workline_id=line.id, version=line.version)
        assert not stopped.is_active


@pytest.mark.asyncio
async def test_postgresql_create_and_close_serialize_on_workline(integration_session_factory) -> None:
    creation_holds_workline = asyncio.Event()
    release_creation = asyncio.Event()

    class PausingWorkLineRepository(WorkLineRepository):
        async def get_binding_for_command_creation(self, db, *, workline_id, device_code):
            binding = await super().get_binding_for_command_creation(
                db, workline_id=workline_id, device_code=device_code
            )
            creation_holds_workline.set()
            await release_creation.wait()
            return binding

    async with integration_session_factory.begin() as db:
        line, _, binding = await _seed_topology(db)

    request = DeviceCommandRequest(
        device_code=binding.device_code,
        workline_id=line.id,
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
        workline_repository=PausingWorkLineRepository(),
        clock=lambda: datetime(2026, 8, 13),
    )

    create_task = asyncio.create_task(command_service.create_command(request))
    await asyncio.wait_for(creation_holds_workline.wait(), timeout=2)

    async def close_workline():
        async with integration_session_factory.begin() as db:
            return await WorkLineConfigurationService(
                definitions=(
                    PluginDefinition(
                        plugin_key="device_command_test",
                        plugin_version="1.0.0",
                        display_name="Test",
                        supported_line_types=("AUTO",),
                    ),
                )
            ).deactivate(db, workline_id=line.id, version=line.version)

    close_task = asyncio.create_task(close_workline())
    await asyncio.sleep(0.05)
    assert not close_task.done()
    release_creation.set()
    await asyncio.wait_for(create_task, timeout=2)
    with pytest.raises(BusinessException):
        await asyncio.wait_for(close_task, timeout=2)

    async with integration_session_factory() as db:
        persisted_workline = await db.get(WorkLine, line.id)
        assert persisted_workline.is_active


@pytest.mark.asyncio
async def test_postgresql_command_frozen_contract_read_does_not_wait_for_workline_lock(integration_session_factory):
    async with integration_session_factory.begin() as db:
        line, _, binding = await _seed_topology(db)
        command = _command(binding, f"CMD-{uuid4().hex}", CommandStatus.PENDING)
        db.add(command)
    lock_acquired = asyncio.Event()
    release_lock = asyncio.Event()

    async def hold_line():
        async with integration_session_factory.begin() as db:
            await WorkLineRepository().get_for_update(db, line.id)
            lock_acquired.set()
            await release_lock.wait()

    holder = asyncio.create_task(hold_line())
    await asyncio.wait_for(lock_acquired.wait(), timeout=2)
    try:
        async with integration_session_factory() as db:
            frozen = await asyncio.wait_for(
                device_command_repository.get_by_command_code(db, command.command_code), timeout=1
            )
            assert frozen.endpoint_base_url == binding.endpoint_base_url
            assert frozen.command_timeout_ms == binding.command_timeout_ms
    finally:
        release_lock.set()
        await holder


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        CommandStatus.PENDING,
        CommandStatus.DISPATCHING,
        CommandStatus.ACKNOWLEDGED,
        CommandStatus.RECONCILING,
        CommandStatus.SUCCEEDED,
        CommandStatus.FAILED,
        CommandStatus.TIMED_OUT,
    ],
)
async def test_postgresql_business_command_status_does_not_create_a_global_device_slot(
    integration_session_factory,
    status: CommandStatus,
) -> None:
    async with integration_session_factory.begin() as db:
        _, _, binding = await _seed_topology(db)
        identity = uuid4().hex
        db.add(_command(binding, f"CMD-{identity}-1", status))
        await db.flush()
        db.add(_command(binding, f"CMD-{identity}-2", CommandStatus.PENDING))
        await db.flush()


@pytest.mark.asyncio
async def test_postgresql_allows_only_one_dispatching_command_per_device(
    integration_session_factory,
) -> None:
    with pytest.raises(IntegrityError):
        async with integration_session_factory.begin() as db:
            _, _, binding = await _seed_topology(db)
            identity = uuid4().hex
            db.add_all(
                [
                    _command(binding, f"CMD-{identity}-1", CommandStatus.DISPATCHING),
                    _command(binding, f"CMD-{identity}-2", CommandStatus.DISPATCHING),
                ]
            )
            await db.flush()


@pytest.mark.asyncio
async def test_postgresql_concurrent_claims_dispatch_only_one_command_per_device(
    integration_session_factory,
) -> None:
    now = datetime(2026, 8, 13)
    async with integration_session_factory.begin() as db:
        _, _, binding = await _seed_topology(db)
        identity = uuid4().hex
        db.add_all(
            [
                _command(binding, f"CMD-{identity}-1", CommandStatus.PENDING),
                _command(binding, f"CMD-{identity}-2", CommandStatus.PENDING),
            ]
        )

    async def claim(token: str) -> DeviceCommand | None:
        async with integration_session_factory.begin() as db:
            return await device_command_repository.claim_next_pending(
                db,
                token=token,
                now=now,
                claim_expires_at=datetime(2026, 8, 13, 0, 0, 30),
            )

    results = await asyncio.gather(claim(f"TOKEN-{identity}-1"), claim(f"TOKEN-{identity}-2"))

    assert sum(command is not None for command in results) == 1
    assert sum(command is None for command in results) == 1
