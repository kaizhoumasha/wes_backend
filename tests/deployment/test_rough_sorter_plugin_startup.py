from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from rough_sorter.handlers import ReplacementPlanDecidedHandler, TargetDecidedHandler, TransportOutcomePublishedHandler
from wes_plugin_sdk import (
    CreateWmsConfirmation,
    DeferExecution,
    DevicePosition,
    DeviceResultReadyFact,
    EvidenceReadyFact,
    PauseForReconciliation,
    RecoveryDecision,
    TransportResultReadyFact,
    WmsResultReadyFact,
)
from wes_plugin_sdk import (
    RecoveryDecidedFact as BaseRecoveryDecidedFact,
)

from deployment._rough_sorter_transport import RoughSorterTransportOutcomePublisher
from deployment.rough_sorter_composition import (
    RoughSorterInitialExecutionCorrelator,
    RoughSorterPluginFactFactory,
    RoughSorterWmsConfirmationRequestResolver,
)
from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.execution.models import InboundEvidence, InboundEvidenceApplyStatus, InboundEvidenceKind
from src.app.execution.models.material_execution import MaterialExecution, MaterialExecutionStatus
from src.app.execution.models.wms_confirmation import WmsConfirmation, WmsConfirmationStatus
from src.app.execution.services.decision_applier import DecisionApplier
from src.app.transport.contracts import (
    RackFace,
    RackPosition,
    TransportCaller,
    TransportMemberOutcome,
    TransportOutcome,
    TransportOutcomeStatus,
)
from src.app.workline.epoch_digest import configuration_digest, topology_digest
from src.app.workline.models.line_run_epoch import (
    LineRunEpoch,
    LineRunEpochDeviceBinding,
    LineRunEpochPositionBinding,
    LineRunEpochStatus,
)
from src.core.uuid7 import is_uuid7

NOW = datetime(2026, 8, 18, 9, 0, 0)
ADMISSION_OPERATION_ID = "019d0000-0000-7000-8000-000000000031"
TARGET_OPERATION_ID = "019d0000-0000-7000-8000-000000000032"


def _device(role: str, device_id: int, contract_key: str) -> LineRunEpochDeviceBinding:
    return LineRunEpochDeviceBinding(
        id=device_id,
        line_run_epoch_id=11,
        device_id=device_id,
        device_code=f"DEVICE-{device_id}",
        device_role=role,
        contract_key=contract_key,
        contract_version="1.0",
        status_max_age_ms=1000,
        command_timeout_ms=5000,
    )


def _position(role: str, location_id: str) -> LineRunEpochPositionBinding:
    return LineRunEpochPositionBinding(
        line_run_epoch_id=11,
        position_role=role,
        location_id=location_id,
        location_type=role,
    )


class _Evidences:
    def __init__(self, evidence: InboundEvidence, *others: InboundEvidence) -> None:
        self.evidence = evidence
        self.others = {item.id: item for item in others}

    async def get_by_id_for_update(self, db: object, evidence_id: int) -> InboundEvidence | None:
        del db
        return self.evidence if evidence_id == self.evidence.id else self.others.get(evidence_id)


class _Executions:
    def __init__(self, execution: MaterialExecution) -> None:
        self.execution = execution

    async def get_by_execution_code_for_update(self, db: object, code: str) -> MaterialExecution | None:
        del db
        return self.execution if code == self.execution.execution_code else None


class _Epochs:
    def __init__(
        self,
        epoch: LineRunEpoch,
        devices: tuple[LineRunEpochDeviceBinding, ...],
        positions: tuple[LineRunEpochPositionBinding, ...],
    ) -> None:
        self.epoch = epoch
        self.devices = devices
        self.positions = positions

    async def get_by_id_for_update(self, db: object, epoch_id: int) -> LineRunEpoch | None:
        del db
        return self.epoch if epoch_id == self.epoch.id else None

    async def list_bindings(self, db: object, epoch_id: int) -> list[LineRunEpochDeviceBinding]:
        del db, epoch_id
        return list(self.devices)

    async def list_position_bindings(self, db: object, epoch_id: int) -> list[LineRunEpochPositionBinding]:
        del db, epoch_id
        return list(self.positions)

    async def get_binding_by_role_for_update(
        self, db: object, *, line_run_epoch_id: int, device_role: str
    ) -> LineRunEpochDeviceBinding | None:
        del db, line_run_epoch_id
        return next((item for item in self.devices if item.device_role == device_role), None)


class _Worklines:
    async def get_by_id(self, db: object, id: int, **kwargs: object) -> object | None:
        del db, kwargs
        return SimpleNamespace(id=id, line_code="ROUGH-LINE-1")


class _Confirmations:
    def __init__(self, confirmation: WmsConfirmation, *others: WmsConfirmation) -> None:
        self.confirmation = confirmation
        self.confirmations = (confirmation, *others)

    async def get_by_identity_for_update(self, db: object, operation: str, operation_id: str) -> WmsConfirmation | None:
        del db
        if (operation, operation_id) == (self.confirmation.operation, self.confirmation.operation_id):
            return self.confirmation
        return None

    async def list_for_execution(self, db: object, material_execution_id: int) -> list[WmsConfirmation]:
        del db
        return [item for item in self.confirmations if item.material_execution_id == material_execution_id]

    async def list_for_executions_for_update(
        self, db: object, *, material_execution_ids: tuple[int, ...], operation: str
    ) -> list[WmsConfirmation]:
        del db
        return [
            item
            for item in self.confirmations
            if item.material_execution_id in material_execution_ids and item.operation == operation
        ]


class _Readiness:
    async def is_ready(self, db: object, binding: object, *, observed_at: datetime) -> bool:
        del db, binding, observed_at
        return True


class _Commands:
    def __init__(self, *commands: DeviceCommand) -> None:
        self.commands = commands

    async def get_by_command_code(
        self, db: object, command_code: str, *, for_update: bool = False
    ) -> DeviceCommand | None:
        del db, for_update
        return next((item for item in self.commands if command_code == item.command_code), None)

    async def list_for_material_execution(
        self, db: object, *, line_run_epoch_id: int, material_execution_id: int
    ) -> list[DeviceCommand]:
        del db
        return [
            item
            for item in self.commands
            if item.line_run_epoch_id == line_run_epoch_id and item.material_execution_id == material_execution_id
        ]

    async def list_for_epoch_for_update(self, db: object, *, line_run_epoch_id: int) -> list[DeviceCommand]:
        del db
        return [item for item in self.commands if item.line_run_epoch_id == line_run_epoch_id]


class _RackBindings:
    def __init__(self, *, fenced: bool = False, events: list[str] | None = None) -> None:
        self.fenced = fenced
        self.events = events
        self.locked: list[tuple[int, str]] = []

    async def lock_rack_fence(self, db: object, *, line_run_epoch_id: int, current_rack_id: str) -> None:
        del db
        self.locked.append((line_run_epoch_id, current_rack_id))
        if self.events is not None:
            self.events.append("rack-fence-lock")

    async def get_old_out_fence_for_update(
        self, db: object, *, line_run_epoch_id: int, current_rack_id: str
    ) -> object | None:
        del db
        if not self.fenced:
            return None
        return SimpleNamespace(
            line_run_epoch_id=line_run_epoch_id,
            current_rack_id=current_rack_id,
            leg="OLD_OUT",
        )


class _RackPositions:
    async def get_by_workline_logic_location(
        self, db: object, *, workline_code: str, logic_location_code: str
    ) -> object | None:
        del db
        assert (workline_code, logic_location_code) == ("ROUGH-LINE-1", "OUTLET-1")
        return SimpleNamespace(position_code="RACK-WORK", logic_location_code=logic_location_code, enabled=True)


class _Placements:
    async def list_active_by_workline_position(
        self, db: object, *, workline_code: str, position_code: str
    ) -> list[object]:
        del db
        assert (workline_code, position_code) == ("ROUGH-LINE-1", "RACK-WORK")
        return [SimpleNamespace(rack_code="RACK-1", logic_location_code="OUTLET-1", placement_status="ARRIVED")]


def _factory(*, topology_override: str | None = None) -> tuple[RoughSorterPluginFactFactory, EvidenceReadyFact]:
    devices = (
        _device("MEASUREMENT_DEVICE", 1, "rough_sorter.measurement_device"),
        _device("TRANSFER_DEVICE", 2, "rough_sorter.transfer_device"),
        _device("PLACEMENT_DEVICE", 3, "rough_sorter.placement_device"),
    )
    positions = (
        _position("MEASUREMENT_POSITION", "MEASUREMENT-1"),
        _position("PIPELINE_INLET", "INLET-1"),
        _position("PIPELINE_OUTLET", "OUTLET-1"),
        _position("NG_POSITION", "NG-1"),
    )
    epoch = LineRunEpoch(
        id=11,
        epoch_code="EPOCH-11",
        workline_id=7,
        plugin_key="rough_sorter",
        plugin_version="1.0.0",
        flow_mode="ROUGH_SORT_INBOUND",
        topology_digest=topology_override or topology_digest(devices, positions),
        configuration_digest=configuration_digest("rough_sorter", "1.0.0", "ROUGH_SORT_INBOUND"),
        status=LineRunEpochStatus.ACTIVE,
        started_at=NOW,
    )
    execution = MaterialExecution(
        id=21,
        execution_code="EXEC-21",
        material_trace_id="TRACE-21",
        workline_id=7,
        line_run_epoch_id=11,
        status=MaterialExecutionStatus.CREATED,
        last_transition_reason="INITIAL_EVIDENCE",
        last_transition_evidence_id=31,
        status_changed_at=NOW,
    )
    evidence = InboundEvidence(
        id=31,
        kind=InboundEvidenceKind.DEVICE_EVENT,
        source_identity="SCAN-31",
        payload_digest="a" * 64,
        normalized_payload={
            "event_type": "SCAN_COMPLETED",
            "timestamp": 1_787_040_000_000,
            "data": {
                "material_trace_id": "TRACE-21",
                "LotCode": "LOT",
                "DateCode": "DATE",
                "Qty": "1",
                "ProductNo": "PRODUCT",
                "MfrPN": "MFR",
                "PONumber": "PO",
                "diameter_mm": "12.5",
                "thickness_mm": "1.2",
                "shape_result": "PASS",
                "position": {
                    "location_id": "MEASUREMENT-1",
                    "location_type": "MEASUREMENT_POSITION",
                    "material_trace_id": "TRACE-21",
                },
            },
        },
        received_at=NOW,
        line_run_epoch_id=11,
        material_execution_id=21,
        device_code="DEVICE-1",
        contract_key="rough_sorter.measurement_device",
        contract_version="1.0",
        apply_status=InboundEvidenceApplyStatus.APPLIED,
    )
    base = EvidenceReadyFact("evidence:31", "31", "1.0", "EXEC-21")
    return (
        RoughSorterPluginFactFactory(
            evidence_repository=_Evidences(evidence),
            execution_repository=_Executions(execution),
            epoch_repository=_Epochs(epoch, devices, positions),
            workline_repository=_Worklines(),
        ),
        base,
    )


@pytest.mark.asyncio
async def test_factory_builds_stable_scan_fact_from_same_transaction_snapshot() -> None:
    factory, base = _factory()
    db = object()

    first = await factory.build(db, base)
    second = await factory.build(db, base)

    assert first == second
    assert first.runtime_snapshot.execution.material_execution_id == "EXEC-21"
    assert first.runtime_snapshot.epoch.workline_code == "ROUGH-LINE-1"
    assert len(first.runtime_snapshot.epoch.position_bindings) == 4
    assert is_uuid7(first.request_operation_id)
    assert first.source_position.location_id == "MEASUREMENT-1"


@pytest.mark.asyncio
async def test_factory_rejects_epoch_digest_drift_before_building_plugin_fact() -> None:
    factory, base = _factory(topology_override="f" * 64)

    with pytest.raises(ValueError, match="topology digest"):
        await factory.build(object(), base)


@pytest.mark.asyncio
async def test_factory_builds_admission_fact_from_confirmation_request_and_response_evidence() -> None:
    factory, _ = _factory()
    evidence = InboundEvidence(
        id=32,
        kind=InboundEvidenceKind.WMS_RESULT,
        source_identity=f"inbound.material.admission_decide@v1:{ADMISSION_OPERATION_ID}",
        payload_digest="b" * 64,
        normalized_payload={
            "operation_id": ADMISSION_OPERATION_ID,
            "code": "DECIDED",
            "timestamp": 1_787_040_000_100,
            "data": {"result": "ACCEPT", "pkg_id": "PKG-1", "inbound_admission_id": "ADM-1"},
        },
        received_at=NOW,
        line_run_epoch_id=11,
        material_execution_id=21,
        contract_key="rough_sorter_inbound",
        contract_version="1.0",
        operation="inbound.material.admission_decide@v1",
        operation_id=ADMISSION_OPERATION_ID,
        apply_status=InboundEvidenceApplyStatus.APPLIED,
    )
    confirmation = WmsConfirmation(
        id=51,
        operation=evidence.operation,
        operation_id=evidence.operation_id,
        material_execution_id=21,
        request_digest="c" * 64,
        request_payload={
            "operation": evidence.operation,
            "operation_id": evidence.operation_id,
            "timestamp": 1_787_040_000_000,
            "data": {
                "material_execution_id": "EXEC-21",
                "material_trace_id": "TRACE-21",
                "source_position": {"type": "HANDOFF_POSITION", "location_code": "MEASUREMENT-1"},
                "six_in_one": {
                    "LotCode": "LOT",
                    "DateCode": "DATE",
                    "Qty": "1",
                    "ProductNo": "PRODUCT",
                    "MfrPN": "MFR",
                    "PONumber": "PO",
                },
                "measurements": {"diameter_mm": "12.5", "thickness_mm": "1.2"},
                "shape_result": "PASS",
                "line_run_epoch_id": "11",
                "workline_code": "ROUGH-LINE-1",
            },
        },
        deadline_at=NOW,
        status=WmsConfirmationStatus.COMPLETED,
        response_evidence_id=32,
        response_result="ACCEPT",
        completed_at=NOW,
    )
    factory._evidences.evidence = evidence  # type: ignore[attr-defined]
    factory._wms_confirmations = _Confirmations(confirmation)  # type: ignore[attr-defined]
    factory._device_readiness = _Readiness()  # type: ignore[attr-defined]
    base = WmsResultReadyFact("evidence:32", "32", "1.0", "EXEC-21", ADMISSION_OPERATION_ID)

    fact = await factory.build(object(), base)

    assert fact.result.value == "ACCEPT"
    assert fact.pkg_id == "PKG-1"
    assert fact.inbound_admission_id == "ADM-1"
    assert fact.source_position.location_id == "MEASUREMENT-1"
    assert fact.next_position.location_id == "INLET-1"
    assert fact.device_ready is True


@pytest.mark.asyncio
async def test_factory_builds_assigned_target_fact_without_recomputing_wms_cell() -> None:
    factory, _ = _factory()
    operation = "inbound.material.target_decide@v1"
    evidence = InboundEvidence(
        id=33,
        kind=InboundEvidenceKind.WMS_RESULT,
        source_identity=f"{operation}:{TARGET_OPERATION_ID}",
        payload_digest="d" * 64,
        normalized_payload={
            "operation_id": TARGET_OPERATION_ID,
            "code": "DECIDED",
            "timestamp": 1_787_040_000_200,
            "data": {
                "result": "ASSIGNED",
                "target_assignment_id": "ASSIGN-1",
                "target_position": {
                    "type": "ONE_LAYER_BIN_CELL",
                    "rack_id": "RACK-1",
                    "rack_slot_code": "SLOT-1",
                    "bin_id": "BIN-1",
                    "bin_cell_id": "CELL-1",
                },
                "placement_sequence": 1,
                "expected_height_mm": "3.2",
            },
        },
        received_at=NOW,
        line_run_epoch_id=11,
        material_execution_id=21,
        contract_key="rough_sorter_inbound",
        contract_version="1.0",
        operation=operation,
        operation_id=TARGET_OPERATION_ID,
        apply_status=InboundEvidenceApplyStatus.APPLIED,
    )
    confirmation = WmsConfirmation(
        id=52,
        operation=operation,
        operation_id=TARGET_OPERATION_ID,
        material_execution_id=21,
        request_digest="e" * 64,
        request_payload={
            "operation": operation,
            "operation_id": TARGET_OPERATION_ID,
            "timestamp": 1_787_040_000_100,
            "data": {
                "material_execution_id": "EXEC-21",
                "material_trace_id": "TRACE-21",
                "pkg_id": "PKG-1",
                "inbound_admission_id": "ADM-1",
                "source_position": {"type": "HANDOFF_POSITION", "location_code": "OUTLET-1"},
                "current_rack_id": "RACK-1",
            },
        },
        deadline_at=NOW,
        status=WmsConfirmationStatus.COMPLETED,
        response_evidence_id=33,
        response_result="ASSIGNED",
        completed_at=NOW,
    )
    factory._evidences.evidence = evidence  # type: ignore[attr-defined]
    factory._wms_confirmations = _Confirmations(confirmation)  # type: ignore[attr-defined]
    factory._device_readiness = _Readiness()  # type: ignore[attr-defined]
    rack_bindings = _RackBindings(fenced=True)
    factory._rack_replacement_bindings = rack_bindings  # type: ignore[attr-defined]
    base = WmsResultReadyFact("evidence:33", "33", "1.0", "EXEC-21", TARGET_OPERATION_ID)

    fact = await factory.build(object(), base)

    assert fact.result.value == "ASSIGNED"
    assert fact.current_rack_id == "RACK-1"
    assert fact.target_position.bin_cell_id == "CELL-1"
    assert fact.target_assignment_id == "ASSIGN-1"
    assert fact.placement_sequence == 1
    assert fact.device_ready is True
    assert fact.current_rack_fenced is True
    assert rack_bindings.locked == [(11, "RACK-1")]
    assert TargetDecidedHandler()(fact) == (
        PauseForReconciliation(
            material_execution_id="EXEC-21",
            fact_id="evidence:33",
            reason_code="CURRENT_RACK_ALREADY_REPLACED",
            affected_resource_ids=("RACK-1",),
        ),
    )


@pytest.mark.asyncio
async def test_factory_rebuilds_measurement_callback_from_command_source_evidence_and_epoch_outlet() -> None:
    factory, _ = _factory()
    command_code = "019d0000-0000-7000-8000-000000000033"
    command = DeviceCommand(
        id=61,
        command_code=command_code,
        device_code="DEVICE-1",
        device_binding_id=1,
        line_run_epoch_id=11,
        execution_ref_type="PLUGIN_DECISION",
        execution_ref_id="evidence:32:execution:21:CREATE_DEVICE_COMMAND:0",
        material_execution_id=21,
        contract_key="rough_sorter.measurement_device",
        contract_version="1.0",
        task_type="PICK_AND_PUT",
        params={
            "material_trace_id": "TRACE-21",
            "source": {
                "location_id": "MEASUREMENT-1",
                "location_type": "MEASUREMENT_POSITION",
                "material_trace_id": "TRACE-21",
                "rack_id": None,
                "rack_slot_code": None,
                "bin_id": None,
                "bin_cell_id": None,
            },
            "target": {
                "location_id": "INLET-1",
                "location_type": "PIPELINE_INLET",
                "material_trace_id": "TRACE-21",
                "rack_id": None,
                "rack_slot_code": None,
                "bin_id": None,
                "bin_cell_id": None,
            },
        },
        deadline_at=NOW,
        payload_digest="f" * 64,
        status=CommandStatus.SUCCEEDED,
        result_evidence_id=34,
    )
    evidence = InboundEvidence(
        id=34,
        kind=InboundEvidenceKind.DEVICE_RESULT,
        source_identity="RESULT-34",
        payload_digest="1" * 64,
        normalized_payload={
            "command_code": command_code,
            "device_code": "DEVICE-1",
            "contract_key": "rough_sorter.measurement_device",
            "contract_version": "1.0",
            "result": "SUCCESS",
            "finish_time": 1_787_040_000_300,
            "source_event_id": "RESULT-34",
            "data": {
                "material_trace_id": "TRACE-21",
                "actual_position": {
                    "location_id": "INLET-1",
                    "location_type": "PIPELINE_INLET",
                    "material_trace_id": "TRACE-21",
                    "rack_id": None,
                    "rack_slot_code": None,
                    "bin_id": None,
                    "bin_cell_id": None,
                },
            },
            "error_detail": None,
        },
        received_at=NOW,
        line_run_epoch_id=11,
        material_execution_id=21,
        device_code="DEVICE-1",
        command_code=command_code,
        contract_key="rough_sorter.measurement_device",
        contract_version="1.0",
        apply_status=InboundEvidenceApplyStatus.APPLIED,
    )
    factory._evidences.evidence = evidence  # type: ignore[attr-defined]
    factory._evidences.others[32] = InboundEvidence(  # type: ignore[attr-defined]
        id=32,
        kind=InboundEvidenceKind.WMS_RESULT,
        source_identity=f"inbound.material.admission_decide@v1:{ADMISSION_OPERATION_ID}",
        payload_digest="2" * 64,
        normalized_payload={"operation_id": ADMISSION_OPERATION_ID, "code": "DECIDED", "data": {"result": "ACCEPT"}},
        received_at=NOW,
        line_run_epoch_id=11,
        material_execution_id=21,
        contract_key="rough_sorter_inbound",
        contract_version="1.0",
        operation="inbound.material.admission_decide@v1",
        operation_id=ADMISSION_OPERATION_ID,
        apply_status=InboundEvidenceApplyStatus.APPLIED,
    )
    factory._commands = _Commands(command)  # type: ignore[attr-defined]
    factory._device_readiness = _Readiness()  # type: ignore[attr-defined]
    base = DeviceResultReadyFact("evidence:34", "34", "1.0", "EXEC-21", command_code, "DEVICE-1", "TRACE-21")

    fact = await factory.build(object(), base)

    assert fact.step.value == "MEASUREMENT_TO_INLET"
    assert fact.source_position.location_id == "MEASUREMENT-1"
    assert fact.actual_position.location_id == "INLET-1"
    assert fact.next_position.location_id == "OUTLET-1"
    assert fact.next_device_ready is True


@pytest.mark.asyncio
async def test_factory_rebuilds_transfer_callback_with_admission_and_current_rack_chain() -> None:
    factory, _ = _factory()
    command_code = "019d0000-0000-7000-8000-000000000034"
    command = DeviceCommand(
        id=62,
        command_code=command_code,
        device_code="DEVICE-2",
        device_binding_id=2,
        line_run_epoch_id=11,
        execution_ref_type="PLUGIN_DECISION",
        execution_ref_id="evidence:34:execution:21:CREATE_DEVICE_COMMAND:0",
        material_execution_id=21,
        contract_key="rough_sorter.transfer_device",
        contract_version="1.0",
        task_type="MOVE_FORWARD",
        params={
            "material_trace_id": "TRACE-21",
            "source": {
                "location_id": "INLET-1",
                "location_type": "PIPELINE_INLET",
                "material_trace_id": "TRACE-21",
                "rack_id": None,
                "rack_slot_code": None,
                "bin_id": None,
                "bin_cell_id": None,
            },
            "target": {
                "location_id": "OUTLET-1",
                "location_type": "PIPELINE_OUTLET",
                "material_trace_id": "TRACE-21",
                "rack_id": None,
                "rack_slot_code": None,
                "bin_id": None,
                "bin_cell_id": None,
            },
        },
        deadline_at=NOW,
        payload_digest="3" * 64,
        status=CommandStatus.SUCCEEDED,
        result_evidence_id=35,
    )
    result_evidence = InboundEvidence(
        id=35,
        kind=InboundEvidenceKind.DEVICE_RESULT,
        source_identity="RESULT-35",
        payload_digest="4" * 64,
        normalized_payload={
            "command_code": command_code,
            "device_code": "DEVICE-2",
            "contract_key": "rough_sorter.transfer_device",
            "contract_version": "1.0",
            "result": "SUCCESS",
            "finish_time": 1_787_040_000_400,
            "source_event_id": "RESULT-35",
            "data": {
                "material_trace_id": "TRACE-21",
                "actual_position": {
                    "location_id": "OUTLET-1",
                    "location_type": "PIPELINE_OUTLET",
                    "material_trace_id": "TRACE-21",
                    "rack_id": None,
                    "rack_slot_code": None,
                    "bin_id": None,
                    "bin_cell_id": None,
                },
            },
            "error_detail": None,
        },
        received_at=NOW,
        line_run_epoch_id=11,
        material_execution_id=21,
        device_code="DEVICE-2",
        command_code=command_code,
        contract_key="rough_sorter.transfer_device",
        contract_version="1.0",
        apply_status=InboundEvidenceApplyStatus.APPLIED,
    )
    source_result = result_evidence.model_copy(
        update={"id": 34, "source_identity": "RESULT-34", "command_code": "PRIOR"}
    )
    admission_evidence = InboundEvidence(
        id=32,
        kind=InboundEvidenceKind.WMS_RESULT,
        source_identity=f"inbound.material.admission_decide@v1:{ADMISSION_OPERATION_ID}",
        payload_digest="5" * 64,
        normalized_payload={
            "operation_id": ADMISSION_OPERATION_ID,
            "code": "DECIDED",
            "timestamp": 1_787_040_000_100,
            "data": {"result": "ACCEPT", "pkg_id": "PKG-1", "inbound_admission_id": "ADM-1"},
        },
        received_at=NOW,
        line_run_epoch_id=11,
        material_execution_id=21,
        contract_key="rough_sorter_inbound",
        contract_version="1.0",
        operation="inbound.material.admission_decide@v1",
        operation_id=ADMISSION_OPERATION_ID,
        apply_status=InboundEvidenceApplyStatus.APPLIED,
    )
    admission = WmsConfirmation(
        id=51,
        operation="inbound.material.admission_decide@v1",
        operation_id=ADMISSION_OPERATION_ID,
        material_execution_id=21,
        request_digest="6" * 64,
        request_payload={
            "operation": "inbound.material.admission_decide@v1",
            "operation_id": ADMISSION_OPERATION_ID,
            "timestamp": 1,
            "data": {
                "material_execution_id": "EXEC-21",
                "material_trace_id": "TRACE-21",
                "six_in_one": {
                    "LotCode": "L",
                    "DateCode": "D",
                    "Qty": "1",
                    "ProductNo": "P",
                    "MfrPN": "M",
                    "PONumber": "PO",
                },
                "measurements": {"diameter_mm": "1", "thickness_mm": "1"},
                "shape_result": "PASS",
                "line_run_epoch_id": "11",
                "workline_code": "ROUGH-LINE-1",
                "source_position": {"type": "HANDOFF_POSITION", "location_code": "MEASUREMENT-1"},
            },
        },
        deadline_at=NOW,
        status=WmsConfirmationStatus.COMPLETED,
        response_evidence_id=32,
        response_result="ACCEPT",
        completed_at=NOW,
    )
    factory._evidences = _Evidences(result_evidence, source_result, admission_evidence)  # type: ignore[attr-defined]
    factory._commands = _Commands(command)  # type: ignore[attr-defined]
    factory._wms_confirmations = _Confirmations(admission)  # type: ignore[attr-defined]
    factory._rack_positions = _RackPositions()  # type: ignore[attr-defined]
    factory._rack_placements = _Placements()  # type: ignore[attr-defined]
    base = DeviceResultReadyFact("evidence:35", "35", "1.0", "EXEC-21", command_code, "DEVICE-2", "TRACE-21")

    fact = await factory.build(object(), base)

    assert fact.step.value == "TRANSFER_TO_OUTLET"
    assert fact.pkg_id == "PKG-1"
    assert fact.inbound_admission_id == "ADM-1"
    assert fact.current_rack_id == "RACK-1"
    assert fact.request_operation_id == command_code

    resolved = await RoughSorterWmsConfirmationRequestResolver(
        fact_factory=factory,
        evidence_repository=factory._evidences,  # type: ignore[attr-defined]
        execution_repository=factory._executions,  # type: ignore[attr-defined]
    ).resolve(
        object(),
        CreateWmsConfirmation(
            material_execution_id="EXEC-21",
            fact_id=base.fact_id,
            operation="inbound.material.target_decide@v1",
            operation_id=command_code,
            evidence_refs=(base.evidence_id,),
            snapshot_refs=("execution:EXEC-21", "rack:RACK-1"),
        ),
    )
    assert resolved.request_payload["data"] == {
        "material_execution_id": "EXEC-21",
        "material_trace_id": "TRACE-21",
        "pkg_id": "PKG-1",
        "inbound_admission_id": "ADM-1",
        "source_position": {"type": "HANDOFF_POSITION", "location_code": "OUTLET-1"},
        "current_rack_id": "RACK-1",
    }


@pytest.mark.asyncio
async def test_factory_builds_ready_replacement_with_release_snapshot_and_two_transport_legs() -> None:
    factory, _ = _factory()
    operation = "inbound.source_rack.replacement_plan_decide@v1"
    operation_id = "019d0000-0000-7000-8000-000000000035"
    evidence = InboundEvidence(
        id=36,
        kind=InboundEvidenceKind.WMS_RESULT,
        source_identity=f"{operation}:{operation_id}",
        payload_digest="7" * 64,
        normalized_payload={
            "operation_id": operation_id,
            "code": "DECIDED",
            "timestamp": 1_787_040_000_500,
            "data": {
                "result": "READY",
                "rack_replacement_id": "REPLACE-1",
                "old_loaded_rack": {
                    "rack_id": "RACK-1",
                    "source": {"type": "RACK_POSITION", "location_code": "OUTLET-1"},
                    "target": {"type": "RACK_POSITION", "location_code": "BUFFER-OLD"},
                    "target_face": "A",
                },
                "new_empty_rack": {
                    "rack_id": "RACK-2",
                    "source": {"type": "RACK_POSITION", "location_code": "BUFFER-NEW"},
                    "target": {"type": "RACK_POSITION", "location_code": "OUTLET-1"},
                    "target_face": "B",
                },
            },
        },
        received_at=NOW,
        line_run_epoch_id=11,
        material_execution_id=21,
        contract_key="rough_sorter_inbound",
        contract_version="1.0",
        operation=operation,
        operation_id=operation_id,
        apply_status=InboundEvidenceApplyStatus.APPLIED,
    )
    confirmation = WmsConfirmation(
        id=53,
        operation=operation,
        operation_id=operation_id,
        material_execution_id=21,
        request_digest="8" * 64,
        request_payload={
            "operation": operation,
            "operation_id": operation_id,
            "timestamp": 1_787_040_000_400,
            "data": {
                "material_execution_id": "EXEC-21",
                "material_trace_id": "TRACE-21",
                "current_rack_id": "RACK-1",
            },
        },
        deadline_at=NOW,
        status=WmsConfirmationStatus.COMPLETED,
        response_evidence_id=36,
        response_result="READY",
        completed_at=NOW,
    )
    placement_command_code = "019d0000-0000-7000-8000-000000000099"
    placement_command = DeviceCommand(
        id=99,
        command_code=placement_command_code,
        device_code="DEVICE-3",
        device_binding_id=3,
        line_run_epoch_id=11,
        execution_ref_type="PLUGIN_DECISION",
        execution_ref_id="evidence:98:execution:22:CREATE_DEVICE_COMMAND:0",
        material_execution_id=22,
        contract_key="rough_sorter.placement_device",
        contract_version="1.0",
        task_type="PICK_AND_PUT",
        params={
            "material_trace_id": "TRACE-22",
            "source": {
                "location_id": "OUTLET-1",
                "location_type": "PIPELINE_OUTLET",
                "material_trace_id": "TRACE-22",
            },
            "target": {
                "location_id": "CELL-99",
                "location_type": "RACK_CELL",
                "material_trace_id": "TRACE-22",
                "rack_id": "RACK-1",
                "rack_slot_code": "SLOT-99",
                "bin_id": "BIN-99",
                "bin_cell_id": "CELL-99",
            },
        },
        deadline_at=NOW,
        payload_digest="9" * 64,
        status=CommandStatus.ACKNOWLEDGED,
    )
    factory._evidences.evidence = evidence  # type: ignore[attr-defined]
    factory._wms_confirmations = _Confirmations(confirmation)  # type: ignore[attr-defined]
    factory._rack_positions = _RackPositions()  # type: ignore[attr-defined]
    factory._rack_placements = _Placements()  # type: ignore[attr-defined]
    events: list[str] = []

    class Commands(_Commands):
        async def list_for_epoch_for_update(self, db: object, *, line_run_epoch_id: int) -> list[DeviceCommand]:
            events.append("release-snapshot")
            return await super().list_for_epoch_for_update(db, line_run_epoch_id=line_run_epoch_id)

    rack_bindings = _RackBindings(events=events)
    factory._commands = Commands(placement_command)  # type: ignore[attr-defined]
    factory._rack_replacement_bindings = rack_bindings  # type: ignore[attr-defined]

    fact = await factory.build(
        object(),
        WmsResultReadyFact("evidence:36", "36", "1.0", "EXEC-21", operation_id),
    )

    assert fact.result.value == "READY"
    assert fact.release_snapshot.current_rack_id == "RACK-1"
    assert tuple(item.command_code for item in fact.release_snapshot.placements) == (placement_command_code,)
    assert fact.release_snapshot.placements[0].confirmation_status.value == "ABSENT"
    assert fact.release_snapshot.placements[0].confirmation_operation_id is None
    assert rack_bindings.locked == [(11, "RACK-1")]
    assert events == ["rack-fence-lock", "release-snapshot"]
    assert ReplacementPlanDecidedHandler()(fact) == (
        DeferExecution("EXEC-21", "evidence:36", "RACK_RELEASE_GATE_NOT_CLOSED"),
    )
    assert fact.old_loaded_rack.rack_id == "RACK-1"
    assert fact.new_empty_rack.rack_id == "RACK-2"

    admission_evidence = InboundEvidence(
        id=32,
        kind=InboundEvidenceKind.WMS_RESULT,
        source_identity=f"inbound.material.admission_decide@v1:{ADMISSION_OPERATION_ID}",
        payload_digest="e" * 64,
        normalized_payload={
            "operation_id": ADMISSION_OPERATION_ID,
            "code": "DECIDED",
            "timestamp": 1,
            "data": {"result": "ACCEPT", "pkg_id": "PKG-1", "inbound_admission_id": "ADM-1"},
        },
        received_at=NOW,
        line_run_epoch_id=11,
        material_execution_id=21,
        contract_key="rough_sorter_inbound",
        contract_version="1.0",
        operation="inbound.material.admission_decide@v1",
        operation_id=ADMISSION_OPERATION_ID,
        apply_status=InboundEvidenceApplyStatus.APPLIED,
    )
    admission_confirmation = WmsConfirmation(
        id=54,
        operation="inbound.material.admission_decide@v1",
        operation_id=ADMISSION_OPERATION_ID,
        material_execution_id=21,
        request_digest="f" * 64,
        request_payload={"operation": "inbound.material.admission_decide@v1"},
        deadline_at=NOW,
        status=WmsConfirmationStatus.COMPLETED,
        response_evidence_id=32,
        response_result="ACCEPT",
        completed_at=NOW,
    )
    transport_evidence = InboundEvidence(
        id=38,
        kind=InboundEvidenceKind.TRANSPORT_RESULT,
        source_identity="transport:TRANSPORT-NEW:outcome:1",
        payload_digest="0" * 64,
        normalized_payload={
            "transport_task_id": "TRANSPORT-NEW",
            "client_request_id": "019d0000-0000-7000-8000-000000000041",
            "outcome_version": 1,
            "caller": {"workline_id": "7"},
            "status": "SUCCEEDED",
            "reason_code": None,
            "members": [
                {
                    "object_id": "RACK-2",
                    "final_position": {"kind": "RACK_POSITION", "location_code": "OUTLET-1"},
                    "position_unknown": False,
                    "failure_code": None,
                    "arrival_face": "B",
                }
            ],
        },
        received_at=NOW,
        line_run_epoch_id=11,
        material_execution_id=21,
        transport_task_id="TRANSPORT-NEW",
        contract_key="rough_sorter.transport_outcome",
        contract_version="1.0",
        apply_status=InboundEvidenceApplyStatus.APPLIED,
    )

    class Bindings:
        async def get_by_client_request_id_for_update(self, db: object, client_request_id: str) -> object:
            del db
            assert client_request_id == "019d0000-0000-7000-8000-000000000041"
            return SimpleNamespace(
                leg="NEW_IN",
                source_evidence_id=36,
                rack_replacement_id="REPLACE-1",
                line_run_epoch_id=11,
                current_rack_id="RACK-1",
            )

    class ProjectedNewRack:
        def __init__(self) -> None:
            self.calls = 0

        async def list_active_by_workline_position(
            self, db: object, *, workline_code: str, position_code: str
        ) -> list[object]:
            del db, workline_code, position_code
            self.calls += 1
            return [SimpleNamespace(rack_code="RACK-2", logic_location_code="OUTLET-1", placement_status="ARRIVED")]

    transport_task = SimpleNamespace(
        transport_task_id="TRANSPORT-NEW",
        client_request_id="019d0000-0000-7000-8000-000000000041",
        kind="RACK_MOVE",
        request_json={
            "client_request_id": "019d0000-0000-7000-8000-000000000041",
            "caller": {"workline_id": "7", "station_id": None},
            "rack_id": "RACK-2",
            "source": {"location_code": "BUFFER-NEW", "kind": "RACK_POSITION"},
            "target": {"location_code": "OUTLET-1", "kind": "RACK_POSITION"},
            "target_face": "B",
            "kind": "RACK_MOVE",
        },
    )

    class TransportTasks:
        async def get_task_by_client_request(self, db: object, client_request_id: str) -> object | None:
            del db
            return transport_task if client_request_id == transport_task.client_request_id else None

    factory._evidences = _Evidences(transport_evidence, evidence, admission_evidence)  # type: ignore[attr-defined]
    factory._wms_confirmations = _Confirmations(confirmation, admission_confirmation)  # type: ignore[attr-defined]
    factory._rack_replacement_bindings = Bindings()  # type: ignore[attr-defined]
    factory._transport_tasks = TransportTasks()  # type: ignore[attr-defined]
    projected_new_rack = ProjectedNewRack()
    factory._rack_placements = projected_new_rack  # type: ignore[attr-defined]

    transport_fact = await factory.build(
        object(),
        TransportResultReadyFact("evidence:38", "38", "1.0", "EXEC-21", "TRANSPORT-NEW"),
    )

    assert transport_fact.rack_replacement_id == "REPLACE-1"
    assert transport_fact.rack_id == "RACK-2"
    assert transport_fact.final_position.location_code == "OUTLET-1"
    assert transport_fact.request_operation_id == "019d0000-0000-7000-8000-000000000041"
    assert projected_new_rack.calls == 0

    decisions = TransportOutcomePublishedHandler()(transport_fact)

    class ConfirmationCreator:
        def __init__(self) -> None:
            self.requests: list[dict[str, object]] = []

        async def create_or_get(self, db: object, **kwargs: object) -> object:
            del db
            self.requests.append(kwargs)
            return object()

    class ExecutionTransitions:
        async def transition(self, db: object, current: MaterialExecution, **kwargs: object) -> MaterialExecution:
            del db, kwargs
            return current

    confirmations = ConfirmationCreator()
    resolver = RoughSorterWmsConfirmationRequestResolver(
        fact_factory=factory,
        evidence_repository=factory._evidences,  # type: ignore[attr-defined]
        execution_repository=factory._executions,  # type: ignore[attr-defined]
    )
    applier = DecisionApplier(
        device_command_service=object(),
        wms_confirmation_service=confirmations,
        wms_request_resolver=resolver,
        rack_binding_repository=object(),
        transport_service=object(),
        material_execution_service=ExecutionTransitions(),
        clock=lambda: NOW,
    )

    await applier.apply(object(), transport_evidence, factory._executions.execution, transport_fact, decisions)  # type: ignore[attr-defined]

    assert confirmations.requests == [
        {
            "operation": "inbound.material.target_decide@v1",
            "operation_id": "019d0000-0000-7000-8000-000000000041",
            "material_execution_id": 21,
            "request_payload": {
                "operation_id": "019d0000-0000-7000-8000-000000000041",
                "operation": "inbound.material.target_decide@v1",
                "timestamp": 1_787_043_600_000,
                "data": {
                    "material_execution_id": "EXEC-21",
                    "material_trace_id": "TRACE-21",
                    "pkg_id": "PKG-1",
                    "inbound_admission_id": "ADM-1",
                    "source_position": {"type": "HANDOFF_POSITION", "location_code": "OUTLET-1"},
                    "current_rack_id": "RACK-2",
                },
            },
            "deadline_at": datetime(2026, 8, 18, 9, 0, 30),
            "created_at": NOW,
        }
    ]

    transport_evidence.normalized_payload["members"][0]["object_id"] = "RACK-UNPLANNED"  # type: ignore[index]
    mismatch_fact = await factory.build(
        object(),
        TransportResultReadyFact("evidence:38", "38", "1.0", "EXEC-21", "TRANSPORT-NEW"),
    )

    assert mismatch_fact.actual_rack_id == "RACK-UNPLANNED"
    assert TransportOutcomePublishedHandler()(mismatch_fact) == (
        PauseForReconciliation(
            material_execution_id="EXEC-21",
            fact_id="evidence:38",
            reason_code="NEW_RACK_ARRIVAL_MISMATCH",
            affected_resource_ids=("RACK-2", "RACK-UNPLANNED"),
        ),
    )

    transport_task.request_json["target"] = {"location_code": "DRIFTED", "kind": "RACK_POSITION"}
    with pytest.raises(ValueError, match="TransportTask frozen plan"):
        await factory.build(
            object(),
            TransportResultReadyFact("evidence:38", "38", "1.0", "EXEC-21", "TRANSPORT-NEW"),
        )


@pytest.mark.asyncio
async def test_factory_rebuilds_placement_callback_and_resolver_uses_frozen_assignment() -> None:
    factory, _ = _factory()
    command_code = "019d0000-0000-7000-8000-000000000036"
    target = {
        "location_id": "CELL-1",
        "location_type": "RACK_CELL",
        "material_trace_id": "TRACE-21",
        "rack_id": "RACK-1",
        "rack_slot_code": "SLOT-1",
        "bin_id": "BIN-1",
        "bin_cell_id": "CELL-1",
    }
    command = DeviceCommand(
        id=63,
        command_code=command_code,
        device_code="DEVICE-3",
        device_binding_id=3,
        line_run_epoch_id=11,
        execution_ref_type="PLUGIN_DECISION",
        execution_ref_id="evidence:33:execution:21:CREATE_DEVICE_COMMAND:0",
        material_execution_id=21,
        contract_key="rough_sorter.placement_device",
        contract_version="1.0",
        task_type="PICK_AND_PUT",
        params={
            "material_trace_id": "TRACE-21",
            "source": {
                "location_id": "OUTLET-1",
                "location_type": "PIPELINE_OUTLET",
                "material_trace_id": "TRACE-21",
                "rack_id": None,
                "rack_slot_code": None,
                "bin_id": None,
                "bin_cell_id": None,
            },
            "target": target,
        },
        deadline_at=NOW,
        payload_digest="9" * 64,
        status=CommandStatus.SUCCEEDED,
        result_evidence_id=37,
    )
    result = InboundEvidence(
        id=37,
        kind=InboundEvidenceKind.DEVICE_RESULT,
        source_identity="RESULT-37",
        payload_digest="a" * 64,
        normalized_payload={
            "command_code": command_code,
            "device_code": "DEVICE-3",
            "contract_key": "rough_sorter.placement_device",
            "contract_version": "1.0",
            "result": "SUCCESS",
            "finish_time": 1_787_040_000_600,
            "source_event_id": "RESULT-37",
            "data": {"material_trace_id": "TRACE-21", "actual_position": target},
            "error_detail": None,
        },
        received_at=NOW,
        line_run_epoch_id=11,
        material_execution_id=21,
        device_code="DEVICE-3",
        command_code=command_code,
        contract_key="rough_sorter.placement_device",
        contract_version="1.0",
        apply_status=InboundEvidenceApplyStatus.APPLIED,
    )
    admission_evidence = InboundEvidence(
        id=32,
        kind=InboundEvidenceKind.WMS_RESULT,
        source_identity=f"inbound.material.admission_decide@v1:{ADMISSION_OPERATION_ID}",
        payload_digest="b" * 64,
        normalized_payload={
            "operation_id": ADMISSION_OPERATION_ID,
            "code": "DECIDED",
            "timestamp": 1,
            "data": {"result": "ACCEPT", "pkg_id": "PKG-1", "inbound_admission_id": "ADM-1"},
        },
        received_at=NOW,
        line_run_epoch_id=11,
        material_execution_id=21,
        contract_key="rough_sorter_inbound",
        contract_version="1.0",
        operation="inbound.material.admission_decide@v1",
        operation_id=ADMISSION_OPERATION_ID,
        apply_status=InboundEvidenceApplyStatus.APPLIED,
    )
    target_evidence = InboundEvidence(
        id=33,
        kind=InboundEvidenceKind.WMS_RESULT,
        source_identity=f"inbound.material.target_decide@v1:{TARGET_OPERATION_ID}",
        payload_digest="c" * 64,
        normalized_payload={
            "operation_id": TARGET_OPERATION_ID,
            "code": "DECIDED",
            "timestamp": 2,
            "data": {
                "result": "ASSIGNED",
                "target_assignment_id": "ASSIGN-1",
                "target_position": {
                    "type": "ONE_LAYER_BIN_CELL",
                    "rack_id": "RACK-1",
                    "rack_slot_code": "SLOT-1",
                    "bin_id": "BIN-1",
                    "bin_cell_id": "CELL-1",
                },
                "placement_sequence": 4,
                "expected_height_mm": "3.2",
            },
        },
        received_at=NOW,
        line_run_epoch_id=11,
        material_execution_id=21,
        contract_key="rough_sorter_inbound",
        contract_version="1.0",
        operation="inbound.material.target_decide@v1",
        operation_id=TARGET_OPERATION_ID,
        apply_status=InboundEvidenceApplyStatus.APPLIED,
    )

    def completed(identity: int, evidence: InboundEvidence) -> WmsConfirmation:
        return WmsConfirmation(
            id=identity,
            operation=evidence.operation or "",
            operation_id=evidence.operation_id or "",
            material_execution_id=21,
            request_digest="d" * 64,
            request_payload={
                "operation": evidence.operation,
                "operation_id": evidence.operation_id,
                "timestamp": 1,
                "data": {},
            },
            deadline_at=NOW,
            status=WmsConfirmationStatus.COMPLETED,
            response_evidence_id=evidence.id,
            response_result=evidence.normalized_payload["data"]["result"],
            completed_at=NOW,
        )

    factory._evidences = _Evidences(result, target_evidence, admission_evidence)  # type: ignore[attr-defined]
    factory._commands = _Commands(command)  # type: ignore[attr-defined]
    factory._wms_confirmations = _Confirmations(  # type: ignore[attr-defined]
        completed(51, admission_evidence), completed(52, target_evidence)
    )
    base = DeviceResultReadyFact("evidence:37", "37", "1.0", "EXEC-21", command_code, "DEVICE-3", "TRACE-21")

    fact = await factory.build(object(), base)
    resolved = await RoughSorterWmsConfirmationRequestResolver(
        fact_factory=factory,
        evidence_repository=factory._evidences,  # type: ignore[attr-defined]
        execution_repository=factory._executions,  # type: ignore[attr-defined]
    ).resolve(
        object(),
        CreateWmsConfirmation(
            material_execution_id="EXEC-21",
            fact_id=base.fact_id,
            operation="inbound.material.placement_report@v1",
            operation_id=command_code,
            evidence_refs=(base.evidence_id,),
            snapshot_refs=("execution:EXEC-21", "command:" + command_code),
        ),
    )

    assert fact.step.value == "PLACEMENT_TO_CELL"
    assert resolved.request_payload["data"]["target_assignment_id"] == "ASSIGN-1"
    assert resolved.request_payload["data"]["placement_sequence"] == 4
    assert resolved.request_payload["data"]["placed_at"] == 1_787_040_000_600


@pytest.mark.asyncio
async def test_factory_rebuilds_ng_callback_from_rejected_causal_response() -> None:
    factory, _ = _factory()
    command_code = "019d0000-0000-7000-8000-000000000039"
    source_position = {
        "location_id": "MEASUREMENT-1",
        "location_type": "MEASUREMENT_POSITION",
        "material_trace_id": "TRACE-21",
        "rack_id": None,
        "rack_slot_code": None,
        "bin_id": None,
        "bin_cell_id": None,
    }
    target_position = {
        "location_id": "NG-1",
        "location_type": "NG_POSITION",
        "material_trace_id": "TRACE-21",
        "rack_id": None,
        "rack_slot_code": None,
        "bin_id": None,
        "bin_cell_id": None,
    }
    command = DeviceCommand(
        id=64,
        command_code=command_code,
        device_code="DEVICE-1",
        device_binding_id=1,
        line_run_epoch_id=11,
        execution_ref_type="PLUGIN_DECISION",
        execution_ref_id="evidence:32:execution:21:CREATE_DEVICE_COMMAND:0",
        material_execution_id=21,
        contract_key="rough_sorter.measurement_device",
        contract_version="1.0",
        task_type="PICK_AND_PUT",
        params={"material_trace_id": "TRACE-21", "source": source_position, "target": target_position},
        deadline_at=NOW,
        payload_digest="3" * 64,
        status=CommandStatus.SUCCEEDED,
        result_evidence_id=39,
    )
    result = InboundEvidence(
        id=39,
        kind=InboundEvidenceKind.DEVICE_RESULT,
        source_identity="RESULT-39",
        payload_digest="4" * 64,
        normalized_payload={
            "command_code": command_code,
            "device_code": "DEVICE-1",
            "contract_key": "rough_sorter.measurement_device",
            "contract_version": "1.0",
            "result": "SUCCESS",
            "finish_time": 1_787_040_000_800,
            "source_event_id": "RESULT-39",
            "data": {"material_trace_id": "TRACE-21", "actual_position": target_position},
            "error_detail": None,
        },
        received_at=NOW,
        line_run_epoch_id=11,
        material_execution_id=21,
        device_code="DEVICE-1",
        command_code=command_code,
        contract_key="rough_sorter.measurement_device",
        contract_version="1.0",
        apply_status=InboundEvidenceApplyStatus.APPLIED,
    )
    rejected = InboundEvidence(
        id=32,
        kind=InboundEvidenceKind.WMS_RESULT,
        source_identity=f"inbound.material.admission_decide@v1:{ADMISSION_OPERATION_ID}",
        payload_digest="5" * 64,
        normalized_payload={
            "operation_id": ADMISSION_OPERATION_ID,
            "code": "DECIDED",
            "timestamp": 1,
            "data": {
                "result": "REJECT",
                "reason_code": "MATERIAL_REJECTED",
                "ng_destination": {"type": "NG_POSITION", "location_code": "NG-1"},
            },
        },
        received_at=NOW,
        line_run_epoch_id=11,
        material_execution_id=21,
        contract_key="rough_sorter_inbound",
        contract_version="1.0",
        operation="inbound.material.admission_decide@v1",
        operation_id=ADMISSION_OPERATION_ID,
        apply_status=InboundEvidenceApplyStatus.APPLIED,
    )
    factory._evidences = _Evidences(result, rejected)  # type: ignore[attr-defined]
    factory._commands = _Commands(command)  # type: ignore[attr-defined]
    base = DeviceResultReadyFact("evidence:39", "39", "1.0", "EXEC-21", command_code, "DEVICE-1", "TRACE-21")

    fact = await factory.build(object(), base)
    resolved = await RoughSorterWmsConfirmationRequestResolver(
        fact_factory=factory,
        evidence_repository=factory._evidences,  # type: ignore[attr-defined]
        execution_repository=factory._executions,  # type: ignore[attr-defined]
    ).resolve(
        object(),
        CreateWmsConfirmation(
            material_execution_id="EXEC-21",
            fact_id=base.fact_id,
            operation="inbound.material.ng_placement_report@v1",
            operation_id=command_code,
            evidence_refs=(base.evidence_id,),
            snapshot_refs=("execution:EXEC-21", "command:" + command_code),
        ),
    )

    assert fact.step.value == "MEASUREMENT_TO_NG"
    assert resolved.request_payload["data"]["ng_position"] == {"type": "NG_POSITION", "location_code": "NG-1"}
    assert resolved.request_payload["data"]["reason_code"] == "MATERIAL_REJECTED"


def test_core_application_does_not_import_concrete_rough_sorter_plugin() -> None:
    import ast

    forbidden = []
    for path in sorted((__import__("pathlib").Path("src/app")).rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                modules = [node.module]
            if any(
                module == "rough_sorter" or module.startswith(("rough_sorter.", "workline_plugins."))
                for module in modules
            ):
                forbidden.append(str(path))
    assert forbidden == []


def test_only_static_composition_root_imports_plugin_without_mutable_registry() -> None:
    import ast
    from pathlib import Path

    direct_importers: list[str] = []
    deployment_sources: dict[str, str] = {}
    for path in sorted(Path("deployment").glob("*.py")):
        source = path.read_text(encoding="utf-8")
        deployment_sources[path.name] = source
        tree = ast.parse(source)
        modules = [
            node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module is not None
        ]
        modules.extend(alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names)
        if any(module == "rough_sorter" or module.startswith("rough_sorter.") for module in modules):
            direct_importers.append(path.name)

    assert direct_importers == ["rough_sorter_composition.py"]
    assert "install_rough_sorter_types" not in "".join(deployment_sources.values())
    assert "get_rough_sorter_types" not in "".join(deployment_sources.values())
    assert "ContextVar" not in "".join(deployment_sources.values())
    assert "class RoughSorterPluginFactFactory" in deployment_sources["_rough_sorter_factory.py"]
    assert "def build_device_fact" in deployment_sources["_rough_sorter_device_facts.py"]
    assert "def build_wms_fact" in deployment_sources["_rough_sorter_wms_facts.py"]
    assert "def build_transport_fact" in deployment_sources["_rough_sorter_transport_recovery_facts.py"]
    assert "def build_recovery_fact" in deployment_sources["_rough_sorter_transport_recovery_facts.py"]


def test_workspace_lock_and_image_explicitly_include_sdk_and_plugin() -> None:
    from pathlib import Path

    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    lock = Path("uv.lock").read_text(encoding="utf-8")
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert 'wes-rough-sorter-plugin = { path = "workline_plugins/rough_sorter" }' in pyproject
    assert 'name = "wes-rough-sorter-plugin"' in lock
    assert 'source = { directory = "workline_plugins/rough_sorter" }' in lock
    assert "COPY packages/wes_plugin_sdk/src packages/wes_plugin_sdk/src" in dockerfile
    assert "COPY workline_plugins/rough_sorter/src workline_plugins/rough_sorter/src" in dockerfile


class _Begin:
    def __init__(self, db: object) -> None:
        self.db = db

    async def __aenter__(self) -> object:
        return self.db

    async def __aexit__(self, *args: object) -> None:
        del args


class _Sessions:
    def __init__(self, db: object) -> None:
        self.db = db

    def begin(self) -> _Begin:
        return _Begin(self.db)


@pytest.mark.asyncio
async def test_initial_correlator_rebuilds_stable_execution_identity_from_persisted_scan() -> None:
    factory, base = _factory()
    evidence = factory._evidences.evidence  # type: ignore[attr-defined]
    correlator = RoughSorterInitialExecutionCorrelator(evidence_repository=_Evidences(evidence))
    db = object()

    first = await correlator.correlate(db, base.evidence_id)
    second = await correlator.correlate(db, base.evidence_id)

    assert first == second
    assert first is not None
    assert first.material_trace_id == "TRACE-21"
    assert first.execution_code.startswith("rough-sorter-")
    assert len(first.execution_code) <= 120


@pytest.mark.asyncio
async def test_wms_resolver_builds_strict_admission_wire_from_same_db_snapshot() -> None:
    factory, base = _factory()
    resolver = RoughSorterWmsConfirmationRequestResolver(
        fact_factory=factory,
        evidence_repository=factory._evidences,  # type: ignore[attr-defined]
        execution_repository=factory._executions,  # type: ignore[attr-defined]
    )
    decision = CreateWmsConfirmation(
        material_execution_id=base.material_execution_id,
        fact_id=base.fact_id,
        operation="inbound.material.admission_decide@v1",
        operation_id=(await factory.build(object(), base)).request_operation_id,
        evidence_refs=(base.evidence_id,),
        snapshot_refs=("execution:EXEC-21", "epoch:11"),
    )
    db = object()

    resolved = await resolver.resolve(db, decision)

    assert resolved.request_payload["operation"] == decision.operation
    assert resolved.request_payload["operation_id"] == decision.operation_id
    assert resolved.request_payload["data"]["six_in_one"] == {
        "LotCode": "LOT",
        "DateCode": "DATE",
        "Qty": "1",
        "ProductNo": "PRODUCT",
        "MfrPN": "MFR",
        "PONumber": "PO",
    }
    assert resolved.request_payload["data"]["source_position"] == {
        "type": "HANDOFF_POSITION",
        "location_code": "MEASUREMENT-1",
    }
    assert resolved.deadline_at > NOW


@pytest.mark.asyncio
async def test_transport_publisher_maps_only_new_in_and_wakes_after_commit() -> None:
    events: list[str] = []

    class Sessions:
        def begin(self) -> object:
            class Transaction:
                async def __aenter__(self) -> object:
                    events.append("begin")
                    return object()

                async def __aexit__(self, *args: object) -> None:
                    del args
                    events.append("commit")

            return Transaction()

    class Bindings:
        async def get_by_client_request_id(self, db: object, client_request_id: str) -> object:
            del db
            events.append("binding-read")
            return SimpleNamespace(
                id=51,
                version=2,
                leg="NEW_IN",
                source_evidence_id=40,
                line_run_epoch_id=11,
                current_rack_id="RACK-1",
                client_request_id=client_request_id,
            )

        async def get_by_client_request_id_for_update(self, db: object, client_request_id: str) -> object:
            del db
            events.append("binding-lock")
            return SimpleNamespace(
                id=51,
                version=2,
                leg="NEW_IN",
                source_evidence_id=40,
                line_run_epoch_id=11,
                current_rack_id="RACK-1",
                client_request_id=client_request_id,
            )

    class EvidenceRepo:
        async def get_by_id_without_lock(self, db: object, evidence_id: int) -> object:
            del db, evidence_id
            events.append("source-read")
            return SimpleNamespace(
                id=40,
                version=3,
                material_execution_id=21,
                line_run_epoch_id=11,
                operation="inbound.source_rack.replacement_plan_decide@v1",
            )

        async def get_by_id_for_update(self, db: object, evidence_id: int) -> object:
            del db, evidence_id
            events.append("source-lock")
            return SimpleNamespace(
                id=40,
                version=3,
                material_execution_id=21,
                line_run_epoch_id=11,
                operation="inbound.source_rack.replacement_plan_decide@v1",
            )

    class ExecutionRepo:
        async def get_by_id_for_update(self, db: object, execution_id: int) -> object:
            del db, execution_id
            events.append("execution-lock")
            return SimpleNamespace(id=21, line_run_epoch_id=11, workline_id=7, status="HOLD")

    class EvidenceService:
        async def accept(self, db: object, **values: object) -> object:
            del db
            events.append("accepted:" + str(values["source_identity"]))
            return SimpleNamespace(evidence=SimpleNamespace(apply_status=InboundEvidenceApplyStatus.APPLIED))

    class Queue:
        def enqueue_execution_facts(self) -> None:
            events.append("wake")

    publisher = RoughSorterTransportOutcomePublisher(
        session_factory=Sessions(),  # type: ignore[arg-type]
        binding_repository=Bindings(),  # type: ignore[arg-type]
        evidence_repository=EvidenceRepo(),  # type: ignore[arg-type]
        execution_repository=ExecutionRepo(),  # type: ignore[arg-type]
        evidence_service=EvidenceService(),  # type: ignore[arg-type]
        queue_gateway=Queue(),  # type: ignore[arg-type]
    )
    outcome = TransportOutcome(
        transport_task_id="TRANSPORT-1",
        client_request_id="019d0000-0000-7000-8000-000000000041",
        outcome_version=1,
        caller=TransportCaller(workline_id="7"),
        status=TransportOutcomeStatus.SUCCEEDED,
        reason_code=None,
        members=(
            TransportMemberOutcome(
                object_id="RACK-2",
                final_position=RackPosition("OUTLET-1"),
                arrival_face=RackFace.B,
            ),
        ),
    )

    await publisher.publish(outcome)

    assert events == [
        "begin",
        "binding-read",
        "source-read",
        "execution-lock",
        "binding-lock",
        "source-lock",
        "accepted:transport:TRANSPORT-1:outcome:1",
        "commit",
        "wake",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["binding", "source"])
async def test_transport_publisher_revalidates_correlation_after_execution_lock(drift: str) -> None:
    events: list[str] = []

    class Sessions:
        def begin(self) -> object:
            class Transaction:
                async def __aenter__(self) -> object:
                    return object()

                async def __aexit__(self, *args: object) -> None:
                    del args

            return Transaction()

    class Bindings:
        async def get_by_client_request_id(self, db: object, client_request_id: str) -> object:
            del db
            return SimpleNamespace(
                id=51,
                version=2,
                leg="NEW_IN",
                source_evidence_id=40,
                line_run_epoch_id=11,
                current_rack_id="RACK-1",
                client_request_id=client_request_id,
            )

        async def get_by_client_request_id_for_update(self, db: object, client_request_id: str) -> object:
            del db
            events.append("binding-lock")
            return SimpleNamespace(
                id=51,
                version=3 if drift == "binding" else 2,
                leg="NEW_IN",
                source_evidence_id=41 if drift == "binding" else 40,
                line_run_epoch_id=11,
                current_rack_id="RACK-1",
                client_request_id=client_request_id,
            )

    class EvidenceRepo:
        async def get_by_id_without_lock(self, db: object, evidence_id: int) -> object:
            del db, evidence_id
            return SimpleNamespace(
                id=40,
                version=3,
                material_execution_id=21,
                line_run_epoch_id=11,
                operation="inbound.source_rack.replacement_plan_decide@v1",
            )

        async def get_by_id_for_update(self, db: object, evidence_id: int) -> object:
            del db, evidence_id
            events.append("source-lock")
            return SimpleNamespace(
                id=40,
                version=4,
                material_execution_id=21,
                line_run_epoch_id=11,
                operation="inbound.source_rack.replacement_plan_decide@v1",
            )

    class ExecutionRepo:
        async def get_by_id_for_update(self, db: object, execution_id: int) -> object:
            del db, execution_id
            events.append("execution-lock")
            return SimpleNamespace(id=21, line_run_epoch_id=11, workline_id=7, status="HOLD")

    class EvidenceService:
        async def accept(self, db: object, **values: object) -> object:
            del db, values
            events.append("accepted")
            return object()

    publisher = RoughSorterTransportOutcomePublisher(
        session_factory=Sessions(),  # type: ignore[arg-type]
        binding_repository=Bindings(),  # type: ignore[arg-type]
        evidence_repository=EvidenceRepo(),  # type: ignore[arg-type]
        execution_repository=ExecutionRepo(),  # type: ignore[arg-type]
        evidence_service=EvidenceService(),  # type: ignore[arg-type]
    )
    outcome = TransportOutcome(
        transport_task_id="TRANSPORT-1",
        client_request_id="019d0000-0000-7000-8000-000000000041",
        outcome_version=1,
        caller=TransportCaller(workline_id="7"),
        status=TransportOutcomeStatus.SUCCEEDED,
        reason_code=None,
        members=(
            TransportMemberOutcome(
                object_id="RACK-2",
                final_position=RackPosition("OUTLET-1"),
                arrival_face=RackFace.B,
            ),
        ),
    )

    with pytest.raises(ValueError, match=f"Transport {drift}.*drift"):
        await publisher.publish(outcome)

    expected_events = ["execution-lock", "binding-lock"]
    if drift == "source":
        expected_events.append("source-lock")
    assert events == expected_events


@pytest.mark.asyncio
async def test_factory_builds_recovery_wms_continuation_from_verified_causal_evidence() -> None:
    factory, _ = _factory()
    factory._executions.execution.status = MaterialExecutionStatus.RECONCILING  # type: ignore[attr-defined]
    recovery_operation_id = "019d0000-0000-7000-8000-000000000042"
    recovery = InboundEvidence(
        id=42,
        kind=InboundEvidenceKind.WMS_EVENT,
        source_identity=f"inbound.execution.recovery_decided@v1:{recovery_operation_id}",
        payload_digest="1" * 64,
        normalized_payload={
            "operation_id": recovery_operation_id,
            "code": "RECORDED",
            "timestamp": 1_787_040_000_700,
            "data": {
                "material_execution_id": "EXEC-21",
                "material_trace_id": "TRACE-21",
                "recovery_id": "RECOVERY-1",
                "reconciling_evidence_id": "32",
                "decision": "CONTINUE",
                "authoritative_position": {"type": "MEASUREMENT_POSITION", "location_code": "MEASUREMENT-1"},
                "reason_code": "OPERATOR_VERIFIED",
            },
        },
        received_at=NOW,
        line_run_epoch_id=11,
        material_execution_id=21,
        contract_key="rough_sorter_inbound",
        contract_version="1.0",
        operation="inbound.execution.recovery_decided@v1",
        operation_id=recovery_operation_id,
        apply_status=InboundEvidenceApplyStatus.APPLIED,
    )
    causal = InboundEvidence(
        id=32,
        kind=InboundEvidenceKind.WMS_RESULT,
        source_identity=f"inbound.material.admission_decide@v1:{ADMISSION_OPERATION_ID}",
        payload_digest="2" * 64,
        normalized_payload={"operation_id": ADMISSION_OPERATION_ID, "code": "RECONCILING", "data": {}},
        received_at=NOW,
        line_run_epoch_id=11,
        material_execution_id=21,
        contract_key="rough_sorter_inbound",
        contract_version="1.0",
        operation="inbound.material.admission_decide@v1",
        operation_id=ADMISSION_OPERATION_ID,
        apply_status=InboundEvidenceApplyStatus.APPLIED,
    )
    factory._evidences = _Evidences(recovery, causal)  # type: ignore[attr-defined]
    position = DevicePosition("MEASUREMENT-1", "MEASUREMENT_POSITION", "TRACE-21")
    base = BaseRecoveryDecidedFact(
        "evidence:42",
        "42",
        "1.0",
        "EXEC-21",
        "RECOVERY-1",
        RecoveryDecision.CONTINUE,
        position,
        "OPERATOR_VERIFIED",
    )

    first = await factory.build(object(), base)
    second = await factory.build(object(), base)

    assert first.continuation == second.continuation
    assert first.reconciling_evidence_id == "32"
    assert first.continuation.operation == "inbound.material.admission_decide@v1"
    assert first.continuation.operation_id != ADMISSION_OPERATION_ID
    assert is_uuid7(first.continuation.operation_id)
