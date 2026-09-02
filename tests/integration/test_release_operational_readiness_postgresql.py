"""四账本发布静默门禁的单 statement PostgreSQL owner。"""

from __future__ import annotations

import asyncio
import importlib
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.dialects import postgresql
from wes_plugin_sdk import CreateDeviceCommand, DevicePosition, EvidenceReadyFact, FactReference, handler

from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.device.models.device import Device
from src.app.device.services import DeviceCommandService
from src.app.execution.models.inbound_evidence import InboundEvidence
from src.app.execution.models.material_execution import MaterialExecution
from src.app.execution.models.wms_confirmation import WmsConfirmation
from src.app.execution.plugin_binding import PluginRuntimeBinding, StaticPluginBinding
from src.app.execution.repositories import inbound_evidence_repository
from src.app.execution.services import (
    DecisionApplier,
    FactProcessor,
    MaterialExecutionService,
    WmsConfirmationService,
)
from src.app.transport.models import TransportEvidence, TransportTask
from src.app.workline.models.line_run_epoch import LineRunEpoch, LineRunEpochDeviceBinding
from src.app.workline.models.workline import WorkLine
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

Ledger = Literal["device", "transport", "inbound", "wms"]


@dataclass(frozen=True)
class Scenario:
    id: str
    ledger: Ledger
    status: str
    expected_key: str | None
    expected_state: str | None
    shape: str = "normal"


SCENARIOS = (
    Scenario("device-pending", "device", "PENDING", "device_command_wait_drain", "WAIT_DRAIN"),
    Scenario("device-dispatching", "device", "DISPATCHING", "device_command_wait_drain", "WAIT_DRAIN"),
    Scenario("device-acknowledged", "device", "ACKNOWLEDGED", "device_command_wait_drain", "WAIT_DRAIN"),
    Scenario("device-reconciling", "device", "RECONCILING", "device_command_block", "BLOCK"),
    Scenario("device-succeeded", "device", "SUCCEEDED", None, "READY"),
    Scenario("device-failed", "device", "FAILED", None, "READY"),
    Scenario("device-timed-out", "device", "TIMED_OUT", None, "READY"),
    Scenario("device-unknown", "device", "OUT_OF_CONTRACT", "device_command_unknown", None, "unknown"),
    Scenario("device-invalid", "device", "PENDING", "device_command_invalid", None, "completed_unclosed"),
    Scenario("transport-pending", "transport", "PENDING", "transport_task_wait_drain", "WAIT_DRAIN"),
    Scenario("transport-accepted", "transport", "ACCEPTED", "transport_task_wait_drain", "WAIT_DRAIN"),
    Scenario("transport-reconciling", "transport", "RECONCILING", "transport_task_block", "BLOCK"),
    Scenario("transport-terminal", "transport", "SUCCEEDED", None, "READY"),
    Scenario("transport-outcome-wait", "transport", "SUCCEEDED", "transport_task_wait_drain", "WAIT_DRAIN", "gap"),
    Scenario("transport-published-ahead", "transport", "SUCCEEDED", "transport_task_invalid", None, "published_ahead"),
    Scenario("transport-gap-without-outcome", "transport", "SUCCEEDED", "transport_task_invalid", None, "gap_null"),
    Scenario("transport-unknown", "transport", "OUT_OF_CONTRACT", "transport_task_unknown", None, "unknown"),
    Scenario("inbound-pending", "inbound", "PENDING", "inbound_evidence_wait_drain", "WAIT_DRAIN"),
    Scenario("inbound-bound-applied", "inbound", "APPLIED", "inbound_evidence_wait_drain", "WAIT_DRAIN", "bound"),
    Scenario("inbound-reconciling", "inbound", "RECONCILING", "inbound_evidence_block", "BLOCK"),
    Scenario("inbound-ignored", "inbound", "IGNORED", None, "READY"),
    Scenario("inbound-published", "inbound", "APPLIED", None, "READY", "published"),
    Scenario("inbound-unbound-device-result", "inbound", "APPLIED", None, "READY", "unbound"),
    Scenario("inbound-unknown", "inbound", "OUT_OF_CONTRACT", "inbound_evidence_unknown", None, "unknown"),
    Scenario("inbound-invalid", "inbound", "APPLIED", "inbound_evidence_invalid", None, "published_without_digest"),
    Scenario("wms-pending", "wms", "PENDING", "wms_confirmation_wait_drain", "WAIT_DRAIN"),
    Scenario("wms-dispatching", "wms", "DISPATCHING", "wms_confirmation_wait_drain", "WAIT_DRAIN"),
    Scenario("wms-reconciling", "wms", "RECONCILING", "wms_confirmation_block", "BLOCK"),
    Scenario("wms-completed", "wms", "COMPLETED", None, "READY"),
    Scenario("wms-unknown", "wms", "OUT_OF_CONTRACT", "wms_confirmation_unknown", None, "unknown"),
    Scenario("wms-invalid", "wms", "COMPLETED", "wms_confirmation_invalid", None, "missing_completion"),
)


@dataclass(frozen=True)
class RepresentativeDistribution:
    terminal: int
    wait_drain: int
    block: int
    invalid: int


def _contract_modules():
    repository_module = importlib.import_module(
        "src.app.runtime.orchestration.repositories.release_operational_readiness_repository"
    )
    service_module = importlib.import_module(
        "src.app.runtime.orchestration.services.query.release_operational_readiness_service"
    )
    return repository_module, service_module


def build_representative_distribution(rows_per_table: int = 10_000) -> RepresentativeDistribution:
    terminal = int(rows_per_table * 0.9)
    remainder = rows_per_table - terminal
    wait_drain = remainder // 3
    block = remainder // 3
    invalid = remainder - wait_drain - block
    return RepresentativeDistribution(terminal, wait_drain, block, invalid)


async def _seed_bound_execution(db: AsyncSession) -> tuple[LineRunEpoch, LineRunEpochDeviceBinding, MaterialExecution]:
    identity = uuid4().hex
    now = timezone.now_for_db()
    line = WorkLine(line_code=f"READINESS-{identity[:12]}", line_name="Release readiness", line_type="AUTO")
    db.add(line)
    await db.flush()
    device = Device(
        device_code=f"READINESS-DEVICE-{identity[:12]}",
        device_name="Release readiness device",
        device_role="TEST",
        work_line_id=line.id,
    )
    db.add(device)
    await db.flush()
    epoch = LineRunEpoch(
        epoch_code=f"READINESS-EPOCH-{identity[:12]}",
        workline_id=line.id,
        plugin_key="readiness_test",
        plugin_version="1.0.0",
        flow_mode="TEST",
        topology_digest="a" * 64,
        configuration_digest="b" * 64,
        configuration_snapshot_json={},
        started_at=now,
    )
    admission = InboundEvidence(
        kind="WMS_EVENT",
        source_identity=f"readiness-admission:{identity}",
        payload_digest="c" * 64,
        normalized_payload={},
        received_at=now,
        operation="readiness.admission@v1",
        operation_id=identity,
        apply_status="APPLIED",
        processed_at=now,
        published_at=now,
        decision_digest="d" * 64,
    )
    db.add_all((epoch, admission))
    await db.flush()
    binding = LineRunEpochDeviceBinding(
        line_run_epoch_id=epoch.id,
        device_id=device.id,
        device_code=device.device_code,
        device_role=device.device_role,
        endpoint_base_url="http://readiness-ecs:8080",
        contract_key="test.readiness",
        contract_version="1.0",
        status_max_age_ms=1_000,
        command_timeout_ms=30_000,
    )
    execution = MaterialExecution(
        execution_code=f"READINESS-EXEC-{identity[:12]}",
        material_trace_id=f"READINESS-MATERIAL-{identity[:12]}",
        workline_id=line.id,
        line_run_epoch_id=epoch.id,
        admission_received_at=now,
        admission_evidence_id=admission.id,
        status="RUNNING",
        last_transition_reason="ADMITTED",
        last_transition_evidence_id=admission.id,
        status_changed_at=now,
    )
    db.add_all((binding, execution))
    await db.flush()
    return epoch, binding, execution


async def _drop_status_check_and_update(db: AsyncSession, scenario: Scenario, row_id: int) -> None:
    table_contract = {
        "device": ("wes_biz.device_commands", "ck_device_commands_device_command_status_valid", "status"),
        "transport": ("wes_runtime.transport_tasks", "ck_transport_tasks_transport_task_status_valid", "status"),
        "inbound": (
            "wes_biz.inbound_evidences",
            "ck_inbound_evidences_inbound_evidence_apply_status_valid",
            "apply_status",
        ),
        "wms": ("wes_biz.wms_confirmations", "ck_wms_confirmations_wms_confirmation_status_valid", "status"),
    }
    table_name, constraint_name, column_name = table_contract[scenario.ledger]
    await db.execute(text(f"ALTER TABLE {table_name} DROP CONSTRAINT {constraint_name}"))
    await db.execute(
        text(f"UPDATE {table_name} SET {column_name} = :status WHERE id = :row_id"),
        {"status": scenario.status, "row_id": row_id},
    )


async def _seed_scenario(db: AsyncSession, scenario: Scenario) -> None:
    epoch, binding, execution = await _seed_bound_execution(db)
    now = timezone.now_for_db()
    identity = uuid4().hex
    if scenario.ledger == "device":
        row = DeviceCommand(
            command_code=f"READINESS-CMD-{identity[:12]}",
            device_code=binding.device_code,
            line_run_epoch_id=epoch.id,
            device_binding_id=binding.id,
            execution_ref_type="TEST_EXECUTION",
            execution_ref_id=identity,
            material_execution_id=execution.id,
            contract_key=binding.contract_key,
            contract_version=binding.contract_version,
            task_type="TEST_ACTION",
            params={},
            payload_digest="e" * 64,
            deadline_at=now + timedelta(minutes=1),
            status="PENDING" if scenario.shape == "unknown" else scenario.status,
            completed_at=(
                now
                if scenario.status in {"SUCCEEDED", "FAILED", "TIMED_OUT"} or scenario.shape == "completed_unclosed"
                else None
            ),
            reconciliation_reason="DELIVERY_UNKNOWN" if scenario.status == "RECONCILING" else None,
        )
    elif scenario.ledger == "transport":
        outcome_version = 1 if scenario.shape in {"gap", "gap_null"} else 0
        published_version = 1 if scenario.shape == "published_ahead" else 0
        row = TransportTask(
            transport_task_id=f"READINESS-TRANSPORT-{identity[:12]}",
            client_request_id=f"READINESS-REQUEST-{identity[:12]}",
            request_digest="3" * 64,
            kind="MOVE",
            caller_json={},
            request_json={},
            submit_operation_id=str(uuid4()),
            submit_timestamp_ms=1,
            submit_request_body="{}",
            submit_request_body_digest="4" * 64,
            status="SUCCEEDED" if scenario.shape == "unknown" else scenario.status,
            outcome_version=outcome_version,
            published_outcome_version=published_version,
            outcome_json={"result": "SUCCEEDED"} if scenario.shape in {"gap", "published_ahead"} else None,
            created_at=now,
            updated_at=now,
        )
    elif scenario.ledger == "inbound":
        kind = "DEVICE_RESULT" if scenario.shape in {"bound", "unbound"} else "WMS_EVENT"
        row = InboundEvidence(
            kind=kind,
            source_identity=f"readiness-inbound:{identity}",
            payload_digest="5" * 64,
            normalized_payload={},
            received_at=now,
            line_run_epoch_id=epoch.id,
            material_execution_id=execution.id if scenario.shape == "bound" else None,
            device_code=binding.device_code if kind == "DEVICE_RESULT" else None,
            command_code=f"READINESS-CMD-{identity[:12]}" if kind == "DEVICE_RESULT" else None,
            operation="readiness.event@v1" if kind == "WMS_EVENT" else None,
            operation_id=identity if kind == "WMS_EVENT" else None,
            apply_status="IGNORED" if scenario.shape == "unknown" else scenario.status,
            processed_at=now if scenario.status != "PENDING" else None,
            published_at=now if scenario.shape == "published" else None,
            decision_digest="6" * 64 if scenario.shape == "published" else None,
        )
    else:
        row = WmsConfirmation(
            operation="readiness.confirm@v1",
            operation_id=identity,
            material_execution_id=execution.id,
            request_digest="7" * 64,
            request_payload={},
            deadline_at=now + timedelta(minutes=1),
            status="COMPLETED" if scenario.shape == "unknown" else scenario.status,
            completed_at=now if scenario.status == "COMPLETED" and scenario.shape != "missing_completion" else None,
        )
    db.add(row)
    await db.flush()
    assert row.id is not None
    if scenario.shape == "unknown":
        await _drop_status_check_and_update(db, scenario, row.id)
    elif scenario.shape == "published_without_digest":
        await db.execute(
            text(
                "ALTER TABLE wes_biz.inbound_evidences "
                "DROP CONSTRAINT ck_inbound_evidences_inbound_evidence_published_decisio_5f24"
            )
        )
        await db.execute(
            text("UPDATE wes_biz.inbound_evidences SET published_at = :now WHERE id = :row_id"),
            {"now": now, "row_id": row.id},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda scenario: scenario.id)
async def test_postgresql_rows_drive_each_category_count_and_final_state(
    integration_db_session: AsyncSession,
    scenario: Scenario,
) -> None:
    await _seed_scenario(integration_db_session, scenario)
    repository_module, service_module = _contract_modules()
    repository = repository_module.ReleaseOperationalReadinessRepository()
    counts = await repository.load_counts(integration_db_session)
    observed = vars(counts)
    if scenario.expected_key is None:
        assert sum(observed.values()) == 0
    else:
        assert observed[scenario.expected_key] == 1
        assert sum(observed.values()) == 1

    service = service_module.ReleaseOperationalReadinessService(repository=repository)
    if scenario.expected_state is None:
        with pytest.raises(service_module.ReleaseOperationalReadinessQueryError):
            await service.check(integration_db_session)
    else:
        assert (await service.check(integration_db_session)).state == scenario.expected_state


@pytest.mark.asyncio
async def test_repository_executes_one_read_only_snapshot_with_ten_second_database_timeout(
    integration_db_session: AsyncSession,
) -> None:
    repository_module, _service_module = _contract_modules()
    statements: list[str] = []

    def record_statement(_conn, _cursor, statement: str, _parameters, _context, _executemany) -> None:
        statements.append(" ".join(statement.split()))

    event.listen(integration_db_session.bind.sync_engine, "before_cursor_execute", record_statement)
    try:
        counts = await repository_module.ReleaseOperationalReadinessRepository().load_counts(integration_db_session)
    finally:
        event.remove(integration_db_session.bind.sync_engine, "before_cursor_execute", record_statement)

    selects = [statement for statement in statements if statement.upper().startswith("SELECT")]
    timeouts = [statement for statement in statements if "statement_timeout" in statement.lower()]
    assert len(selects) == 1
    assert len(timeouts) == 1
    assert "10s" in timeouts[0] or "10000" in timeouts[0]
    assert not any(
        statement.upper().startswith(("INSERT", "UPDATE", "DELETE", "LOCK")) or "FOR UPDATE" in statement.upper()
        for statement in statements
    )
    assert all(isinstance(value, int) and value >= 0 for value in vars(counts).values())


@pytest.mark.asyncio
async def test_committed_device_transport_and_wms_handoffs_have_no_cleared_snapshot(
    integration_session_factory,
) -> None:
    identity = uuid4().hex
    now = timezone.now_for_db()
    async with integration_session_factory.begin() as db:
        epoch, binding, execution = await _seed_bound_execution(db)
        command = DeviceCommand(
            command_code=f"READINESS-CMD-{identity[:12]}",
            device_code=binding.device_code,
            line_run_epoch_id=epoch.id,
            device_binding_id=binding.id,
            execution_ref_type="TEST_EXECUTION",
            execution_ref_id=identity,
            material_execution_id=execution.id,
            contract_key=binding.contract_key,
            contract_version=binding.contract_version,
            task_type="TEST_ACTION",
            params={},
            payload_digest="8" * 64,
            deadline_at=now + timedelta(minutes=1),
            status=CommandStatus.ACKNOWLEDGED,
            ack_received_at=now,
        )
        db.add(command)
        await db.flush()
        command_id = command.id
        execution_id = execution.id
        admission_evidence_id = execution.admission_evidence_id
        binding_id = binding.id
        device_id = binding.device_id
        epoch_id = epoch.id
        line_id = execution.workline_id

    repository_module, _service_module = _contract_modules()
    repository = repository_module.ReleaseOperationalReadinessRepository()
    async with integration_session_factory() as observer:
        assert (await repository.load_counts(observer)).device_command_wait_drain == 1

    async with integration_session_factory.begin() as handoff:
        persisted_command = await handoff.get(DeviceCommand, command_id, with_for_update=True)
        assert persisted_command is not None
        bound_result = InboundEvidence(
            kind="DEVICE_RESULT",
            source_identity=f"readiness-device-result:{identity}",
            payload_digest="9" * 64,
            normalized_payload={},
            received_at=now,
            line_run_epoch_id=epoch_id,
            material_execution_id=execution_id,
            device_code=persisted_command.device_code,
            command_code=persisted_command.command_code,
            contract_version="1.0",
            apply_status="APPLIED",
            processed_at=now,
        )
        handoff.add(bound_result)
        await handoff.flush()
        persisted_command.result_evidence_id = bound_result.id
        persisted_command.transition_to(CommandStatus.SUCCEEDED)
        device_handoff = await repository.load_counts(handoff)
        assert device_handoff.device_command_wait_drain == 0
        assert device_handoff.inbound_evidence_wait_drain == 1
        bound_result_id = bound_result.id

    async with integration_session_factory() as observer:
        committed = await repository.load_counts(observer)
        assert committed.device_command_wait_drain == 0
        assert committed.inbound_evidence_wait_drain == 1
    async with integration_session_factory.begin() as cleanup:
        await cleanup.execute(text("DELETE FROM wes_biz.device_commands WHERE id = :id"), {"id": command_id})
        await cleanup.execute(text("DELETE FROM wes_biz.inbound_evidences WHERE id = :id"), {"id": bound_result_id})

    transport_task_id = f"READINESS-TRANSPORT-{identity[:12]}"
    async with integration_session_factory.begin() as db:
        db.add_all(
            (
                TransportTask(
                    transport_task_id=transport_task_id,
                    client_request_id=f"READINESS-REQUEST-{identity[:12]}",
                    request_digest="b" * 64,
                    kind="MOVE",
                    caller_json={},
                    request_json={},
                    submit_operation_id=str(uuid4()),
                    submit_timestamp_ms=1,
                    submit_request_body="{}",
                    submit_request_body_digest="c" * 64,
                    status="SUCCEEDED",
                    outcome_version=1,
                    published_outcome_version=0,
                    outcome_json={"result": "SUCCEEDED"},
                    created_at=now,
                    updated_at=now,
                ),
                TransportEvidence(
                    operation_id=str(uuid4()),
                    transport_task_id=transport_task_id,
                    operation="transport.task.resulted@v1",
                    outcome_revision=1,
                    event_timestamp_ms=1,
                    message_digest="d" * 64,
                    payload_json={},
                    ack_timestamp_ms=1,
                    ack_data_json={},
                    status="APPLIED",
                    received_at=now,
                    processed_at=now,
                ),
            )
        )
    async with integration_session_factory() as observer:
        assert (await repository.load_counts(observer)).transport_task_wait_drain == 1
        assert (
            await observer.scalar(
                select(TransportEvidence.id).where(TransportEvidence.transport_task_id == transport_task_id)
            )
            is not None
        )
    async with integration_session_factory.begin() as fenced_publish:
        transport = await fenced_publish.scalar(
            select(TransportTask).where(TransportTask.transport_task_id == transport_task_id).with_for_update()
        )
        visible_evidence = await fenced_publish.scalar(
            select(TransportEvidence.id).where(
                TransportEvidence.transport_task_id == transport_task_id,
                TransportEvidence.outcome_revision == 1,
            )
        )
        assert transport is not None and visible_evidence is not None
        assert (await repository.load_counts(fenced_publish)).transport_task_wait_drain == 1
        transport.published_outcome_version = 1
        await fenced_publish.flush()
        assert (await repository.load_counts(fenced_publish)).transport_task_wait_drain == 0
    async with integration_session_factory() as observer:
        assert (await repository.load_counts(observer)).transport_task_wait_drain == 0
    async with integration_session_factory.begin() as cleanup:
        await cleanup.execute(
            text("DELETE FROM wes_runtime.transport_evidence WHERE transport_task_id = :id"),
            {"id": transport_task_id},
        )
        await cleanup.execute(
            text("DELETE FROM wes_runtime.transport_tasks WHERE transport_task_id = :id"),
            {"id": transport_task_id},
        )

    async with integration_session_factory.begin() as db:
        confirmation = WmsConfirmation(
            operation="readiness.confirm@v1",
            operation_id=identity,
            material_execution_id=execution_id,
            request_digest="e" * 64,
            request_payload={},
            deadline_at=now + timedelta(minutes=1),
            status="PENDING",
        )
        db.add(confirmation)
        await db.flush()
        confirmation_id = confirmation.id
    async with integration_session_factory() as observer:
        assert (await repository.load_counts(observer)).wms_confirmation_wait_drain == 1
    async with integration_session_factory.begin() as handoff:
        persisted_confirmation = await handoff.get(WmsConfirmation, confirmation_id, with_for_update=True)
        assert persisted_confirmation is not None
        wms_result = InboundEvidence(
            kind="WMS_RESULT",
            source_identity=f"readiness-wms-result:{identity}",
            payload_digest="f" * 64,
            normalized_payload={},
            received_at=now,
            line_run_epoch_id=epoch_id,
            material_execution_id=execution_id,
            operation=persisted_confirmation.operation,
            operation_id=persisted_confirmation.operation_id,
            contract_version="1.0",
            apply_status="APPLIED",
            processed_at=now,
        )
        handoff.add(wms_result)
        await handoff.flush()
        _ = await WmsConfirmationService().complete(
            handoff,
            persisted_confirmation,
            response_evidence_id=wms_result.id,
            response_result="SUCCEEDED",
            completed_at=now,
        )
        wms_handoff = await repository.load_counts(handoff)
        assert wms_handoff.wms_confirmation_wait_drain == 0
        assert wms_handoff.inbound_evidence_wait_drain == 1
        wms_result_id = wms_result.id
    async with integration_session_factory() as observer:
        committed = await repository.load_counts(observer)
        assert committed.wms_confirmation_wait_drain == 0
        assert committed.inbound_evidence_wait_drain == 1
    async with integration_session_factory.begin() as cleanup:
        await cleanup.execute(text("DELETE FROM wes_biz.wms_confirmations WHERE id = :id"), {"id": confirmation_id})
        await cleanup.execute(text("DELETE FROM wes_biz.inbound_evidences WHERE id = :id"), {"id": wms_result_id})

    async with integration_session_factory.begin() as db:
        diagnostic_command = DeviceCommand(
            command_code=f"READINESS-DIAGNOSTIC-{identity[:12]}",
            device_code=f"DIAGNOSTIC-{identity[:12]}",
            execution_ref_type="MANUAL_DEBUG",
            execution_ref_id=f"READINESS-DIAGNOSTIC-{identity}",
            contract_key="test.readiness",
            contract_version="1.0",
            task_type="TEST_ACTION",
            params={},
            payload_digest="2" * 64,
            deadline_at=now + timedelta(minutes=1),
            endpoint_base_url="http://readiness-ecs:8080",
            command_timeout_ms=30_000,
            execution_reason="release readiness diagnostic exception",
            created_by=42,
            status=CommandStatus.ACKNOWLEDGED,
            ack_received_at=now,
        )
        db.add(diagnostic_command)
        await db.flush()
        diagnostic_command_id = diagnostic_command.id
    async with integration_session_factory.begin() as handoff:
        persisted_command = await handoff.get(DeviceCommand, diagnostic_command_id, with_for_update=True)
        assert persisted_command is not None
        diagnostic_result = InboundEvidence(
            kind="DEVICE_RESULT",
            source_identity=f"readiness-diagnostic-result:{identity}",
            payload_digest="3" * 64,
            normalized_payload={},
            received_at=now,
            device_code=persisted_command.device_code,
            command_code=persisted_command.command_code,
            apply_status="APPLIED",
            processed_at=now,
        )
        handoff.add(diagnostic_result)
        await handoff.flush()
        persisted_command.result_evidence_id = diagnostic_result.id
        persisted_command.transition_to(CommandStatus.SUCCEEDED)
        counts = await repository.load_counts(handoff)
        assert counts.device_command_wait_drain == 0
        assert counts.inbound_evidence_wait_drain == 0
        diagnostic_result_id = diagnostic_result.id
    async with integration_session_factory() as observer:
        counts = await repository.load_counts(observer)
        assert counts.device_command_wait_drain == 0
        assert counts.inbound_evidence_wait_drain == 0
    async with integration_session_factory.begin() as cleanup:
        await cleanup.execute(text("DELETE FROM wes_biz.device_commands WHERE id = :id"), {"id": diagnostic_command_id})
        await cleanup.execute(
            text("DELETE FROM wes_biz.inbound_evidences WHERE id = :id"), {"id": diagnostic_result_id}
        )
        await cleanup.execute(text("DELETE FROM wes_biz.material_executions WHERE id = :id"), {"id": execution_id})
        await cleanup.execute(
            text("DELETE FROM wes_biz.line_run_epoch_device_bindings WHERE id = :id"), {"id": binding_id}
        )
        await cleanup.execute(text("DELETE FROM wes_biz.devices WHERE id = :id"), {"id": device_id})
        await cleanup.execute(text("DELETE FROM wes_biz.line_run_epochs WHERE id = :id"), {"id": epoch_id})
        await cleanup.execute(
            text("DELETE FROM wes_biz.inbound_evidences WHERE id = :id"), {"id": admission_evidence_id}
        )
        await cleanup.execute(text("DELETE FROM wes_biz.work_lines WHERE id = :id"), {"id": line_id})


@pytest.mark.asyncio
async def test_fact_processor_creates_device_command_with_published_evidence_atomically(
    integration_session_factory,
) -> None:
    identity = uuid4().hex
    now = timezone.now_for_db()
    async with integration_session_factory.begin() as db:
        epoch, binding, execution = await _seed_bound_execution(db)
        evidence = InboundEvidence(
            kind="DEVICE_EVENT",
            source_identity=f"readiness-fact:{identity}",
            payload_digest="4" * 64,
            normalized_payload={},
            received_at=now,
            line_run_epoch_id=epoch.id,
            material_execution_id=execution.id,
            device_code=binding.device_code,
            contract_version="1.0",
            apply_status="APPLIED",
            processed_at=now,
        )
        db.add(evidence)
        await db.flush()
        evidence_id = evidence.id
        execution_id = execution.id
        admission_evidence_id = execution.admission_evidence_id
        binding_id = binding.id
        device_id = binding.device_id
        epoch_id = epoch.id
        line_id = execution.workline_id

    repository_module, _service_module = _contract_modules()
    readiness_repository = repository_module.ReleaseOperationalReadinessRepository()
    async with integration_session_factory() as observer:
        counts = await readiness_repository.load_counts(observer)
        assert counts.inbound_evidence_wait_drain == 1
        assert counts.device_command_wait_drain == 0

    @handler(fact_type=EvidenceReadyFact, name="release_readiness_fact", supported_versions=("1.0",))
    def create_command(fact: EvidenceReadyFact) -> tuple[CreateDeviceCommand, ...]:
        return (
            CreateDeviceCommand(
                fact.material_execution_id,
                fact.fact_id,
                "TEST",
                "READINESS_CHECK",
                execution.material_trace_id,
                DevicePosition("READINESS-SOURCE", "TEST", execution.material_trace_id),
                DevicePosition("READINESS-TARGET", "TEST", execution.material_trace_id),
            ),
        )

    class IdentityFactFactory:
        async def build(self, _db: object, fact: FactReference) -> FactReference:
            return fact

    applied_inside_transaction = asyncio.Event()
    release_commit = asyncio.Event()

    class ObservingEvidenceRepository:
        async def claim_decision_batch(self, db, **kwargs):  # type: ignore[no-untyped-def]
            return await inbound_evidence_repository.claim_decision_batch(db, **kwargs)

        async def get_decision_claim_for_update(self, db, **kwargs):  # type: ignore[no-untyped-def]
            return await inbound_evidence_repository.get_decision_claim_for_update(db, **kwargs)

        async def get_by_id_for_update(self, db, evidence_id):  # type: ignore[no-untyped-def]
            return await inbound_evidence_repository.get_by_id_for_update(db, evidence_id)

        async def flush(self, db):  # type: ignore[no-untyped-def]
            await inbound_evidence_repository.flush(db)
            persisted = await db.get(InboundEvidence, evidence_id)
            if persisted is None or persisted.published_at is None or applied_inside_transaction.is_set():
                return
            created = await db.scalar(
                select(DeviceCommand.id).where(DeviceCommand.material_execution_id == execution_id)
            )
            assert created is not None
            counts = await readiness_repository.load_counts(db)
            assert counts.inbound_evidence_wait_drain == 0
            assert counts.device_command_wait_drain == 1
            applied_inside_transaction.set()
            await release_commit.wait()

    applier = DecisionApplier(
        device_command_service=DeviceCommandService(session_factory=integration_session_factory, clock=lambda: now),
        wms_confirmation_service=object(),
        transport_service=object(),
        material_execution_service=MaterialExecutionService(),
        clock=lambda: now,
    )
    processor = FactProcessor(
        session_factory=integration_session_factory,
        plugin_binding=StaticPluginBinding(
            (
                PluginRuntimeBinding(
                    plugin_key="readiness_test",
                    plugin_version="1.0.0",
                    handlers=(create_command,),
                    fact_factory=IdentityFactFactory(),
                ),
            )
        ),
        decision_applier=applier,
        evidence_repository=ObservingEvidenceRepository(),
        clock=lambda: now,
        token_factory=lambda: f"readiness-claim-{identity}",
    )
    processing = asyncio.create_task(processor.process_batch(limit=1))
    await asyncio.wait_for(applied_inside_transaction.wait(), timeout=5)
    async with integration_session_factory() as second_session_before_commit:
        counts = await readiness_repository.load_counts(second_session_before_commit)
        assert counts.inbound_evidence_wait_drain == 1
        assert counts.device_command_wait_drain == 0
    release_commit.set()
    assert await asyncio.wait_for(processing, timeout=5) == 1

    async with integration_session_factory() as second_session_after_commit:
        persisted = await second_session_after_commit.get(InboundEvidence, evidence_id)
        created = await second_session_after_commit.scalar(
            select(DeviceCommand.id).where(DeviceCommand.material_execution_id == execution_id)
        )
        assert persisted is not None and persisted.published_at is not None
        assert created is not None
        counts = await readiness_repository.load_counts(second_session_after_commit)
        assert counts.inbound_evidence_wait_drain == 0
        assert counts.device_command_wait_drain == 1

    async with integration_session_factory.begin() as cleanup:
        await cleanup.execute(
            text("DELETE FROM wes_biz.device_commands WHERE material_execution_id = :execution_id"),
            {"execution_id": execution_id},
        )
        await cleanup.execute(
            text(
                "UPDATE wes_biz.material_executions "
                "SET last_transition_evidence_id = :admission_id WHERE id = :execution_id"
            ),
            {"admission_id": admission_evidence_id, "execution_id": execution_id},
        )
        await cleanup.execute(
            text("UPDATE wes_biz.inbound_evidences SET material_execution_id = NULL WHERE id = :id"),
            {"id": evidence_id},
        )
        await cleanup.execute(text("DELETE FROM wes_biz.inbound_evidences WHERE id = :id"), {"id": evidence_id})
        await cleanup.execute(text("DELETE FROM wes_biz.material_executions WHERE id = :id"), {"id": execution_id})
        await cleanup.execute(
            text("DELETE FROM wes_biz.line_run_epoch_device_bindings WHERE id = :id"), {"id": binding_id}
        )
        await cleanup.execute(text("DELETE FROM wes_biz.devices WHERE id = :id"), {"id": device_id})
        await cleanup.execute(text("DELETE FROM wes_biz.line_run_epochs WHERE id = :id"), {"id": epoch_id})
        await cleanup.execute(
            text("DELETE FROM wes_biz.inbound_evidences WHERE id = :id"), {"id": admission_evidence_id}
        )
        await cleanup.execute(text("DELETE FROM wes_biz.work_lines WHERE id = :id"), {"id": line_id})


@pytest.mark.asyncio
async def test_aggregate_statement_is_count_only_supplemental_evidence(integration_db_session: AsyncSession) -> None:
    repository_module, _service_module = _contract_modules()
    statement = repository_module.ReleaseOperationalReadinessRepository().build_statement()
    sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).lower()
    assert ";" not in sql
    assert " for update" not in sql
    for payload_column in ("request_payload", "normalized_payload", "caller_json", "request_json", "params"):
        assert payload_column not in sql


def test_representative_performance_builder_owns_forty_thousand_row_shape() -> None:
    distribution = build_representative_distribution()
    assert sum(vars(distribution).values()) == 10_000
    assert distribution.terminal >= 9_000
    assert all(value > 0 for value in (distribution.wait_drain, distribution.block, distribution.invalid))
    assert sum(vars(distribution).values()) * 4 == 40_000
