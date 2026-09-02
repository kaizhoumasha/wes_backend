from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from wes_plugin_sdk import (
    CompleteExecution,
    CreateDeviceCommand,
    CreateTransportTask,
    DevicePosition,
    EvidenceReadyFact,
    PauseForReconciliation,
    TransportRackPosition,
    TransportRcsTemplateId,
    TransportTaskType,
    Wait,
)

from src.app.execution.models import InboundEvidence, InboundEvidenceApplyStatus, InboundEvidenceKind
from src.app.execution.models.material_execution import MaterialExecution, MaterialExecutionStatus
from src.app.execution.services.decision_applier import DecisionApplier, decision_digest
from src.app.workline.models.line_run_epoch import LineRunEpochDeviceBinding

NOW = datetime(2026, 8, 17, 9, 0, 0)


def _evidence() -> InboundEvidence:
    return InboundEvidence(
        id=31,
        kind=InboundEvidenceKind.DEVICE_EVENT,
        source_identity="source-31",
        payload_digest="a" * 64,
        normalized_payload={"data": {}},
        received_at=NOW,
        line_run_epoch_id=11,
        material_execution_id=21,
        contract_version="1.0",
        apply_status=InboundEvidenceApplyStatus.APPLIED,
    )


def _execution() -> MaterialExecution:
    return MaterialExecution(
        id=21,
        execution_code="EXEC-1",
        material_trace_id="TRACE-1",
        workline_id=7,
        line_run_epoch_id=11,
        status=MaterialExecutionStatus.CREATED,
        last_transition_reason="INITIAL_EVIDENCE",
        last_transition_evidence_id=31,
        status_changed_at=NOW,
    )


def _fact() -> EvidenceReadyFact:
    return EvidenceReadyFact(
        fact_id="evidence:31",
        evidence_id="31",
        fact_version="1.0",
        material_execution_id="EXEC-1",
    )


class _Epochs:
    async def get_binding_by_role_for_update(self, db: object, **kwargs: object) -> LineRunEpochDeviceBinding | None:
        del db
        assert kwargs == {"line_run_epoch_id": 11, "device_role": "TRANSFER_DEVICE"}
        return LineRunEpochDeviceBinding(
            id=41,
            line_run_epoch_id=11,
            device_id=5,
            device_code="TRANSFER-1",
            device_role="TRANSFER_DEVICE",
            endpoint_base_url="http://ecs-decision:8080",
            contract_key="rough_sorter.transfer",
            contract_version="1.0",
            status_max_age_ms=1000,
            command_timeout_ms=5000,
        )


class _DeviceCommands:
    def __init__(self) -> None:
        self.requests: list[object] = []

    async def create_command_in_session(self, db: object, request: object) -> object:
        del db
        self.requests.append(request)
        return SimpleNamespace(command_code="CMD-1")


class _WmsConfirmations:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def create_or_get(self, db: object, **kwargs: object) -> object:
        del db
        self.calls.append(kwargs)
        return SimpleNamespace(duplicate=False)


class _TransportBindings:
    def __init__(self) -> None:
        self.bindings: dict[tuple[int, str, str], object] = {}
        self.locked: list[tuple[int, str, str]] = []
        self.resource_locks: list[tuple[int, str]] = []

    async def lock_decision_identity(self, db: object, **kwargs: object) -> None:
        del db
        self.locked.append((int(kwargs["line_run_epoch_id"]), str(kwargs["correlation_id"]), str(kwargs["step"])))

    async def get_by_decision_identity_for_update(self, db: object, **kwargs: object) -> object | None:
        del db
        return self.bindings.get((int(kwargs["line_run_epoch_id"]), str(kwargs["correlation_id"]), str(kwargs["step"])))

    async def lock_resource_fence(self, db: object, *, line_run_epoch_id: int, resource_fence_id: str) -> None:
        del db
        self.resource_locks.append((line_run_epoch_id, resource_fence_id))

    async def add(self, db: object, binding: object) -> object:
        del db
        self.bindings[binding.decision_identity] = binding
        return binding


class _Transport:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def move_rack_in_session(self, db: object, **kwargs: object) -> object:
        del db
        self.calls.append(kwargs)
        return SimpleNamespace(transport_task_id="T-1")


class _Executions:
    def __init__(self) -> None:
        self.targets: list[MaterialExecutionStatus] = []

    async def transition(self, db: object, execution: MaterialExecution, **kwargs: object) -> MaterialExecution:
        del db
        target = kwargs["target"]
        assert isinstance(target, MaterialExecutionStatus)
        self.targets.append(target)
        execution.status = target
        return execution


def _applier(**overrides: object) -> DecisionApplier:
    dependencies = {
        "epoch_repository": _Epochs(),
        "device_command_service": _DeviceCommands(),
        "wms_confirmation_service": _WmsConfirmations(),
        "transport_binding_repository": _TransportBindings(),
        "transport_service": _Transport(),
        "material_execution_service": _Executions(),
        "clock": lambda: NOW,
        "uuid_factory": lambda: "019cd8ce-34b7-7000-8000-000000000001",
    }
    dependencies.update(overrides)
    return DecisionApplier(**dependencies)


@pytest.mark.asyncio
async def test_create_device_command_resolves_frozen_role_and_builds_typed_params() -> None:
    commands = _DeviceCommands()
    executions = _Executions()
    applier = _applier(device_command_service=commands, material_execution_service=executions)
    source = DevicePosition("IN", "HANDOFF", "TRACE-1")
    target = DevicePosition("OUT", "HANDOFF", "TRACE-1")
    decision = CreateDeviceCommand(
        "EXEC-1", "evidence:31", "TRANSFER_DEVICE", "MOVE_FORWARD", "TRACE-1", source, target
    )

    await applier.apply(object(), _evidence(), _execution(), _fact(), (decision,))

    request = commands.requests[0]
    assert request.device_code == "TRANSFER-1"
    assert request.material_execution_id == 21
    assert request.execution_ref_id == "evidence:31:execution:21:CREATE_DEVICE_COMMAND:0"
    assert request.deadline_at == NOW + timedelta(seconds=5)
    assert request.params == {
        "material_trace_id": "TRACE-1",
        "source": {
            "location_id": "IN",
            "location_type": "HANDOFF",
            "material_trace_id": "TRACE-1",
            "rack_id": None,
            "rack_slot_code": None,
            "bin_id": None,
            "bin_cell_id": None,
        },
        "target": {
            "location_id": "OUT",
            "location_type": "HANDOFF",
            "material_trace_id": "TRACE-1",
            "rack_id": None,
            "rack_slot_code": None,
            "bin_id": None,
            "bin_cell_id": None,
        },
    }
    assert executions.targets == [MaterialExecutionStatus.RUNNING]


@pytest.mark.asyncio
async def test_create_device_command_rejects_material_trace_mismatch_before_persistence() -> None:
    commands = _DeviceCommands()
    applier = _applier(device_command_service=commands)
    decision = CreateDeviceCommand(
        "EXEC-1",
        "evidence:31",
        "TRANSFER_DEVICE",
        "MOVE_FORWARD",
        "OTHER-TRACE",
        DevicePosition("IN", "HANDOFF", "OTHER-TRACE"),
        DevicePosition("OUT", "HANDOFF", "OTHER-TRACE"),
    )

    with pytest.raises(ValueError, match="material_trace_id"):
        await applier.apply(object(), _evidence(), _execution(), _fact(), (decision,))

    assert commands.requests == []


@pytest.mark.asyncio
async def test_device_command_execution_identity_is_bounded_when_execution_code_is_at_limit() -> None:
    execution = _execution().model_copy(update={"execution_code": "E" * 120})
    fact = EvidenceReadyFact(
        fact_id="evidence:31",
        evidence_id="31",
        fact_version="1.0",
        material_execution_id=execution.execution_code,
    )
    commands = _DeviceCommands()
    decision = CreateDeviceCommand(
        execution.execution_code,
        fact.fact_id,
        "TRANSFER_DEVICE",
        "MOVE_FORWARD",
        "TRACE-1",
        DevicePosition("IN", "HANDOFF", "TRACE-1"),
        DevicePosition("OUT", "HANDOFF", "TRACE-1"),
    )

    await _applier(device_command_service=commands).apply(object(), _evidence(), execution, fact, (decision,))

    assert commands.requests[0].execution_ref_id == "evidence:31:execution:21:CREATE_DEVICE_COMMAND:0"
    assert len(commands.requests[0].execution_ref_id) <= 120


@pytest.mark.asyncio
async def test_create_transport_task_persists_scoped_decision_mapping_before_transport() -> None:
    transport_bindings = _TransportBindings()
    transport = _Transport()
    applier = _applier(transport_binding_repository=transport_bindings, transport_service=transport)
    decision = CreateTransportTask(
        "EXEC-1",
        "evidence:31",
        TransportTaskType.RACK_MOVE,
        "REPLACE-1",
        "PRIMARY_MOVE",
        "RACK-CURRENT",
        "RACK-CURRENT",
        TransportRackPosition("BUFFER"),
        TransportRackPosition("SORTER"),
        "90",
        TransportRcsTemplateId.CTU03,
    )

    await applier.apply(object(), _evidence(), _execution(), _fact(), (decision,))

    binding = transport_bindings.bindings[(11, "REPLACE-1", "PRIMARY_MOVE")]
    assert binding.line_run_epoch_id == 11
    assert binding.resource_fence_id == "RACK-CURRENT"
    assert transport_bindings.locked == [(11, "REPLACE-1", "PRIMARY_MOVE")]
    assert transport_bindings.resource_locks == [(11, "RACK-CURRENT")]
    assert transport.calls[0]["client_request_id"] == binding.client_request_id
    assert transport.calls[0]["rack_id"] == "RACK-CURRENT"
    assert transport.calls[0]["caller"].workline_id == "7"
    assert transport.calls[0]["execution_authority"].workline_id == 7
    assert transport.calls[0]["execution_authority"].line_run_epoch_id == 11
    assert transport.calls[0]["execution_authority"].bin_execution_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["line_run_epoch_id", "resource_fence_id", "source_evidence_id"])
async def test_create_transport_task_rejects_existing_binding_correlation_drift(mismatch: str) -> None:
    transport_bindings = _TransportBindings()
    transport = _Transport()
    persisted = {
        "line_run_epoch_id": 11,
        "resource_fence_id": "RACK-CURRENT",
        "source_evidence_id": 31,
    }
    persisted[mismatch] = {
        "line_run_epoch_id": 12,
        "resource_fence_id": "RACK-OTHER",
        "source_evidence_id": 32,
    }[mismatch]
    transport_bindings.bindings[(11, "REPLACE-1", "OLD_OUT")] = SimpleNamespace(
        correlation_id="REPLACE-1",
        step="OLD_OUT",
        client_request_id="019cd8ce-34b7-7000-8000-000000000099",
        **persisted,
    )
    decision = CreateTransportTask(
        "EXEC-1",
        "evidence:31",
        TransportTaskType.RACK_MOVE,
        "REPLACE-1",
        "OLD_OUT",
        "RACK-CURRENT",
        "RACK-CURRENT",
        TransportRackPosition("BUFFER"),
        TransportRackPosition("SORTER"),
        "90",
        TransportRcsTemplateId.CTU03,
    )

    with pytest.raises(ValueError, match="transport decision binding conflict"):
        await _applier(transport_binding_repository=transport_bindings, transport_service=transport).apply(
            object(), _evidence(), _execution(), _fact(), (decision,)
        )

    assert transport.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decision", "target"),
    [
        (Wait("EXEC-1", "evidence:31", "NO_CELL"), MaterialExecutionStatus.HOLD),
        (
            PauseForReconciliation("EXEC-1", "evidence:31", "PHYSICAL_UNKNOWN", ("TRACE-1",)),
            MaterialExecutionStatus.RECONCILING,
        ),
        (CompleteExecution("EXEC-1", "evidence:31", "PLACEMENT_RECORDED"), MaterialExecutionStatus.CLOSED),
    ],
)
async def test_lifecycle_decisions_map_to_closed_execution_states(
    decision: object, target: MaterialExecutionStatus
) -> None:
    executions = _Executions()
    applier = _applier(material_execution_service=executions)

    await applier.apply(object(), _evidence(), _execution(), _fact(), (decision,))

    assert executions.targets == [target]


def test_decision_digest_includes_type_ordinal_and_payload() -> None:
    decisions = (Wait("EXEC-1", "evidence:31", "A"), Wait("EXEC-1", "evidence:31", "B"))

    assert decision_digest(decisions) == decision_digest(decisions)
    assert decision_digest(decisions) != decision_digest(tuple(reversed(decisions)))
    assert decision_digest(decisions) != decision_digest(
        (CompleteExecution("EXEC-1", "evidence:31", "A"), decisions[1])
    )


@pytest.mark.asyncio
async def test_unknown_decision_and_identity_mismatch_fail_closed() -> None:
    applier = _applier()

    with pytest.raises(TypeError, match="unsupported Decision"):
        await applier.apply(object(), _evidence(), _execution(), _fact(), (object(),))
    with pytest.raises(ValueError, match="material_execution_id"):
        await applier.apply(
            object(),
            _evidence(),
            _execution(),
            _fact(),
            (Wait("OTHER", "evidence:31", "WAIT"),),
        )
