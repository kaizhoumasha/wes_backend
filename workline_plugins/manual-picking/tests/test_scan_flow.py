"""点位 handler 的决定由插件应用层冻结为本次经过和原身份义务。"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import wes_plugin_sdk as sdk
from manual_picking.application.passage_model import ManualPickingPassage
from manual_picking.application.scan_flow import ManualPickingScanFlow

from src.app.device.contracts import EcsDeviceEvent
from src.app.execution.models import InboundEvidence, InboundEvidenceApplyStatus, InboundEvidenceKind
from src.app.execution.plugin_binding import BusinessEvidenceDisposition

NOW = datetime(2026, 9, 13, 12)


def _scan(evidence_id: int, device_code: str, bin_code: str) -> InboundEvidence:
    payload = EcsDeviceEvent(
        device_code=device_code,
        contract_key="third_party_integration",
        contract_version="1.1",
        event_type="SCAN_COMPLETED",
        timestamp=1_788_389_900_000 + evidence_id,
        source_event_id=f"EVENT-{evidence_id}",
        is_debug=False,
        data={"bin_code": bin_code},
    )
    return InboundEvidence(
        id=evidence_id,
        kind=InboundEvidenceKind.DEVICE_EVENT,
        source_identity=payload.source_event_id,
        payload_digest="a" * 64,
        normalized_payload=payload.model_dump(mode="json", exclude_unset=True),
        received_at=NOW,
        workline_id=7,
        device_code=device_code,
        apply_status=InboundEvidenceApplyStatus.APPLIED,
    )


class _Evidence:
    def __init__(self, *rows: InboundEvidence) -> None:
        self.rows = {row.id: row for row in rows}

    async def get_by_id_for_update(self, db, evidence_id):  # type: ignore[no-untyped-def]
        return self.rows.get(evidence_id)


class _WorkLines:
    def __init__(self) -> None:
        self.line = SimpleNamespace(
            id=7,
            line_code="LINE-1",
            is_active=True,
            plugin_key="manual-picking",
            plugin_version="0.1.0",
            config={
                "device_bindings": {"SCAN1": "S1", "SCAN2": "S2", "SCAN3": "S3", "SCAN4": "S4"},
                "position_bindings": {
                    "FIVE_RACK": "FIVE-RACK-POSITION",
                    "RETURN_RACK": "RETURN-RACK-POSITION",
                    "TRANSFER_RACK": "TRANSFER-RACK-POSITION",
                    "INLET": "INLET-POSITION",
                    "OUTLET": "OUTLET-POSITION",
                },
            },
        )

        self.line.position_bindings = {
            role: {"location_id": location} for role, location in self.line.config["position_bindings"].items()
        }

    async def get_for_authority_update(self, db, workline_id):  # type: ignore[no-untyped-def]
        return self.line if workline_id == 7 else None

    async def get_binding_for_command_creation(self, db, *, workline_id, device_code):  # type: ignore[no-untyped-def]
        return SimpleNamespace(command_timeout_ms=30000, contract_key="third_party_integration", contract_version="1.1")


class _Tasks:
    async def get_executing_for_workline_for_update(self, db, workline_id):  # type: ignore[no-untyped-def]
        return SimpleNamespace(
            id=31,
            task_id="PICK-001",
            workline_id=7,
            status="EXECUTING",
            target_rack_id="TRANSFER-1",
            target_rack_face="A",
        )

    async def get_by_task_id_for_update(self, db, task_id):  # type: ignore[no-untyped-def]
        assert task_id == "PICK-001"
        return await self.get_executing_for_workline_for_update(db, 7)


class _SourceRacks:
    async def list_bin_source_racks(self, db, task_id):  # type: ignore[no-untyped-def]
        assert task_id == 31
        return [SimpleNamespace(plan_revision=1, rack_id="RACK-1", rack_face="90", cancelled_evidence_id=None)]

    async def has_applied_source_face(self, db, workline_id, rack_id, rack_face):  # type: ignore[no-untyped-def]
        return (workline_id, rack_id, rack_face) == (7, "RACK-1", "90")

    async def has_source_face(self, db, workline_id, rack_id, rack_face):  # type: ignore[no-untyped-def]
        return (workline_id, rack_id, rack_face) == (7, "RACK-1", "90")


class _Projections:
    def __init__(self, missing=None):  # type: ignore[no-untyped-def]
        self.missing = missing
        self.bin_projections = {}

    async def get(self, db, object_type, object_id, *, for_update=False):  # type: ignore[no-untyped-def]
        if (object_type, object_id) == self.missing:
            return None
        if object_type == "BIN":
            return self.bin_projections.setdefault(
                object_id,
                SimpleNamespace(
                    workline_id=7,
                    position_unknown=False,
                    position_json={"kind": "HANDOFF_POSITION", "location_code": "INLET-POSITION"},
                    arrival_face=None,
                    source_transport_task_id=f"MOVE-{object_id}",
                ),
            )
        face = "A" if object_id == "TRANSFER-1" else "90"
        location = "TRANSFER-RACK-POSITION" if object_id == "TRANSFER-1" else "FIVE-RACK-POSITION"
        return SimpleNamespace(
            workline_id=7,
            position_unknown=False,
            position_json={"kind": "RACK_POSITION", "location_code": location},
            arrival_face=face,
            source_transport_task_id=f"MOVE-{object_id}",
        )

    async def get_workline_for_update(self, db, workline_id):  # type: ignore[no-untyped-def]
        return SimpleNamespace(id=workline_id) if workline_id == 7 else None

    async def flush(self, db):  # type: ignore[no-untyped-def]
        return None

    async def get_for_update(self, db, object_type, object_id):  # type: ignore[no-untyped-def]
        return await self.get(db, object_type, object_id, for_update=True)

    async def lock_object_authority(self, db, object_type, object_id):  # type: ignore[no-untyped-def]
        return None


class _Transports:
    def __init__(self, failed=None):  # type: ignore[no-untyped-def]
        self.failed = failed

    async def get_task(self, db, transport_task_id, *, for_update=False):  # type: ignore[no-untyped-def]
        return SimpleNamespace(
            transport_task_id=transport_task_id,
            kind="BIN_MOVE" if transport_task_id.startswith("MOVE-A") else "RACK_MOVE",
            status="FAILED" if transport_task_id == self.failed else "SUCCEEDED",
            request_json={
                "moves": [
                    {
                        "bin_code": transport_task_id.removeprefix("MOVE-"),
                        "source": {"kind": "RACK_BIN_SLOT", "rack_id": "RACK-1", "rack_face": "90"},
                    }
                ]
            }
            if transport_task_id.startswith("MOVE-A")
            else {},
        )


class _Passages:
    def __init__(self, evidences: _Evidence) -> None:
        self.rows = []
        self.evidences = evidences

    async def add(self, db, passage):  # type: ignore[no-untyped-def]
        self.rows.append(passage)
        passage.id = len(self.rows)
        return passage

    async def scan2_head_for_update(self, db, workline_id):  # type: ignore[no-untyped-def]
        return next((row for row in self.rows if row.scan2_evidence_id is None and row.disposition == "OPEN"), None)

    async def scan2_in_flight_for_update(self, db, workline_id):  # type: ignore[no-untyped-def]
        return tuple(
            row
            for row in self.rows
            if row.workline_id == workline_id
            and row.scan2_evidence_id is not None
            and row.scan3_evidence_id is None
            and row.disposition != "CLOSED"
        )

    async def scan1_unclosed_for_update(self, db, workline_id):  # type: ignore[no-untyped-def]
        return tuple(row for row in self.rows if row.workline_id == workline_id and row.disposition != "CLOSED")

    async def by_admission_operation_for_update(self, db, operation_id):  # type: ignore[no-untyped-def]
        return next((row for row in self.rows if row.admission_operation_id == operation_id), None)

    async def unique_open_bin_for_update(self, db, *, workline_id, bin_code, after_scan2=False):  # type: ignore[no-untyped-def]
        rows = [
            row
            for row in self.rows
            if row.workline_id == workline_id
            and row.bin_code == bin_code
            and row.disposition != "CLOSED"
            and (after_scan2 is None or (row.scan3_evidence_id is not None) == after_scan2)
        ]
        return rows[0] if len(rows) == 1 else None

    async def by_command_code_for_update(self, db, command_code):  # type: ignore[no-untyped-def]
        return next(
            (
                row
                for row in self.rows
                if row.disposition != "CLOSED"
                and command_code
                in (
                    row.scan1_command_code,
                    row.scan2_command_code,
                    row.scan2_fault_command_code,
                )
            ),
            None,
        )


class _Returns:
    def __init__(self) -> None:
        self.rows = []

    async def add(self, db, row):  # type: ignore[no-untyped-def]
        self.rows.append(row)
        row.id = len(self.rows)
        return row

    async def current_bin_for_update(self, db, workline_id, bin_code):  # type: ignore[no-untyped-def]
        return next(
            (
                row
                for row in self.rows
                if row.workline_id == workline_id
                and row.bin_code == bin_code
                and row.return_state not in {"EXITED", "VOIDED"}
            ),
            None,
        )

    async def by_command_code_for_update(self, db, command_code):  # type: ignore[no-untyped-def]
        return next(
            (row for row in self.rows if command_code in (row.scan3_command_code, row.scan4_command_code)),
            None,
        )


class _BatchProgress:
    def __init__(self) -> None:
        self.scanned = set()

    async def record_scan1(self, db, **kwargs):  # type: ignore[no-untyped-def]
        bin_code = kwargs["bin_code"]
        if bin_code in self.scanned:
            return False
        self.scanned.add(bin_code)
        return True


class _Commands:
    def __init__(self) -> None:
        self.requests = []
        self.statuses = {}

    async def create_command_in_session(self, db, request):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        return SimpleNamespace(command_code=f"COMMAND-{len(self.requests)}")

    async def get_by_command_code(self, db, command_code, *, for_update=False):  # type: ignore[no-untyped-def]
        request = self.requests[int(command_code.removeprefix("COMMAND-")) - 1]
        return SimpleNamespace(
            status=self.statuses.get(command_code, "SUCCEEDED"),
            device_code=request.device_code,
            workline_id=request.workline_id,
            execution_ref_type=request.execution_ref_type,
            execution_ref_id=request.execution_ref_id,
            task_type=request.task_type,
        )

    async def has_unclosed_for_device_for_update(self, db, *, workline_id, device_code):  # type: ignore[no-untyped-def]
        return any(
            request.workline_id == workline_id
            and request.device_code == device_code
            and self.statuses.get(f"COMMAND-{index}", "SUCCEEDED") in {"PENDING", "DISPATCHING", "ACKNOWLEDGED"}
            for index, request in enumerate(self.requests, start=1)
        )


class _Admissions:
    def __init__(self) -> None:
        self.intents = []
        self.options = []

    async def create_in_session(self, db, intent, **kwargs):  # type: ignore[no-untyped-def]
        self.intents.append(intent)
        self.options.append(kwargs)


class _WmsReader:
    def decode_admission_outcome(self, payload):  # type: ignore[no-untyped-def]
        return sdk.ManualBinAdmissionOutcome(
            operation_id=payload["operation_id"],
            result=payload["data"]["result"],
            task_id=payload["data"].get("task_id"),
            retry_after_ms=payload["data"].get("retry_after_ms"),
        )

    def decode_completed_fact(self, payload):  # type: ignore[no-untyped-def]
        data = payload["data"]
        return sdk.ManualBinCompletedFact(**data)


def _setup(
    *,
    transport_reader=None,
    missing_projection=None,
    failed_transport=None,
    batch_reader=None,
    batch_result=None,
    returns=None,
):  # type: ignore[no-untyped-def]
    evidences = _Evidence(_scan(1, "S1", "A000000001-B"), _scan(2, "S2", "A000000001-C"))
    passages = _Passages(evidences)
    commands = _Commands()
    admissions = _Admissions()
    flow = ManualPickingScanFlow(
        evidences=evidences,
        worklines=_WorkLines(),
        tasks=_Tasks(),
        passages=passages,
        returns=returns or _Returns(),
        batch_progress=_BatchProgress(),
        commands=commands,
        command_reader=commands,
        admissions=admissions,
        wms_reader=_WmsReader(),
        transport_reader=transport_reader or _Transports(failed_transport),
        source_racks=_SourceRacks(),
        position_reader=_Projections(missing_projection),
        batch_reader=batch_reader,
        batch_result=batch_result,
    )
    return flow, evidences, passages, commands, admissions


@pytest.mark.asyncio
@pytest.mark.parametrize("different_position_codes", [False, True])
async def test_inbound_batch_result_routes_to_batch_flow_only_after_rack_position_is_confirmed(
    different_position_codes,
) -> None:
    intent = sdk.wms_operations.outbound_bin_inbound_batch(
        operation_id="batch-1", task_id="PICK-001", plan_revision=1, rack_id="RACK-1", rack_face="90"
    )
    reader = SimpleNamespace(read_inbound=AsyncMock(return_value=(intent, object())))
    result = SimpleNamespace(apply_inbound_in_session=AsyncMock(return_value="INBOUND_READY"))
    flow, evidences, _, _, _ = _setup(batch_reader=reader, batch_result=result)
    if different_position_codes:
        flow._worklines.line.config["position_bindings"] = {
            role: f"CONFIG-{role}" for role in flow._worklines.line.position_bindings
        }
    evidence = _wms(30, InboundEvidenceKind.WMS_RESULT, "batch-1", {})
    evidence.operation = "outbound.bin.inbound_batch@v1"
    evidences.rows[30] = evidence

    applied = await flow.apply_in_session(object(), 30, workline_id=7)

    assert applied.disposition is BusinessEvidenceDisposition.APPLIED
    assert result.apply_inbound_in_session.await_args.kwargs["picking_task_id"] == 31
    assert result.apply_inbound_in_session.await_args.kwargs["confirmed_rack_id"] == "RACK-1"
    assert result.apply_inbound_in_session.await_args.kwargs["inlet_location"] == "INLET-POSITION"


@pytest.mark.asyncio
async def test_inbound_batch_result_starts_bin_transport_before_target_rack_arrives() -> None:
    intent = sdk.wms_operations.outbound_bin_inbound_batch(
        operation_id="batch-1", task_id="PICK-001", plan_revision=1, rack_id="RACK-1", rack_face="90"
    )
    reader = SimpleNamespace(read_inbound=AsyncMock(return_value=(intent, object())))
    result = SimpleNamespace(apply_inbound_in_session=AsyncMock(return_value="INBOUND_READY"))
    flow, evidences, _, _, _ = _setup(
        missing_projection=("RACK", "TRANSFER-1"), batch_reader=reader, batch_result=result
    )
    evidence = _wms(30, InboundEvidenceKind.WMS_RESULT, "batch-1", {})
    evidence.operation = "outbound.bin.inbound_batch@v1"
    evidences.rows[30] = evidence

    applied = await flow.apply_in_session(object(), 30, workline_id=7)

    assert applied.disposition is BusinessEvidenceDisposition.APPLIED
    result.apply_inbound_in_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_late_inbound_ready_uses_active_member_after_parent_completion() -> None:
    intent = sdk.wms_operations.outbound_bin_inbound_batch(
        operation_id="batch-late", task_id="PICK-001", plan_revision=1, rack_id="RACK-1", rack_face="90"
    )
    reader = SimpleNamespace(read_inbound=AsyncMock(return_value=(intent, object())))
    result = SimpleNamespace(apply_inbound_in_session=AsyncMock(return_value="INBOUND_READY"))
    flow, evidences, *_ = _setup(batch_reader=reader, batch_result=result)
    flow._tasks.get_executing_for_workline_for_update = AsyncMock(return_value=None)
    flow._tasks.get_by_task_id_for_update = AsyncMock(
        return_value=SimpleNamespace(id=31, task_id="PICK-001", workline_id=7, status="EXECUTION_COMPLETED")
    )
    evidence = _wms(34, InboundEvidenceKind.WMS_RESULT, "batch-late", {})
    evidence.operation = "outbound.bin.inbound_batch@v1"
    evidences.rows[34] = evidence

    assert (await flow.apply_in_session(object(), 34, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    result.apply_inbound_in_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_late_inbound_ready_after_member_cancellation_creates_no_bin_move() -> None:
    intent = sdk.wms_operations.outbound_bin_inbound_batch(
        operation_id="batch-cancelled", task_id="PICK-001", plan_revision=1, rack_id="RACK-1", rack_face="90"
    )
    reader = SimpleNamespace(read_inbound=AsyncMock(return_value=(intent, object())))
    result = SimpleNamespace(apply_inbound_in_session=AsyncMock(return_value="INBOUND_READY"))
    flow, evidences, *_ = _setup(batch_reader=reader, batch_result=result, missing_projection=("RACK", "RACK-1"))
    flow._source_racks.list_bin_source_racks = AsyncMock(
        return_value=[SimpleNamespace(plan_revision=1, rack_id="RACK-1", rack_face="90", cancelled_evidence_id=45)]
    )
    evidence = _wms(35, InboundEvidenceKind.WMS_RESULT, "batch-cancelled", {})
    evidence.operation = "outbound.bin.inbound_batch@v1"
    evidences.rows[35] = evidence

    assert (await flow.apply_in_session(object(), 35, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    reader.read_inbound.assert_awaited_once()
    result.apply_inbound_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_return_batch_result_uses_confirmed_source_and_does_not_pass_when_rack_position_is_missing() -> None:
    intent = sdk.wms_operations.outbound_bin_return_batch(
        operation_id="batch-2",
        workline_code="LINE-1",
        rack_id="RACK-1",
        rack_face="90",
        return_candidates=(sdk.BinReturnCandidate(1, "A000000001", "OUTLET-POSITION"),),
    )
    reader = SimpleNamespace(read_return=AsyncMock(return_value=(intent, object())))
    result = SimpleNamespace(apply_return_in_session=AsyncMock(return_value="RETURN_READY"))
    flow, evidences, _, _, _ = _setup(batch_reader=reader, batch_result=result)
    evidence = _wms(31, InboundEvidenceKind.WMS_RESULT, "batch-2", {})
    evidence.operation = "outbound.bin.return_batch@v1"
    evidences.rows[31] = evidence

    applied = await flow.apply_in_session(object(), 31, workline_id=7)
    assert applied.disposition is BusinessEvidenceDisposition.APPLIED
    assert result.apply_return_in_session.await_args.kwargs["return_location"] == "OUTLET-POSITION"

    blocked, blocked_evidences, _, _, _ = _setup(
        batch_reader=reader, batch_result=result, missing_projection=("RACK", "RACK-1")
    )
    blocked_evidences.rows[31] = evidence
    applied = await blocked.apply_in_session(object(), 31, workline_id=7)
    assert applied.disposition is BusinessEvidenceDisposition.DEFERRED
    assert result.apply_return_in_session.await_count == 1


@pytest.mark.asyncio
async def test_return_batch_result_accepts_a_cancelled_source_member_at_the_rack() -> None:
    intent = sdk.wms_operations.outbound_bin_return_batch(
        operation_id="batch-cancelled",
        workline_code="LINE-1",
        rack_id="RACK-1",
        rack_face="90",
        return_candidates=(sdk.BinReturnCandidate(1, "A000000001", "OUTLET-POSITION"),),
    )
    reader = SimpleNamespace(read_return=AsyncMock(return_value=(intent, object())))
    result = SimpleNamespace(apply_return_in_session=AsyncMock(return_value="RETURN_READY"))
    flow, evidences, *_ = _setup(batch_reader=reader, batch_result=result)
    flow._source_racks.has_applied_source_face = AsyncMock(return_value=False)
    evidence = _wms(33, InboundEvidenceKind.WMS_RESULT, "batch-cancelled", {})
    evidence.operation = "outbound.bin.return_batch@v1"
    evidences.rows[33] = evidence

    applied = await flow.apply_in_session(object(), 33, workline_id=7)

    assert applied.disposition is BusinessEvidenceDisposition.APPLIED
    result.apply_return_in_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_return_decision_still_applies_for_original_completed_task() -> None:
    intent = sdk.wms_operations.outbound_bin_return_batch(
        operation_id="batch-after-completion",
        workline_code="LINE-1",
        rack_id="RACK-1",
        rack_face="90",
        return_candidates=(sdk.BinReturnCandidate(1, "A000000001", "OUTLET-POSITION"),),
    )
    reader = SimpleNamespace(read_return=AsyncMock(return_value=(intent, object())))
    result = SimpleNamespace(apply_return_in_session=AsyncMock(return_value="RETURN_READY"))
    flow, evidences, _, _, _ = _setup(batch_reader=reader, batch_result=result)
    evidence = _wms(32, InboundEvidenceKind.WMS_RESULT, "batch-after-completion", {})
    evidence.operation = "outbound.bin.return_batch@v1"
    evidences.rows[32] = evidence
    flow._tasks = SimpleNamespace(
        get_executing_for_workline_for_update=AsyncMock(
            return_value=SimpleNamespace(id=99, task_id="PICK-NEW", workline_id=7, status="EXECUTING")
        )
    )
    flow._passages = SimpleNamespace(
        unfinished_return_prefix_for_update=AsyncMock(
            return_value=(SimpleNamespace(task_id="PICK-001", bin_code="A000000001", return_state="READY"),)
        )
    )
    flow._positions = _Projections(("RACK", "TRANSFER-1"))

    applied = await flow.apply_in_session(object(), 32, workline_id=7)

    assert applied.disposition is BusinessEvidenceDisposition.APPLIED
    assert result.apply_return_in_session.await_count == 1


@pytest.mark.asyncio
async def test_return_transport_final_result_does_not_close_fifo_without_source_picked() -> None:
    rows = [
        SimpleNamespace(bin_code="A000000001", return_state="RETURN_REQUESTED", disposition="NORMAL"),
        SimpleNamespace(bin_code="A000000002", return_state="RETURN_REQUESTED", disposition="NORMAL"),
        SimpleNamespace(bin_code="A000000003", return_state="READY", disposition="NORMAL"),
    ]
    passages = SimpleNamespace(unfinished_return_prefix_for_update=AsyncMock(return_value=tuple(rows)))
    task = SimpleNamespace(
        client_request_id="request-1",
        kind="BIN_MOVE",
        request_json={
            "moves": [
                {
                    "bin_code": row.bin_code,
                    "target": {"kind": "RACK_BIN_SLOT", "rack_id": "R1", "rack_face": "90", "slot_id": str(index)},
                }
                for index, row in enumerate(rows[:2], 1)
            ]
        },
    )
    transport_reader = SimpleNamespace(get_task=AsyncMock(return_value=task))
    flow = ManualPickingScanFlow(
        commands=object(), admissions=object(), passages=passages, transport_reader=transport_reader
    )
    payload = {
        "transport_task_id": "move-1",
        "client_request_id": "request-1",
        "caller": {"workline_id": "7"},
        "step": "MANUAL_PICKING_RETURN_BATCH",
        "batch_operation_id": "batch-1",
        "status": "SUCCEEDED",
        "members": [
            {"object_id": move["bin_code"], "final_position": move["target"]} for move in task.request_json["moves"]
        ],
    }
    evidence = SimpleNamespace(transport_task_id="move-1", normalized_payload=payload)

    assert await flow._apply_transport_result(object(), evidence, 7) == "SUCCEEDED"
    assert [row.return_state for row in rows] == ["RETURN_REQUESTED", "RETURN_REQUESTED", "READY"]


def _wms(evidence_id: int, kind: InboundEvidenceKind, operation_id: str, data: dict) -> InboundEvidence:
    operation = (
        "outbound.manual_bin.work_admission_decide@v1"
        if kind is InboundEvidenceKind.WMS_RESULT
        else "outbound.manual_bin.work_completed@v1"
    )
    return InboundEvidence(
        id=evidence_id,
        kind=kind,
        source_identity=f"{operation}:{operation_id}",
        payload_digest="b" * 64,
        normalized_payload={"operation_id": operation_id, "data": data},
        received_at=NOW,
        workline_id=7,
        operation=operation,
        operation_id=operation_id,
        apply_status=InboundEvidenceApplyStatus.APPLIED,
    )


def _result(evidence_id: int, command_code: str, *, device_code: str = "S4") -> InboundEvidence:
    return InboundEvidence(
        id=evidence_id,
        kind=InboundEvidenceKind.DEVICE_RESULT,
        source_identity=f"RESULT-{evidence_id}",
        payload_digest="c" * 64,
        normalized_payload={
            "command_code": command_code,
            "device_code": device_code,
            "contract_key": "third_party_integration",
            "contract_version": "1.1",
            "result": "SUCCESS",
            "finish_time": 1_788_390_000_000,
            "source_event_id": f"RESULT-{evidence_id}",
            "data": {},
            "error_detail": None,
        },
        received_at=NOW,
        workline_id=7,
        device_code=device_code,
        command_code=command_code,
        apply_status=InboundEvidenceApplyStatus.APPLIED,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["SUCCEEDED", "UNKNOWN"])
async def test_transport_outcome_is_consumed_without_material_execution(status: str) -> None:
    transport_task = SimpleNamespace(
        transport_task_id="TRANSPORT-1",
        client_request_id="REQUEST-1",
        request_json={
            "rack_id": "RACK-1",
            "target": {"kind": "RACK_POSITION", "location_code": "FIVE-RACK-POSITION"},
            "target_face": "90",
        },
    )
    transport_reader = SimpleNamespace(get_task=AsyncMock(return_value=transport_task))
    flow, evidences, _, commands, _ = _setup(transport_reader=transport_reader)
    evidences.rows[10] = InboundEvidence(
        id=10,
        kind=InboundEvidenceKind.TRANSPORT_RESULT,
        source_identity="transport:TRANSPORT-1:outcome:1",
        payload_digest="d" * 64,
        normalized_payload={
            "transport_task_id": "TRANSPORT-1",
            "client_request_id": "REQUEST-1",
            "outcome_version": 1,
            "caller": {"workline_id": "7"},
            "status": status,
            "reason_code": None,
            "members": (
                [
                    {
                        "object_id": "RACK-1",
                        "final_position": {"kind": "RACK_POSITION", "location_code": "FIVE-RACK-POSITION"},
                        "position_unknown": False,
                        "failure_code": None,
                        "arrival_face": "90",
                    }
                ]
                if status == "SUCCEEDED"
                else []
            ),
            "picking_task_id": "PICK-001",
            "rack_id": "RACK-1",
            "step": "PICKING_TASK_BIN_SOURCE_RACK_IN",
            "source_evidence_id": 1,
        },
        received_at=NOW,
        workline_id=7,
        transport_task_id="TRANSPORT-1",
        apply_status=InboundEvidenceApplyStatus.APPLIED,
    )

    result = await flow.apply_in_session(object(), 10, workline_id=7)

    assert result.disposition is BusinessEvidenceDisposition.APPLIED
    assert result.decision_digest is not None
    assert commands.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result_status,reason_code",
    [("FAILED", "RCS_TASK_CANCELLED"), ("FAILED", "RCS_EXECUTION_FAILED"), ("REJECTED", None)],
)
@pytest.mark.parametrize(
    "step,target_code,template",
    [
        ("PICKING_TASK_BIN_SOURCE_RACK_IN", "FIVE-RACK-POSITION", "CTU01"),
        ("PICKING_TASK_RETURN_RACK_IN", "RETURN-RACK-POSITION", "F01"),
    ],
)
@pytest.mark.parametrize(
    "member_active,goal_satisfied,expect_retry",
    [
        (True, False, True),
        (False, False, False),
        (True, True, False),
    ],
    ids=["member-active-goal-unmet", "member-cancelled", "goal-satisfied"],
)
async def test_cancelled_rack_follows_business_basis_and_goal_fact(
    result_status: str,
    reason_code: str | None,
    step: str,
    target_code: str,
    template: str,
    member_active: bool,
    goal_satisfied: bool,
    expect_retry: bool,
) -> None:
    transport_task = SimpleNamespace(
        status=result_status,
        kind="RACK_MOVE",
        transport_task_id="TRANSPORT-1",
        client_request_id="REQUEST-1",
        request_json={
            "rack_id": "RACK-1",
            "source": {"kind": "RACK", "location_code": "RACK-1"},
            "target": {"kind": "RACK_POSITION", "location_code": target_code},
            "target_face": "90",
            "rcs_template_id": template,
        },
    )
    creator = SimpleNamespace(create=AsyncMock(), create_windowed_inbound=AsyncMock(return_value="CREATED"))
    binding = SimpleNamespace(
        workline_id=7,
        step=step,
        source_evidence_id=1,
        resource_fence_id="RACK-1",
        picking_task_id=31,
        correlation_id="pt:31:e:1:rack:RACK-1",
    )

    async def get_task(_db, transport_task_id, *, for_update=False):
        if transport_task_id == "MOVE-RACK-1" and goal_satisfied:
            return SimpleNamespace(status="SUCCEEDED")
        return transport_task

    flow, evidences, _, _, _ = _setup(
        transport_reader=SimpleNamespace(get_task=AsyncMock(side_effect=get_task)),
        missing_projection=None if goal_satisfied else ("RACK", "RACK-1"),
    )
    if goal_satisfied and step == "PICKING_TASK_RETURN_RACK_IN":
        flow._positions = SimpleNamespace(
            get=AsyncMock(
                return_value=SimpleNamespace(
                    workline_id=7,
                    position_unknown=False,
                    position_json={"kind": "RACK_POSITION", "location_code": target_code},
                    arrival_face="90",
                    source_transport_task_id="MOVE-RACK-1",
                )
            )
        )
    flow._rack_creator = creator
    flow._transport_bindings = SimpleNamespace(get_by_client_request_id=AsyncMock(return_value=binding))
    flow._tasks = SimpleNamespace(
        get_by_id_for_update=AsyncMock(return_value=SimpleNamespace(id=31, status="EXECUTION_COMPLETED"))
    )
    flow._source_racks = SimpleNamespace(
        list_active_bin_source_racks=AsyncMock(
            return_value=(SimpleNamespace(rack_id="RACK-1", rack_face="90", source_evidence_id=1),)
            if member_active
            else ()
        ),
        list_active_direct_picks=AsyncMock(
            return_value=(SimpleNamespace(rack_id="RACK-1", rack_face="90", source_evidence_id=1),)
            if member_active
            else ()
        ),
    )
    evidence = InboundEvidence(
        id=10,
        kind=InboundEvidenceKind.TRANSPORT_RESULT,
        source_identity="transport:TRANSPORT-1:outcome:1",
        payload_digest="d" * 64,
        normalized_payload={
            "transport_task_id": "TRANSPORT-1",
            "client_request_id": "REQUEST-1",
            "caller": {"workline_id": "7"},
            "status": result_status,
            "reason_code": reason_code,
            "rack_id": "RACK-1",
            "step": binding.step,
            "source_evidence_id": 1,
        },
        received_at=NOW,
        workline_id=7,
        transport_task_id="TRANSPORT-1",
        apply_status=InboundEvidenceApplyStatus.APPLIED,
    )
    evidences.rows[10] = evidence

    first = await flow.apply_in_session(object(), 10, workline_id=7)
    if result_status == "REJECTED" and member_active:
        expect_retry = True
    if not expect_retry:
        assert first.disposition is BusinessEvidenceDisposition.IGNORED
        creator.create_windowed_inbound.assert_not_awaited()
        return

    assert (first.disposition, first.retry_after_ms) == (BusinessEvidenceDisposition.DEFERRED, 1000)
    creator.create_windowed_inbound.assert_not_awaited()
    binding.correlation_id = "retry:1:TRANSPORT-0"
    assert await flow._apply_transport_result(object(), evidence, 7) == 2000
    binding.correlation_id = "pt:31:e:1:rack:RACK-1"

    evidence.decision_next_attempt_at = NOW + timedelta(seconds=1)
    creator.create_windowed_inbound.return_value = "PENDING"
    second = await flow.apply_in_session(object(), 10, workline_id=7)
    assert (second.disposition, second.retry_after_ms) == (BusinessEvidenceDisposition.DEFERRED, 1000)
    creator.create_windowed_inbound.return_value = "CREATED"
    second = await flow.apply_in_session(object(), 10, workline_id=7)
    assert second.disposition is BusinessEvidenceDisposition.APPLIED
    assert creator.create_windowed_inbound.await_count == 2
    assert creator.create_windowed_inbound.await_args.kwargs["correlation_id"] == "retry:1:TRANSPORT-1"
    assert creator.create_windowed_inbound.await_args.kwargs["retry_terminal_inbound"] is True
    assert creator.create_windowed_inbound.await_args.kwargs["intent"].rack_id == "RACK-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "step,kind,target_kind,target_code,final_kind,final_code,face",
    [
        (
            "MANUAL_PICKING_SOURCE_RACK_ROTATE",
            "RACK_ROTATE",
            "RACK_POSITION",
            "FIVE-RACK-POSITION",
            "RACK_POSITION",
            "FIVE-RACK-POSITION",
            "270",
        ),
        ("MANUAL_PICKING_SOURCE_RACK_OUT", "RACK_MOVE", "ZONE", "WH01", "RACK_POSITION", "WHE0809", None),
        ("MANUAL_PICKING_RETURN_BUFFER_DRAIN_RACK_OUT", "RACK_MOVE", "ZONE", "WH01", "RACK_POSITION", "WHE0809", None),
        ("MANUAL_PICKING_TRANSFER_RACK_OUT", "RACK_MOVE", "ZONE", "WH05", "RACK_POSITION", "WHE0406", None),
        (
            "MANUAL_PICKING_TRANSFER_RACK_OUT",
            "RACK_MOVE",
            "RACK_POSITION",
            "STORE-POS",
            "RACK_POSITION",
            "STORE-POS",
            None,
        ),
    ],
)
async def test_rack_switch_result_requires_matching_frozen_transport(
    step: str, kind: str, target_kind: str, target_code: str, final_kind: str, final_code: str, face: str | None
) -> None:
    final = {"kind": final_kind, "location_code": final_code}
    request = {"rack_id": "RACK-1", "target_face": face}
    request["target" if face is None else "position"] = {"kind": target_kind, "location_code": target_code}
    transport_task = SimpleNamespace(
        kind=kind, transport_task_id="TRANSPORT-1", client_request_id="REQUEST-1", request_json=request
    )
    flow, evidences, _, _, _ = _setup(transport_reader=SimpleNamespace(get_task=AsyncMock(return_value=transport_task)))
    evidences.rows[10] = InboundEvidence(
        id=10,
        kind=InboundEvidenceKind.TRANSPORT_RESULT,
        source_identity="transport:TRANSPORT-1:outcome:1",
        payload_digest="d" * 64,
        normalized_payload={
            "transport_task_id": "TRANSPORT-1",
            "client_request_id": "REQUEST-1",
            "outcome_version": 1,
            "caller": {"workline_id": "7"},
            "status": "SUCCEEDED",
            "members": [{"object_id": "RACK-1", "final_position": final, "arrival_face": face}],
            "rack_id": "RACK-1",
            "step": step,
            "source_evidence_id": 1,
        },
        received_at=NOW,
        workline_id=7,
        transport_task_id="TRANSPORT-1",
        apply_status=InboundEvidenceApplyStatus.APPLIED,
    )
    assert (await flow.apply_in_session(object(), 10, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    if target_kind == "ZONE":
        evidences.rows[10].normalized_payload["members"][0]["final_position"]["kind"] = "ZONE"
    else:
        evidences.rows[10].normalized_payload["members"][0]["final_position"]["location_code"] = "WRONG"
    assert (
        await flow.apply_in_session(object(), 10, workline_id=7)
    ).disposition is BusinessEvidenceDisposition.RECONCILING


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing_projection",
    [("RACK", "TRANSFER-1"), ("BIN", "A000000001")],
)
async def test_scan1_waits_for_verified_target_rack_and_bin_position(missing_projection: tuple[str, str]) -> None:
    flow, _, passages, commands, _ = _setup(missing_projection=missing_projection)

    result = await flow.apply_in_session(object(), 1, workline_id=7)

    assert result.disposition is BusinessEvidenceDisposition.DEFERRED
    assert commands.requests == []
    assert passages.rows == []


@pytest.mark.asyncio
async def test_scan1_does_not_treat_failed_bin_transport_position_as_arrival() -> None:
    flow, _, passages, commands, _ = _setup(failed_transport="MOVE-A000000001")

    result = await flow.apply_in_session(object(), 1, workline_id=7)

    assert result.disposition is BusinessEvidenceDisposition.RECONCILING
    assert commands.requests == []
    assert passages.rows == []


@pytest.mark.asyncio
async def test_scan1_uses_the_bin_transport_even_after_its_source_rack_position_becomes_unknown() -> None:
    flow, _, passages, commands, _ = _setup()
    flow._source_racks = SimpleNamespace(
        list_bin_source_racks=AsyncMock(
            return_value=[
                SimpleNamespace(rack_id="RACK-1", rack_face="90"),
                SimpleNamespace(rack_id="RACK-2", rack_face="270"),
            ]
        )
    )
    original_get = flow._positions.get

    async def get_projection(db, object_type, object_id):  # type: ignore[no-untyped-def]
        if (object_type, object_id) == ("RACK", "RACK-1"):
            return SimpleNamespace(position_unknown=True)
        return await original_get(db, object_type, object_id)

    flow._positions.get = get_projection

    result = await flow.apply_in_session(object(), 1, workline_id=7)

    assert result.disposition is BusinessEvidenceDisposition.APPLIED
    assert len(passages.rows) == 1
    assert passages.rows[0].bin_code == "A000000001"
    assert [request.task_type for request in commands.requests] == ["MOVE_FORWARD"]


@pytest.mark.asyncio
async def test_scan1_rejects_bin_from_a_source_rack_outside_the_task() -> None:
    flow, _, passages, commands, _ = _setup()
    flow._source_racks = SimpleNamespace(
        list_bin_source_racks=AsyncMock(return_value=[SimpleNamespace(rack_id="RACK-2", rack_face="270")])
    )

    result = await flow.apply_in_session(object(), 1, workline_id=7)

    assert result.disposition is BusinessEvidenceDisposition.RECONCILING
    assert passages.rows == []
    assert commands.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("different_position_codes", [False, True])
async def test_scan1_then_scan2_freezes_passage_and_typed_wms_admission(different_position_codes) -> None:
    flow, _, passages, commands, admissions = _setup()
    if different_position_codes:
        flow._worklines.line.config["position_bindings"] = {
            role: f"CONFIG-{role}" for role in flow._worklines.line.position_bindings
        }

    assert (await flow.apply_in_session(object(), 1, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    assert (await flow.apply_in_session(object(), 2, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    assert len(passages.rows) == 1
    passage = passages.rows[0]
    assert passage.bin_code == "A000000001"
    assert passage.scan1_evidence_id == 1
    assert passage.scan2_evidence_id == 2
    assert commands.requests[0].task_type == "MOVE_FORWARD"
    assert commands.requests[0].material_execution_id is None
    assert len(commands.requests) == 1
    assert admissions.intents[0].bin_code == "A000000001"
    assert admissions.intents[0].task_id == "PICK-001"


@pytest.mark.asyncio
async def test_debug_scan2_does_not_request_wms_or_release_bin() -> None:
    flow, evidences, passages, commands, admissions = _setup()
    evidences.rows[2].normalized_payload["is_debug"] = True

    await flow.apply_in_session(object(), 1, workline_id=7)
    result = await flow.apply_in_session(object(), 2, workline_id=7)

    assert result.disposition is BusinessEvidenceDisposition.IGNORED
    assert passages.rows[0].scan2_evidence_id is None
    assert len(commands.requests) == 1
    assert admissions.intents == []


@pytest.mark.asyncio
async def test_no_work_result_releases_point2_without_material_execution() -> None:
    flow, evidences, passages, commands, admissions = _setup()
    await flow.apply_in_session(object(), 1, workline_id=7)
    await flow.apply_in_session(object(), 2, workline_id=7)
    operation_id = admissions.intents[0].operation_id
    evidences.rows[3] = _wms(3, InboundEvidenceKind.WMS_RESULT, operation_id, {"result": "NO_WORK"})

    result = await flow.apply_in_session(object(), 3, workline_id=7)

    assert result.disposition is BusinessEvidenceDisposition.APPLIED
    assert passages.rows[0].disposition == "NORMAL"
    assert passages.rows[0].admission_result == "NO_WORK"
    assert [request.task_type for request in commands.requests] == ["MOVE_FORWARD", "MOVE_FORWARD"]
    assert commands.requests[1].material_execution_id is None


@pytest.mark.asyncio
async def test_no_work_result_waits_for_known_scan2_command_then_rechecks() -> None:
    flow, evidences, passages, commands, admissions = _setup()
    await flow.apply_in_session(object(), 1, workline_id=7)
    await flow.apply_in_session(object(), 2, workline_id=7)
    evidences.rows[3] = _wms(
        3, InboundEvidenceKind.WMS_RESULT, admissions.intents[0].operation_id, {"result": "NO_WORK"}
    )
    commands.requests.append(SimpleNamespace(workline_id=7, device_code="S2"))
    commands.statuses["COMMAND-2"] = "ACKNOWLEDGED"

    waiting = await flow.apply_in_session(object(), 3, workline_id=7)
    assert (waiting.disposition, waiting.retry_after_ms) == (BusinessEvidenceDisposition.DEFERRED, 1000)
    assert passages.rows[0].admission_result is None

    commands.statuses["COMMAND-2"] = "SUCCEEDED"
    assert (await flow.apply_in_session(object(), 3, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    assert passages.rows[0].admission_result == "NO_WORK"


@pytest.mark.asyncio
async def test_work_required_waits_for_matching_wms_completion() -> None:
    flow, evidences, passages, commands, admissions = _setup()
    await flow.apply_in_session(object(), 1, workline_id=7)
    await flow.apply_in_session(object(), 2, workline_id=7)
    operation_id = admissions.intents[0].operation_id
    evidences.rows[3] = _wms(
        3, InboundEvidenceKind.WMS_RESULT, operation_id, {"result": "WORK_REQUIRED", "task_id": "PICK-001"}
    )
    evidences.rows[4] = _wms(
        4,
        InboundEvidenceKind.WMS_EVENT,
        "019f12d0-58d7-7b4d-a23a-1b90aa5d4484",
        {
            "task_id": "PICK-001",
            "bin_code": "A000000001",
            "result": "NORMAL",
            "completed_at": 1_788_389_999_000,
        },
    )

    assert (await flow.apply_in_session(object(), 3, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    assert len(commands.requests) == 1
    assert (await flow.apply_in_session(object(), 4, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    assert passages.rows[0].wms_result == "NORMAL"
    assert passages.rows[0].wms_completed_evidence_id == 4
    assert passages.rows[0].reason_code is None
    assert [request.task_type for request in commands.requests] == ["MOVE_FORWARD", "MOVE_FORWARD"]


@pytest.mark.asyncio
async def test_wms_completion_waits_for_known_scan2_command_then_rechecks() -> None:
    flow, evidences, passages, commands, admissions = _setup()
    await flow.apply_in_session(object(), 1, workline_id=7)
    await flow.apply_in_session(object(), 2, workline_id=7)
    evidences.rows[3] = _wms(
        3,
        InboundEvidenceKind.WMS_RESULT,
        admissions.intents[0].operation_id,
        {"result": "WORK_REQUIRED", "task_id": "PICK-001"},
    )
    await flow.apply_in_session(object(), 3, workline_id=7)
    evidences.rows[4] = _wms(
        4,
        InboundEvidenceKind.WMS_EVENT,
        "019f12d0-58d7-7b4d-a23a-1b90aa5d4484",
        {
            "task_id": "PICK-001",
            "bin_code": "A000000001",
            "result": "NORMAL",
            "completed_at": 1_788_389_999_000,
        },
    )
    commands.requests.append(SimpleNamespace(workline_id=7, device_code="S2"))
    commands.statuses["COMMAND-2"] = "ACKNOWLEDGED"

    waiting = await flow.apply_in_session(object(), 4, workline_id=7)
    assert (waiting.disposition, waiting.retry_after_ms) == (BusinessEvidenceDisposition.DEFERRED, 1000)
    assert passages.rows[0].wms_completed_evidence_id is None

    commands.statuses["COMMAND-2"] = "SUCCEEDED"
    assert (await flow.apply_in_session(object(), 4, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    assert passages.rows[0].wms_completed_evidence_id == 4


@pytest.mark.asyncio
async def test_ng_work_completion_persists_reason_and_scan3_routes_left() -> None:
    flow, evidences, passages, commands, admissions = _setup()
    await flow.apply_in_session(object(), 1, workline_id=7)
    await flow.apply_in_session(object(), 2, workline_id=7)
    operation_id = admissions.intents[0].operation_id
    evidences.rows[3] = _wms(
        3, InboundEvidenceKind.WMS_RESULT, operation_id, {"result": "WORK_REQUIRED", "task_id": "PICK-001"}
    )
    evidences.rows[4] = _wms(
        4,
        InboundEvidenceKind.WMS_EVENT,
        "019f12d0-58d7-7b4d-a23a-1b90aa5d4484",
        {
            "task_id": "PICK-001",
            "bin_code": "A000000001",
            "result": "NG",
            "completed_at": 1_788_389_999_000,
        },
    )

    assert (await flow.apply_in_session(object(), 3, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    assert (await flow.apply_in_session(object(), 4, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    assert passages.rows[0].wms_result == "NG"
    assert passages.rows[0].reason_code == "MANUAL_PICK_NG"
    evidences.rows[5] = _scan(5, "S3", "A000000001-B")
    assert (await flow.apply_in_session(object(), 5, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    assert commands.requests[-1].task_type == "MOVE_LEFT"


@pytest.mark.asyncio
async def test_wms_completion_arriving_before_admission_result_never_autobinds() -> None:
    flow, evidences, passages, commands, admissions = _setup()
    await flow.apply_in_session(object(), 1, workline_id=7)
    await flow.apply_in_session(object(), 2, workline_id=7)
    operation_id = admissions.intents[0].operation_id
    evidences.rows[3] = _wms(
        3,
        InboundEvidenceKind.WMS_EVENT,
        "019f12d0-58d7-7b4d-a23a-1b90aa5d4484",
        {
            "task_id": "PICK-001",
            "bin_code": "A000000001",
            "result": "NORMAL",
            "completed_at": 1_788_389_999_000,
        },
    )
    evidences.rows[4] = _wms(
        4, InboundEvidenceKind.WMS_RESULT, operation_id, {"result": "WORK_REQUIRED", "task_id": "PICK-001"}
    )

    assert (
        await flow.apply_in_session(object(), 3, workline_id=7)
    ).disposition is BusinessEvidenceDisposition.RECONCILING
    assert len(commands.requests) == 1
    assert (await flow.apply_in_session(object(), 4, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    assert passages.rows[0].wms_completed_evidence_id is None
    assert len(commands.requests) == 1


@pytest.mark.asyncio
async def test_same_completed_result_with_new_event_id_is_noop() -> None:
    flow, evidences, passages, commands, admissions = _setup()
    await flow.apply_in_session(object(), 1, workline_id=7)
    await flow.apply_in_session(object(), 2, workline_id=7)
    evidences.rows[3] = _wms(
        3,
        InboundEvidenceKind.WMS_RESULT,
        admissions.intents[0].operation_id,
        {"result": "WORK_REQUIRED", "task_id": "PICK-001"},
    )
    await flow.apply_in_session(object(), 3, workline_id=7)
    completed = {
        "task_id": "PICK-001",
        "bin_code": "A000000001",
        "result": "NORMAL",
        "completed_at": 1_788_389_999_000,
    }
    evidences.rows[4] = _wms(4, InboundEvidenceKind.WMS_EVENT, "019f12d0-58d7-7b4d-a23a-1b90aa5d4484", completed)
    evidences.rows[5] = _wms(5, InboundEvidenceKind.WMS_EVENT, "019f12d0-58d7-7b4d-a23a-1b90aa5d4485", completed)
    await flow.apply_in_session(object(), 4, workline_id=7)

    assert (await flow.apply_in_session(object(), 5, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    assert passages.rows[0].wms_completed_evidence_id == 4
    assert len(commands.requests) == 2


@pytest.mark.asyncio
async def test_wait_creates_new_due_admission_without_releasing_point2() -> None:
    flow, evidences, passages, commands, admissions = _setup()
    await flow.apply_in_session(object(), 1, workline_id=7)
    await flow.apply_in_session(object(), 2, workline_id=7)
    old_id = admissions.intents[0].operation_id
    evidences.rows[3] = _wms(3, InboundEvidenceKind.WMS_RESULT, old_id, {"result": "WAIT", "retry_after_ms": 1000})

    assert (await flow.apply_in_session(object(), 3, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    assert len(commands.requests) == 1
    assert len(admissions.intents) == 2
    assert admissions.intents[1].operation_id != old_id
    assert passages.rows[0].admission_operation_id == admissions.intents[1].operation_id
    assert admissions.options[1]["not_before"] > admissions.options[1]["created_at"]


@pytest.mark.asyncio
async def test_scan3_unknown_goes_left_and_scan4_unreadable_holds() -> None:
    flow, evidences, _passages, commands, admissions = _setup()
    evidences.rows[3] = _scan(3, "S3", "A000000099-B")
    evidences.rows[4] = _scan(4, "S4", "A000000001-C")
    await flow.apply_in_session(object(), 1, workline_id=7)
    await flow.apply_in_session(object(), 2, workline_id=7)
    operation_id = admissions.intents[0].operation_id
    evidences.rows[5] = _wms(5, InboundEvidenceKind.WMS_RESULT, operation_id, {"result": "NO_WORK"})
    await flow.apply_in_session(object(), 5, workline_id=7)

    assert (await flow.apply_in_session(object(), 3, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    assert commands.requests[-1].task_type == "MOVE_LEFT"
    unknown_command_code = f"COMMAND-{len(commands.requests)}"
    evidences.rows[6] = _result(6, unknown_command_code, device_code="S3")
    commands.statuses[unknown_command_code] = "ACKNOWLEDGED"
    assert (
        await flow.apply_in_session(object(), 6, workline_id=7)
    ).disposition is BusinessEvidenceDisposition.RECONCILING
    commands.statuses[unknown_command_code] = "SUCCEEDED"
    assert (await flow.apply_in_session(object(), 6, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    before = len(commands.requests)
    assert (
        await flow.apply_in_session(object(), 4, workline_id=7)
    ).disposition is BusinessEvidenceDisposition.RECONCILING
    assert len(commands.requests) == before
    assert flow._returns.rows == []


@pytest.mark.asyncio
async def test_unassociated_unpublished_evidence_does_not_block_any_scan_point() -> None:
    flow, evidences, _passages, commands, admissions = _setup()
    for evidence_id, device_code in enumerate(("S1", "S2", "S3", "S4"), start=101):
        historical = _scan(evidence_id, device_code, "UNASSOCIATED")
        historical.received_at = NOW - timedelta(seconds=1)
        historical.apply_status = InboundEvidenceApplyStatus.RECONCILING
        evidences.rows[evidence_id] = historical
    evidences.rows[4] = _scan(4, "S3", "A000000001-B")
    evidences.rows[5] = _scan(5, "S4", "A000000001-B")

    assert (await flow.apply_in_session(object(), 1, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    assert (await flow.apply_in_session(object(), 2, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    evidences.rows[3] = _wms(
        3, InboundEvidenceKind.WMS_RESULT, admissions.intents[0].operation_id, {"result": "NO_WORK"}
    )
    assert (await flow.apply_in_session(object(), 3, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    assert (await flow.apply_in_session(object(), 4, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    assert (await flow.apply_in_session(object(), 5, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    assert flow._returns.rows[0].scan4_evidence_id == 5
    assert [request.task_type for request in commands.requests] == [
        "MOVE_FORWARD",
        "MOVE_FORWARD",
        "MOVE_FORWARD",
        "MOVE_FORWARD",
    ]


@pytest.mark.asyncio
async def test_scan3_current_valid_b_overrides_scan1_direct_route() -> None:
    flow, evidences, passages, commands, _ = _setup()
    evidences.rows[1] = _scan(1, "S1", "A000000001-C")
    evidences.rows[3] = _scan(3, "S3", "A000000001-B")

    assert (await flow.apply_in_session(object(), 1, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    assert (await flow.apply_in_session(object(), 3, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    assert passages.rows[0].bin_code == "A000000001"
    assert passages.rows[0].scan3_evidence_id == 3
    assert [request.task_type for request in commands.requests] == ["MOVE_RIGHT", "MOVE_FORWARD"]
    assert passages.rows[0].disposition == "CLOSED"
    assert passages.rows[0].scan2_evidence_id is None
    evidences.rows[4] = _scan(4, "S4", "A000000001-B")
    assert (await flow.apply_in_session(object(), 4, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    assert flow._returns.rows[0].scan4_evidence_id == 4


@pytest.mark.asyncio
async def test_scan2_other_bin_does_not_claim_or_mutate_fifo_head() -> None:
    flow, evidences, passages, commands, admissions = _setup()
    evidences.rows[2] = _scan(2, "S2", "A000000099-C")
    await flow.apply_in_session(object(), 1, workline_id=7)

    result = await flow.apply_in_session(object(), 2, workline_id=7)

    assert result.disposition is BusinessEvidenceDisposition.APPLIED
    assert passages.rows[0].bin_code == "A000000001"
    assert passages.rows[0].scan2_evidence_id is None
    assert passages.rows[0].disposition == "OPEN"
    assert passages.rows[0].scan2_fault_evidence_id == 2
    assert [request.task_type for request in commands.requests] == ["MOVE_FORWARD", "MOVE_FORWARD"]
    assert admissions.intents == []
    evidences.rows[3] = _scan(3, "S2", "A000000001-C")
    assert (
        await flow.apply_in_session(object(), 3, workline_id=7)
    ).disposition is BusinessEvidenceDisposition.RECONCILING
    assert len(commands.requests) == 2


@pytest.mark.asyncio
async def test_scan2_without_fifo_head_never_releases_unknown_bin() -> None:
    flow, _, _, commands, admissions = _setup()

    result = await flow.apply_in_session(object(), 2, workline_id=7)

    assert result.disposition is BusinessEvidenceDisposition.RECONCILING
    assert commands.requests == []
    assert admissions.intents == []


@pytest.mark.asyncio
async def test_scan2_abnormal_code_waits_for_head_physical_success() -> None:
    flow, evidences, passages, commands, _ = _setup()
    evidences.rows[2] = _scan(2, "S2", "A000000099-C")
    await flow.apply_in_session(object(), 1, workline_id=7)
    commands.statuses[passages.rows[0].scan1_command_code] = "ACKNOWLEDGED"

    assert (await flow.apply_in_session(object(), 2, workline_id=7)).disposition is BusinessEvidenceDisposition.DEFERRED
    assert passages.rows[0].scan2_fault_evidence_id is None
    assert len(commands.requests) == 1


@pytest.mark.asyncio
async def test_scan2_does_not_release_a_second_bin_while_first_waits_for_wms() -> None:
    flow, evidences, passages, commands, admissions = _setup()
    await flow.apply_in_session(object(), 1, workline_id=7)
    await flow.apply_in_session(object(), 2, workline_id=7)
    evidences.rows[3] = _scan(3, "S2", "A000000099-C")

    result = await flow.apply_in_session(object(), 3, workline_id=7)

    assert result.disposition is BusinessEvidenceDisposition.DEFERRED
    assert len(commands.requests) == 1
    assert len(admissions.intents) == 1
    assert passages.rows[0].scan2_evidence_id == 2


@pytest.mark.asyncio
async def test_scan2_rescan_of_same_bin_is_not_retried_as_a_new_passage() -> None:
    flow, evidences, passages, commands, admissions = _setup()
    await flow.apply_in_session(object(), 1, workline_id=7)
    await flow.apply_in_session(object(), 2, workline_id=7)
    evidences.rows[3] = _scan(3, "S2", "A000000001-C")

    result = await flow.apply_in_session(object(), 3, workline_id=7)

    assert result.disposition is BusinessEvidenceDisposition.RECONCILING
    assert len(commands.requests) == 1
    assert len(admissions.intents) == 1
    assert passages.rows[0].scan2_evidence_id == 2


@pytest.mark.asyncio
async def test_scan4_enters_return_buffer_only_after_matching_ecs_success() -> None:
    flow, evidences, passages, commands, admissions = _setup()
    evidences.rows[4] = _scan(4, "S3", "A000000001-B")
    evidences.rows[5] = _scan(5, "S4", "A000000001-B")
    await flow.apply_in_session(object(), 1, workline_id=7)
    await flow.apply_in_session(object(), 2, workline_id=7)
    operation_id = admissions.intents[0].operation_id
    evidences.rows[3] = _wms(3, InboundEvidenceKind.WMS_RESULT, operation_id, {"result": "NO_WORK"})
    await flow.apply_in_session(object(), 3, workline_id=7)

    assert (await flow.apply_in_session(object(), 4, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    assert (await flow.apply_in_session(object(), 5, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    passage = passages.rows[0]
    assert flow._returns.rows[0].return_state == "MOVE_PENDING"
    assert flow._returns.rows[0].scan4_command_code == f"COMMAND-{len(commands.requests)}"
    evidences.rows[6] = _result(6, flow._returns.rows[0].scan4_command_code)

    assert (await flow.apply_in_session(object(), 6, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    assert flow._returns.rows[0].return_state == "READY"
    projection = await flow._positions.get(object(), "BIN", passage.bin_code, for_update=True)
    assert projection.position_json == {"kind": "HANDOFF_POSITION", "location_code": "OUTLET-POSITION"}
    assert projection.position_unknown is False


@pytest.mark.asyncio
async def test_scan4_does_not_enter_return_buffer_for_acknowledged_command() -> None:
    flow, evidences, _passages, commands, admissions = _setup()
    evidences.rows[4] = _scan(4, "S3", "A000000001-B")
    evidences.rows[5] = _scan(5, "S4", "A000000001-B")
    await flow.apply_in_session(object(), 1, workline_id=7)
    await flow.apply_in_session(object(), 2, workline_id=7)
    evidences.rows[3] = _wms(
        3, InboundEvidenceKind.WMS_RESULT, admissions.intents[0].operation_id, {"result": "NO_WORK"}
    )
    await flow.apply_in_session(object(), 3, workline_id=7)
    await flow.apply_in_session(object(), 4, workline_id=7)
    await flow.apply_in_session(object(), 5, workline_id=7)
    command_code = flow._returns.rows[0].scan4_command_code
    commands.statuses[command_code] = "ACKNOWLEDGED"
    evidences.rows[6] = _result(6, command_code)

    assert (
        await flow.apply_in_session(object(), 6, workline_id=7)
    ).disposition is BusinessEvidenceDisposition.RECONCILING
    assert flow._returns.rows[0].return_state == "MOVE_PENDING"


@pytest.mark.asyncio
async def test_scan4_first_arrival_does_not_wait_for_unrelated_device_command() -> None:
    flow, evidences, _passages, commands, admissions = _setup()
    evidences.rows[4] = _scan(4, "S3", "A000000001-B")
    evidences.rows[5] = _scan(5, "S4", "A000000001-B")
    await flow.apply_in_session(object(), 1, workline_id=7)
    await flow.apply_in_session(object(), 2, workline_id=7)
    evidences.rows[3] = _wms(
        3, InboundEvidenceKind.WMS_RESULT, admissions.intents[0].operation_id, {"result": "NO_WORK"}
    )
    await flow.apply_in_session(object(), 3, workline_id=7)
    await flow.apply_in_session(object(), 4, workline_id=7)
    commands.requests.append(SimpleNamespace(workline_id=7, device_code="S4"))
    commands.statuses["COMMAND-4"] = "ACKNOWLEDGED"

    assert (await flow.apply_in_session(object(), 5, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    assert flow._returns.rows[0].scan4_evidence_id == 5
    assert flow._returns.rows[0].scan4_event_time == evidences.rows[5].normalized_payload["timestamp"]
    assert commands.statuses["COMMAND-4"] == "ACKNOWLEDGED"


@pytest.mark.asyncio
async def test_scan4_freezes_first_arrival_while_own_scan3_result_is_pending() -> None:
    flow, evidences, passages, commands, admissions = _setup()
    evidences.rows[4] = _scan(4, "S3", "A000000001-B")
    evidences.rows[5] = _scan(5, "S4", "A000000001-B")
    await flow.apply_in_session(object(), 1, workline_id=7)
    await flow.apply_in_session(object(), 2, workline_id=7)
    evidences.rows[3] = _wms(
        3, InboundEvidenceKind.WMS_RESULT, admissions.intents[0].operation_id, {"result": "NO_WORK"}
    )
    await flow.apply_in_session(object(), 3, workline_id=7)
    await flow.apply_in_session(object(), 4, workline_id=7)
    passages.rows[0]
    commands.statuses[flow._returns.rows[0].scan3_command_code] = "ACKNOWLEDGED"
    before = len(commands.requests)

    waiting = await flow.apply_in_session(object(), 5, workline_id=7)
    assert waiting.disposition is BusinessEvidenceDisposition.DEFERRED
    assert flow._returns.rows[0].scan4_evidence_id == 5
    assert flow._returns.rows[0].scan4_event_time == evidences.rows[5].normalized_payload["timestamp"]
    assert flow._returns.rows[0].scan4_command_code is None
    assert len(commands.requests) == before

    first_arrival = flow._returns.rows[0].scan4_event_time
    commands.statuses[flow._returns.rows[0].scan3_command_code] = "SUCCEEDED"
    assert (await flow.apply_in_session(object(), 5, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    assert flow._returns.rows[0].scan4_event_time == first_arrival
    assert flow._returns.rows[0].scan4_command_code == f"COMMAND-{before + 1}"
    assert len(commands.requests) == before + 1
    assert (await flow.apply_in_session(object(), 5, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    assert len(commands.requests) == before + 1


@pytest.mark.asyncio
async def test_scan4_timed_out_scan3_keeps_first_arrival_without_waiting_forever() -> None:
    flow, evidences, passages, commands, admissions = _setup()
    evidences.rows[4] = _scan(4, "S3", "A000000001-B")
    evidences.rows[5] = _scan(5, "S4", "A000000001-B")
    await flow.apply_in_session(object(), 1, workline_id=7)
    await flow.apply_in_session(object(), 2, workline_id=7)
    evidences.rows[3] = _wms(
        3, InboundEvidenceKind.WMS_RESULT, admissions.intents[0].operation_id, {"result": "NO_WORK"}
    )
    await flow.apply_in_session(object(), 3, workline_id=7)
    await flow.apply_in_session(object(), 4, workline_id=7)
    passages.rows[0]
    commands.statuses[flow._returns.rows[0].scan3_command_code] = "TIMED_OUT"
    before = len(commands.requests)

    assert (
        await flow.apply_in_session(object(), 5, workline_id=7)
    ).disposition is BusinessEvidenceDisposition.RECONCILING
    assert flow._returns.rows[0].scan4_evidence_id == 5
    assert flow._returns.rows[0].scan4_event_time == evidences.rows[5].normalized_payload["timestamp"]
    assert flow._returns.rows[0].scan4_command_code is None
    assert len(commands.requests) == before


@pytest.mark.asyncio
async def test_scan1_waits_for_previous_physical_result_and_does_not_duplicate_active_bin() -> None:
    flow, evidences, passages, commands, _ = _setup()
    evidences.rows[3] = _scan(3, "S1", "A000000002-B")
    evidences.rows[4] = _scan(4, "S1", "A000000001-B")
    await flow.apply_in_session(object(), 1, workline_id=7)
    commands.statuses[passages.rows[0].scan1_command_code] = "ACKNOWLEDGED"

    assert (await flow.apply_in_session(object(), 3, workline_id=7)).disposition is BusinessEvidenceDisposition.DEFERRED
    assert len(commands.requests) == len(passages.rows) == 1

    commands.statuses[passages.rows[0].scan1_command_code] = "SUCCEEDED"
    assert (
        await flow.apply_in_session(object(), 4, workline_id=7)
    ).disposition is BusinessEvidenceDisposition.RECONCILING
    assert len(commands.requests) == len(passages.rows) == 1
    assert (await flow.apply_in_session(object(), 3, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    assert len(commands.requests) == len(passages.rows) == 2


@pytest.mark.asyncio
async def test_scan3_rescan_waits_while_first_command_is_pending() -> None:
    flow, evidences, passages, commands, admissions = _setup()
    evidences.rows[4] = _scan(4, "S3", "A000000001-B")
    evidences.rows[5] = _scan(5, "S3", "A000000001-B")
    await flow.apply_in_session(object(), 1, workline_id=7)
    await flow.apply_in_session(object(), 2, workline_id=7)
    evidences.rows[3] = _wms(
        3, InboundEvidenceKind.WMS_RESULT, admissions.intents[0].operation_id, {"result": "NO_WORK"}
    )
    await flow.apply_in_session(object(), 3, workline_id=7)
    await flow.apply_in_session(object(), 4, workline_id=7)
    frozen_command = flow._returns.rows[0].scan3_command_code
    commands.statuses[frozen_command] = "ACKNOWLEDGED"
    before = len(commands.requests)

    assert (await flow.apply_in_session(object(), 5, workline_id=7)).disposition is BusinessEvidenceDisposition.DEFERRED
    assert len(commands.requests) == before
    assert passages.rows[0].scan3_evidence_id == 4
    assert flow._returns.rows[0].scan3_command_code == frozen_command
    assert commands.requests[-1].task_type == "MOVE_FORWARD"


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["FAILED", "TIMED_OUT"])
async def test_scan3_rescan_retries_after_first_command_reaches_terminal_state(terminal_status: str) -> None:
    flow, evidences, passages, commands, admissions = _setup()
    evidences.rows[4] = _scan(4, "S3", "A000000001-B")
    evidences.rows[5] = _scan(5, "S3", "A000000001-B")
    await flow.apply_in_session(object(), 1, workline_id=7)
    await flow.apply_in_session(object(), 2, workline_id=7)
    evidences.rows[3] = _wms(
        3, InboundEvidenceKind.WMS_RESULT, admissions.intents[0].operation_id, {"result": "NO_WORK"}
    )
    await flow.apply_in_session(object(), 3, workline_id=7)
    await flow.apply_in_session(object(), 4, workline_id=7)
    stuck_command = flow._returns.rows[0].scan3_command_code
    commands.statuses[stuck_command] = terminal_status
    before = len(commands.requests)

    assert (await flow.apply_in_session(object(), 5, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    assert len(commands.requests) == before + 1
    assert passages.rows[0].scan3_evidence_id == 4
    assert flow._returns.rows[0].scan3_command_code != stuck_command
    assert commands.requests[-1].task_type == "MOVE_FORWARD"

    retry_command = flow._returns.rows[0].scan3_command_code
    assert (await flow.apply_in_session(object(), 5, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    assert len(commands.requests) == before + 1
    assert flow._returns.rows[0].scan3_command_code == retry_command


@pytest.mark.asyncio
async def test_scan3_rescan_after_command_success_creates_no_new_action() -> None:
    flow, evidences, passages, commands, admissions = _setup()
    evidences.rows[4] = _scan(4, "S3", "A000000001-B")
    evidences.rows[5] = _scan(5, "S3", "A000000001-B")
    await flow.apply_in_session(object(), 1, workline_id=7)
    await flow.apply_in_session(object(), 2, workline_id=7)
    evidences.rows[3] = _wms(
        3, InboundEvidenceKind.WMS_RESULT, admissions.intents[0].operation_id, {"result": "NO_WORK"}
    )
    await flow.apply_in_session(object(), 3, workline_id=7)
    await flow.apply_in_session(object(), 4, workline_id=7)
    succeeded_command = flow._returns.rows[0].scan3_command_code
    commands.statuses[succeeded_command] = "SUCCEEDED"
    before = len(commands.requests)

    assert (
        await flow.apply_in_session(object(), 5, workline_id=7)
    ).disposition is BusinessEvidenceDisposition.RECONCILING
    assert len(commands.requests) == before
    assert passages.rows[0].scan3_evidence_id == 4
    assert flow._returns.rows[0].scan3_command_code == succeeded_command


@pytest.mark.asyncio
async def test_scan3_unknown_does_not_issue_second_command_while_first_is_unclosed() -> None:
    flow, evidences, _, commands, _ = _setup()
    evidences.rows[3] = _scan(3, "S3", "A000000099-B")
    evidences.rows[4] = _scan(4, "S3", "A000000099-B")
    assert (await flow.apply_in_session(object(), 3, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    commands.statuses["COMMAND-1"] = "ACKNOWLEDGED"

    assert (
        await flow.apply_in_session(object(), 4, workline_id=7)
    ).disposition is BusinessEvidenceDisposition.RECONCILING
    assert len(commands.requests) == 1


@pytest.mark.asyncio
async def test_scan1_closed_passage_does_not_hide_unclosed_entry_command() -> None:
    flow, evidences, passages, commands, _ = _setup()
    evidences.rows[1] = _scan(1, "S1", "A000000001-C")
    evidences.rows[5] = _scan(5, "S1", "A000000002-B")
    await flow.apply_in_session(object(), 1, workline_id=7)
    commands.statuses["COMMAND-1"] = "ACKNOWLEDGED"
    passages.rows[0].disposition = "CLOSED"

    assert (await flow.apply_in_session(object(), 5, workline_id=7)).disposition is BusinessEvidenceDisposition.DEFERRED
    assert len(commands.requests) == 1


@pytest.mark.asyncio
async def test_scan3_known_bin_does_not_wait_for_unrelated_unknown_command() -> None:
    flow, evidences, passages, commands, admissions = _setup()
    evidences.rows[3] = _scan(3, "S3", "A000000099-B")
    evidences.rows[5] = _scan(5, "S3", "A000000001-B")
    await flow.apply_in_session(object(), 3, workline_id=7)
    commands.statuses["COMMAND-1"] = "ACKNOWLEDGED"
    await flow.apply_in_session(object(), 1, workline_id=7)
    await flow.apply_in_session(object(), 2, workline_id=7)
    evidences.rows[4] = _wms(
        4, InboundEvidenceKind.WMS_RESULT, admissions.intents[0].operation_id, {"result": "NO_WORK"}
    )
    await flow.apply_in_session(object(), 4, workline_id=7)
    before = len(commands.requests)

    assert (await flow.apply_in_session(object(), 5, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    assert len(commands.requests) == before + 1
    assert passages.rows[0].scan3_evidence_id == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("unresolved", ["ACKNOWLEDGED", "RECONCILING"])
async def test_scan3_known_bin_does_not_wait_for_own_scan2_result_after_arrival(unresolved: str) -> None:
    flow, evidences, passages, commands, admissions = _setup()
    evidences.rows[4] = _scan(4, "S3", "A000000001-B")
    await flow.apply_in_session(object(), 1, workline_id=7)
    await flow.apply_in_session(object(), 2, workline_id=7)
    evidences.rows[3] = _wms(
        3, InboundEvidenceKind.WMS_RESULT, admissions.intents[0].operation_id, {"result": "NO_WORK"}
    )
    await flow.apply_in_session(object(), 3, workline_id=7)
    passage = passages.rows[0]
    commands.statuses[passage.scan2_command_code] = unresolved
    before = len(commands.requests)

    assert (await flow.apply_in_session(object(), 4, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    assert passage.disposition == "CLOSED"
    assert flow._returns.rows[0].scan3_command_code is not None
    assert len(commands.requests) == before + 1


@pytest.mark.asyncio
async def test_scan3_arrival_handoff_and_late_scan2_result_do_not_change_return() -> None:
    returns = _Returns()
    flow, evidences, passages, commands, admissions = _setup(returns=returns)
    evidences.rows[4] = _scan(4, "S3", "A000000001-B")
    await flow.apply_in_session(object(), 1, workline_id=7)
    await flow.apply_in_session(object(), 2, workline_id=7)
    evidences.rows[3] = _wms(
        3, InboundEvidenceKind.WMS_RESULT, admissions.intents[0].operation_id, {"result": "NO_WORK"}
    )
    await flow.apply_in_session(object(), 3, workline_id=7)
    passage = passages.rows[0]
    commands.statuses[passage.scan2_command_code] = "RECONCILING"

    assert (await flow.apply_in_session(object(), 4, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    assert passage.disposition == "CLOSED"
    assert len(returns.rows) == 1
    row = returns.rows[0]
    assert row.scan3_evidence_id == 4 and row.scan3_command_code is not None

    passages.rows.clear()  # CLOSED Passage 不再是运行数据。
    commands.statuses[passage.scan2_command_code] = "FAILED"
    assert (await flow.apply_in_session(object(), 4, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    assert returns.rows == [row]
    assert row.return_state == "NONE"
    evidences.rows[7] = _result(7, passage.scan2_command_code, device_code="S2")
    assert (await flow.apply_in_session(object(), 7, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    assert row.return_state == "NONE"
    evidences.rows[5] = _scan(5, "S4", "A000000001-B")
    assert (await flow.apply_in_session(object(), 5, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    assert row.scan4_evidence_id == 5 and row.return_state == "MOVE_PENDING"
    evidences.rows[6] = _result(6, row.scan4_command_code)
    assert (await flow.apply_in_session(object(), 6, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    assert row.return_state == "READY"
    commands.statuses[row.scan3_command_code] = "FAILED"
    evidences.rows[8] = _scan(8, "S3", "A000000001-B")
    count = len(commands.requests)
    assert (
        await flow.apply_in_session(object(), 8, workline_id=7)
    ).disposition is BusinessEvidenceDisposition.RECONCILING
    assert len(commands.requests) == count


@pytest.mark.asyncio
async def test_scan3_timed_out_scan2_result_does_not_override_arrival() -> None:
    flow, evidences, passages, commands, admissions = _setup()
    evidences.rows[4] = _scan(4, "S3", "A000000001-B")
    await flow.apply_in_session(object(), 1, workline_id=7)
    await flow.apply_in_session(object(), 2, workline_id=7)
    evidences.rows[3] = _wms(
        3, InboundEvidenceKind.WMS_RESULT, admissions.intents[0].operation_id, {"result": "NO_WORK"}
    )
    await flow.apply_in_session(object(), 3, workline_id=7)
    passage = passages.rows[0]
    commands.statuses[passage.scan2_command_code] = "TIMED_OUT"
    before = len(commands.requests)

    assert (await flow.apply_in_session(object(), 4, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    assert passage.scan3_evidence_id == 4 and passage.disposition == "CLOSED"
    assert len(commands.requests) == before + 1
    assert flow._returns.rows[0].scan3_evidence_id == 4


@pytest.mark.asyncio
async def test_scan4_rescan_preserves_first_fifo_order_and_command() -> None:
    flow, evidences, _passages, commands, admissions = _setup()
    evidences.rows[4] = _scan(4, "S3", "A000000001-B")
    evidences.rows[5] = _scan(5, "S4", "A000000001-B")
    evidences.rows[6] = _scan(6, "S4", "A000000001-B")
    await flow.apply_in_session(object(), 1, workline_id=7)
    await flow.apply_in_session(object(), 2, workline_id=7)
    evidences.rows[3] = _wms(
        3, InboundEvidenceKind.WMS_RESULT, admissions.intents[0].operation_id, {"result": "NO_WORK"}
    )
    await flow.apply_in_session(object(), 3, workline_id=7)
    await flow.apply_in_session(object(), 4, workline_id=7)
    await flow.apply_in_session(object(), 5, workline_id=7)
    frozen_command = flow._returns.rows[0].scan4_command_code
    commands.statuses[frozen_command] = "ACKNOWLEDGED"
    before = len(commands.requests)

    assert (await flow.apply_in_session(object(), 6, workline_id=7)).disposition is BusinessEvidenceDisposition.DEFERRED
    assert len(commands.requests) == before
    assert flow._returns.rows[0].scan4_evidence_id == 5
    assert flow._returns.rows[0].scan4_command_code == frozen_command
    assert flow._returns.rows[0].return_state == "MOVE_PENDING"


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["FAILED", "TIMED_OUT"])
async def test_scan4_rescan_after_terminal_command_preserves_first_arrival(terminal_status: str) -> None:
    flow, evidences, _, commands, admissions = _setup()
    evidences.rows[4] = _scan(4, "S3", "A000000001-B")
    evidences.rows[5] = _scan(5, "S4", "A000000001-B")
    evidences.rows[6] = _scan(6, "S4", "A000000001-B")
    await flow.apply_in_session(object(), 1, workline_id=7)
    await flow.apply_in_session(object(), 2, workline_id=7)
    evidences.rows[3] = _wms(
        3, InboundEvidenceKind.WMS_RESULT, admissions.intents[0].operation_id, {"result": "NO_WORK"}
    )
    await flow.apply_in_session(object(), 3, workline_id=7)
    await flow.apply_in_session(object(), 4, workline_id=7)
    await flow.apply_in_session(object(), 5, workline_id=7)
    first_arrival = flow._returns.rows[0].scan4_event_time
    failed_command = flow._returns.rows[0].scan4_command_code
    commands.statuses[failed_command] = terminal_status
    before = len(commands.requests)

    assert (await flow.apply_in_session(object(), 6, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    assert len(commands.requests) == before + 1
    assert flow._returns.rows[0].scan4_evidence_id == 5
    assert flow._returns.rows[0].scan4_event_time == first_arrival
    assert flow._returns.rows[0].scan4_command_code != failed_command
    assert flow._returns.rows[0].return_state == "MOVE_PENDING"

    retry_command = flow._returns.rows[0].scan4_command_code
    assert (await flow.apply_in_session(object(), 6, workline_id=7)).disposition is BusinessEvidenceDisposition.APPLIED
    assert len(commands.requests) == before + 1
    assert flow._returns.rows[0].scan4_command_code == retry_command


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["valid", "different_position_codes", "wrong_rack", "no_ingress", "wrong_member", "pending"]
)
async def test_drain_return_requires_active_ready_chain_and_its_authoritative_ingress(case):
    intent = sdk.wms_operations.outbound_bin_return_batch(
        operation_id="return-1",
        workline_code="LINE-1",
        rack_id="RACK-1",
        rack_face="90",
        return_candidates=(sdk.BinReturnCandidate(1, "A000000001", "OUTLET-POSITION"),),
    )
    reader = SimpleNamespace(read_return=AsyncMock(return_value=(intent, object())))
    result = SimpleNamespace(apply_return_in_session=AsyncMock(return_value="RETURN_READY"))
    flow, evidences, *_ = _setup(batch_reader=reader, batch_result=result)
    if case == "different_position_codes":
        flow._worklines.line.config["position_bindings"] = {
            role: f"CONFIG-{role}" for role in flow._worklines.line.position_bindings
        }
    drain = SimpleNamespace(
        result=sdk.ReturnBufferDrainReady(
            (sdk.RackFaceSequence("WRONG" if case == "wrong_rack" else "RACK-1", ("90",)),)
        )
        if case != "pending"
        else None
    )
    flow._drains = SimpleNamespace(
        current=AsyncMock(return_value=drain),
        transport=AsyncMock(return_value=None if case == "no_ingress" else object()),
        arrival_matches=AsyncMock(return_value=case != "wrong_member"),
    )
    flow._source_racks.has_source_face = AsyncMock(return_value=True)
    evidence = _wms(31, InboundEvidenceKind.WMS_RESULT, "return-1", {})
    evidence.operation = "outbound.bin.return_batch@v1"
    evidences.rows[31] = evidence
    applied = await flow.apply_in_session(object(), 31, workline_id=7)
    assert applied.disposition is (
        BusinessEvidenceDisposition.APPLIED
        if case in {"valid", "different_position_codes", "wrong_rack", "pending"}
        else BusinessEvidenceDisposition.RECONCILING
    )
    assert result.apply_return_in_session.await_count == int(
        case in {"valid", "different_position_codes", "wrong_rack", "pending"}
    )
    if case in {"wrong_rack", "pending"}:
        flow._source_racks.has_source_face.assert_awaited_once()
    else:
        flow._source_racks.has_source_face.assert_not_awaited()


@pytest.mark.asyncio
async def test_source_return_no_batch_applies_when_current_drain_reserves_a_different_rack() -> None:
    intent = sdk.wms_operations.outbound_bin_return_batch(
        operation_id="return-source",
        workline_code="LINE-1",
        rack_id="RACK-1",
        rack_face="90",
        return_candidates=(sdk.BinReturnCandidate(1, "A000000001", "OUTLET-POSITION"),),
    )
    reader = SimpleNamespace(read_return=AsyncMock(return_value=(intent, object())))
    result = SimpleNamespace(apply_return_in_session=AsyncMock(return_value="RETURN_NO_BATCH"))
    flow, evidences, *_ = _setup(batch_reader=reader, batch_result=result)
    flow._drains = SimpleNamespace(
        current=AsyncMock(
            return_value=SimpleNamespace(
                result=sdk.ReturnBufferDrainReady((sdk.RackFaceSequence("DRAIN-RACK", ("270",)),))
            )
        ),
        transport=AsyncMock(),
        arrival_matches=AsyncMock(),
    )
    flow._source_racks.has_source_face = AsyncMock(return_value=True)
    evidence = _wms(32, InboundEvidenceKind.WMS_RESULT, "return-source", {})
    evidence.operation = "outbound.bin.return_batch@v1"
    evidences.rows[32] = evidence

    db = object()
    applied = await flow.apply_in_session(db, 32, workline_id=7)

    assert applied.disposition is BusinessEvidenceDisposition.APPLIED
    flow._source_racks.has_source_face.assert_awaited_once_with(db, 7, "RACK-1", "90")
    flow._drains.transport.assert_not_awaited()
    result.apply_return_in_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_drain_wms_response_is_validated_and_published_for_plan_wake():
    flow, evidences, *_ = _setup()
    evidence = _wms(31, InboundEvidenceKind.WMS_RESULT, "drain-1", {})
    evidence.operation = "workline.return_buffer.drain_rack_decide@v1"
    evidences.rows[31] = evidence
    flow._drain_reader = SimpleNamespace(
        read=AsyncMock(
            return_value=(
                SimpleNamespace(workline_code="LINE-1"),
                sdk.ReturnBufferDrainOutcome(sdk.ReturnBufferDrainReady((sdk.RackFaceSequence("R1", ("90",)),))),
            )
        )
    )
    applied = await flow.apply_in_session(object(), 31, workline_id=7)
    assert applied.disposition is BusinessEvidenceDisposition.APPLIED
    flow._drain_reader.read.assert_awaited_once()
