from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import wes_plugin_sdk as sdk

from src.app.device.models import CommandStatus
from src.app.execution.models import InboundEvidenceApplyStatus, WmsConfirmationStatus
from src.app.execution.services.wms_confirmation_service import WmsConfirmationAcceptance
from src.app.transport.contracts import (
    TRANSPORT_DEBUG_CALLER_WORKLINE_ID,
    MoveBinsRequest,
    MoveRackRequest,
    RotateRackRequest,
    TransportHandle,
)
from src.app.wms_adapter.outbound_picking.typed import encode_request as encode_prepare_request
from src.app.wms_integration.outbound_picking.models import PickingTaskStatus
from src.app.wms_integration.outbound_picking.services.picking_task_prepare import (
    PickingTaskPrepareNoopReason,
    PickingTaskPrepareResult,
)
from src.app.workline_integration_debug.contracts import (
    SORTING_3_SITE_CONFIGURATION,
    IntegrationDebugPhase,
    IntegrationDebugProfile,
    IntegrationTransportAction,
    IntegrationTransportActionKind,
)
from src.app.workline_integration_debug.models import IntegrationRun, IntegrationRunStep
from src.app.workline_integration_debug.service import (
    CreateIntegrationRun,
    IntegrationDebugConflict,
    IntegrationDebugContractError,
    IntegrationDebugService,
)
from src.app.workline_integration_debug.transport import build_transport_request


class _Transaction:
    async def __aenter__(self):  # type: ignore[no-untyped-def]
        return object()

    async def __aexit__(self, *_args: object) -> None:
        return None


class _Sessions:
    def __call__(self) -> _Transaction:
        return _Transaction()

    def begin(self) -> _Transaction:
        return _Transaction()


class _Repository:
    def __init__(self, run: IntegrationRun) -> None:
        self.run = run
        self.steps = []
        self.workline_for_update = False
        self.evidence = None
        self.confirmation = None
        self.prepare_confirmation = None
        self.response_evidence = None
        self.plan_resources = {"direct_picks": [], "bin_source_racks": []}
        self.picking_task = (
            SimpleNamespace(
                id=run.picking_task_id,
                task_id=run.task_id,
                status=PickingTaskStatus.EXECUTING,
                workline_id=run.workline_id,
                plan_blocked_evidence_id=None,
                last_applied_plan_revision=1,
            )
            if run is not None
            else None
        )

    async def get_run(self, _db, _run_id, *, for_update=False):  # type: ignore[no-untyped-def]
        return self.run

    async def get_step_by_client_request_id(self, _db, client_request_id, *, for_update=False):  # type: ignore[no-untyped-def]
        return next((step for step in self.steps if step.client_request_id == client_request_id), None)

    async def get_step_for_update(self, _db, _run_id, phase):  # type: ignore[no-untyped-def]
        return next((step for step in self.steps if step.phase == phase), None)

    async def next_ordinal(self, _db, _run_id):  # type: ignore[no-untyped-def]
        return len(self.steps)

    async def add_step(self, _db, step):  # type: ignore[no-untyped-def]
        self.steps.append(step)

    async def list_steps(self, _db, _run_id):  # type: ignore[no-untyped-def]
        return self.steps

    async def get_workline_by_code(self, _db, workline_code, *, for_update=False):  # type: ignore[no-untyped-def]
        self.workline_for_update = for_update
        return SimpleNamespace(id=3, line_code=workline_code)

    async def get_active_for_workline(self, _db, _workline_id, *, for_update=False):  # type: ignore[no-untyped-def]
        return None

    async def add_run(self, _db, run, steps):  # type: ignore[no-untyped-def]
        self.run = run
        self.steps.extend(steps)

    async def get_evidence_by_operation(  # type: ignore[no-untyped-def]
        self, _db, _operation, _operation_id, *, for_update=False
    ):
        return self.evidence

    async def get_confirmation(self, _db, _confirmation_id):  # type: ignore[no-untyped-def]
        return self.confirmation

    async def get_evidence(self, _db, _evidence_id):  # type: ignore[no-untyped-def]
        return self.response_evidence

    async def get_prepare_confirmation(self, _db, _picking_task_id):  # type: ignore[no-untyped-def]
        return self.prepare_confirmation

    async def get_picking_task(self, _db, _task_id, *, for_update=False):  # type: ignore[no-untyped-def]
        return self.picking_task

    async def list_plan_resources(self, _db, _picking_task_id):  # type: ignore[no-untyped-def]
        return self.plan_resources


@pytest.mark.parametrize(
    ("action", "request_type"),
    [
        (
            IntegrationTransportAction(
                kind=IntegrationTransportActionKind.MOVE_RACK,
                client_request_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4472",
                rack_id="RACK-01",
                source={"kind": "ZONE", "location_code": "STORAGE-01"},
                target={"kind": "RACK_POSITION", "location_code": "LINE3-RACK"},
                target_face="90",
                rcs_template_id="CTU01",
            ),
            MoveRackRequest,
        ),
        (
            IntegrationTransportAction(
                kind=IntegrationTransportActionKind.ROTATE_RACK,
                client_request_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4473",
                rack_id="RACK-01",
                source={"kind": "RACK_POSITION", "location_code": "LINE3-RACK"},
                target={},
                target_face="270",
                rcs_template_id="CTU02",
            ),
            RotateRackRequest,
        ),
        (
            IntegrationTransportAction(
                kind=IntegrationTransportActionKind.MOVE_BINS,
                client_request_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4474",
                rack_id="RACK-01",
                bin_code="BIN-001",
                source={"kind": "RACK_BIN_SLOT", "rack_id": "RACK-01", "rack_face": "90", "slot_id": "SLOT-01"},
                target={"kind": "HANDOFF_POSITION", "location_code": "LINE3-INFEED"},
                rcs_template_id="CTU01",
            ),
            MoveBinsRequest,
        ),
    ],
)
def test_selected_site_resources_map_to_one_existing_transport_request(
    action: IntegrationTransportAction,
    request_type: type,
) -> None:
    request = build_transport_request(action)

    assert type(request) is request_type
    assert request.client_request_id == action.client_request_id
    assert request.caller.workline_id == TRANSPORT_DEBUG_CALLER_WORKLINE_ID
    if isinstance(request, MoveRackRequest):
        assert request.rack_id == action.rack_id
        assert request.source.location_code == action.source["location_code"]
        assert request.target.location_code == action.target["location_code"]
        assert request.rcs_template_id.value == action.rcs_template_id
    elif isinstance(request, RotateRackRequest):
        assert request.rack_id == action.rack_id
        assert request.position.location_code == action.source["location_code"]
        assert request.target_face == action.target_face
        assert request.rcs_template_id.value == action.rcs_template_id
    else:
        assert len(request.moves) == 1
        assert request.moves[0].bin_code == action.bin_code
        assert request.moves[0].source.slot_id == action.source["slot_id"]  # type: ignore[union-attr]
        assert request.moves[0].target.location_code == action.target["location_code"]  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_create_run_scopes_exclusivity_to_resolved_workline_id() -> None:
    repository = _Repository(None)  # type: ignore[arg-type]
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        confirmations=AsyncMock(),  # type: ignore[arg-type]
        transport=AsyncMock(),  # type: ignore[arg-type]
        device_commands=AsyncMock(),  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )

    result = await service.create_run(
        CreateIntegrationRun(
            workline_code="sorting-3",
            profile=IntegrationDebugProfile.CONTRACT_SIMULATION,
            environment_label="integration",
            device_code="SIM-ECS-01",
        ),
        actor_id=42,
    )

    assert repository.run.active_scope == "WORKLINE:3"
    assert repository.workline_for_update is True
    assert result["workline_id"] == 3
    assert result["site_configuration"] == {
        "outbound_rcs_template": "CTU01",
        "return_rcs_template": "CTU03",
        "bin_rack_positions": ["KT16", "KT17"],
        "outbound_transfer_position": "OUT65",
        "return_zone_code": "WH05",
        "infeed_position": "CNV0301",
        "outfeed_position": "CNV0302",
        "ecs_endpoint_base_url": "http://10.24.209.26:8080/",
        "scan_device_codes": [
            "STATION_SCAN9",
            "STATION_SCAN10",
            "STATION_SCAN11",
            "STATION_SCAN12",
        ],
    }


@pytest.mark.asyncio
async def test_unstarted_run_can_be_closed_without_leaving_workline_scope_occupied() -> None:
    repository = _Repository(None)  # type: ignore[arg-type]
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        confirmations=AsyncMock(),  # type: ignore[arg-type]
        transport=AsyncMock(),  # type: ignore[arg-type]
        device_commands=AsyncMock(),  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )
    created = await service.create_run(
        CreateIntegrationRun(
            workline_code="sorting-3",
            profile=IntegrationDebugProfile.CONTRACT_SIMULATION,
            environment_label="integration",
            device_code="SIM-ECS-01",
        ),
        actor_id=42,
    )

    closed = await service.close_run(
        created["run_id"],
        wms_cleanup_confirmed=True,
        site_cleanup_confirmed=True,
        expected_version=created["version"],
        actor_id=42,
    )

    assert closed["status"] == "CLOSED_BY_OPERATOR"
    assert repository.run.active_scope is None


@pytest.mark.asyncio
async def test_completion_report_requires_current_phase_and_bound_completion_identity() -> None:
    run = IntegrationRun(
        run_id="run-report",
        workline_id=3,
        workline_code="sorting-3",
        scenario_key="manual_outbound_picking@v1",
        expected_plugin_key="manual_bin_processing",
        profile="CONTRACT_SIMULATION",
        environment_label="integration",
        operator_user_id=42,
        active_scope="WORKLINE:3",
        status="ACTIVE",
        current_phase="POINT2_RELEASE",
        task_id="PICK-001",
        bin_code="BIN-001",
        device_code="SIM-ECS-01",
        configuration_json={"admission_task_id": "PICK-001", "manual_bin_admission_result": "WORK_REQUIRED"},
    )
    repository = _Repository(run)
    repository.steps.append(
        IntegrationRunStep(
            run_id=run.run_id,
            ordinal=1,
            phase="WORK_COMPLETION",
            status="SUCCEEDED",
            operation="outbound.manual_bin.work_completed@v1",
            operation_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4477",
        )
    )
    repository.steps.append(
        IntegrationRunStep(
            run_id=run.run_id,
            ordinal=2,
            phase="POINT2_RELEASE",
            status="SUCCEEDED",
            result_summary_json={"simulated": True},
        )
    )
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        confirmations=AsyncMock(),  # type: ignore[arg-type]
        transport=AsyncMock(),  # type: ignore[arg-type]
        device_commands=AsyncMock(),  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )
    values = {
        "client_request_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4478",
        "completion_operation_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4477",
        "apply_revision": 1,
        "apply_result": "APPLIED",
        "reason_code": None,
        "occurred_at": 1788389999000,
        "expected_version": 0,
        "actor_id": 42,
    }

    with pytest.raises(IntegrationDebugConflict, match="不能上报 APPLIED"):
        await service.send_completion_apply_report("run-report", **values)  # type: ignore[arg-type]

    run.current_phase = "COMPLETION_REPORT"
    values["completion_operation_id"] = "019f12d0-58d7-7b4d-a23a-1b90aa5d4499"
    with pytest.raises(IntegrationDebugContractError, match="completion_operation_id"):
        await service.send_completion_apply_report("run-report", **values)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_completion_report_phase_cannot_be_skipped_by_manual_confirmation() -> None:
    run = IntegrationRun(
        run_id="run-report",
        workline_id=3,
        workline_code="sorting-3",
        scenario_key="manual_outbound_picking@v1",
        expected_plugin_key="manual_bin_processing",
        profile="CONTRACT_SIMULATION",
        environment_label="integration",
        operator_user_id=42,
        active_scope="WORKLINE:3",
        status="ACTIVE",
        current_phase="COMPLETION_REPORT",
        device_code="SIM-ECS-01",
    )
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=_Repository(run),  # type: ignore[arg-type]
        confirmations=AsyncMock(),  # type: ignore[arg-type]
        transport=AsyncMock(),  # type: ignore[arg-type]
        device_commands=AsyncMock(),  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )

    with pytest.raises(IntegrationDebugConflict, match="不能人工确认推进"):
        await service.confirm_current_phase(
            "run-report",
            note="现场已确认",
            expected_version=0,
            actor_id=42,
        )


@pytest.mark.asyncio
async def test_completion_binding_rejects_completed_at_before_point2_scan() -> None:
    run = IntegrationRun(
        run_id="run-completion",
        workline_id=3,
        workline_code="sorting-3",
        scenario_key="manual_outbound_picking@v1",
        expected_plugin_key="manual_bin_processing",
        profile="CONTRACT_SIMULATION",
        environment_label="integration",
        operator_user_id=42,
        active_scope="WORKLINE:3",
        status="WAITING_EXTERNAL",
        current_phase="WORK_COMPLETION",
        task_id="PICK-001",
        bin_code="BIN-001",
        device_code="SIM-ECS-01",
        configuration_json={"point2_scanned_at": 2000, "admission_task_id": "PICK-001"},
    )
    repository = _Repository(run)
    repository.evidence = SimpleNamespace(
        apply_status=InboundEvidenceApplyStatus.PENDING,
        normalized_payload={
            "data": {
                "task_id": "PICK-001",
                "bin_code": "BIN-001",
                "result": "NORMAL",
                "completed_at": 1999,
            }
        },
    )
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        confirmations=AsyncMock(),  # type: ignore[arg-type]
        transport=AsyncMock(),  # type: ignore[arg-type]
        device_commands=AsyncMock(),  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )

    with pytest.raises(IntegrationDebugContractError, match="completed_at"):
        await service.bind_work_completion(
            "run-completion",
            operation_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4477",
            expected_version=0,
            actor_id=42,
        )


@pytest.mark.asyncio
async def test_completion_binding_rejects_ignored_evidence() -> None:
    run = IntegrationRun(
        run_id="run-completion-rejected",
        workline_id=3,
        workline_code="sorting-3",
        scenario_key="manual_outbound_picking@v1",
        expected_plugin_key="manual_bin_processing",
        profile="CONTRACT_SIMULATION",
        environment_label="integration",
        operator_user_id=42,
        active_scope="WORKLINE:3",
        status="WAITING_EXTERNAL",
        current_phase="WORK_COMPLETION",
        task_id="PICK-001",
        bin_code="BIN-001",
        device_code="SIM-ECS-01",
        configuration_json={"point2_scanned_at": 2000, "admission_task_id": "PICK-001"},
    )
    repository = _Repository(run)
    repository.evidence = SimpleNamespace(
        apply_status=InboundEvidenceApplyStatus.IGNORED,
        normalized_payload={
            "data": {
                "task_id": "PICK-001",
                "bin_code": "BIN-001",
                "result": "BROKEN",
                "completed_at": 2001,
            }
        },
    )
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        confirmations=AsyncMock(),  # type: ignore[arg-type]
        transport=AsyncMock(),  # type: ignore[arg-type]
        device_commands=AsyncMock(),  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )

    with pytest.raises(IntegrationDebugContractError, match="完成决定"):
        await service.bind_work_completion(
            run.run_id,
            operation_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4477",
            expected_version=0,
            actor_id=42,
        )


def test_work_required_freezes_wms_returned_task_and_reconciling_stays_blocked() -> None:
    run = IntegrationRun(
        run_id="run-decision",
        workline_id=3,
        workline_code="sorting-3",
        scenario_key="manual_outbound_picking@v1",
        expected_plugin_key="manual_bin_processing",
        profile="CONTRACT_SIMULATION",
        environment_label="integration",
        operator_user_id=42,
        active_scope="WORKLINE:3",
        status="WAITING_EXTERNAL",
        current_phase="WORK_ADMISSION",
        task_id="PICK-ORIGINAL",
        bin_code="BIN-001",
        device_code="SIM-ECS-01",
    )
    admission = IntegrationRunStep(
        run_id=run.run_id,
        ordinal=1,
        phase="WORK_ADMISSION",
        status="WAITING",
        operation="outbound.manual_bin.work_admission_decide@v1",
    )

    IntegrationDebugService._advance_completed_wms_action(
        run,
        admission,
        response_result="WORK_REQUIRED",
        response_data={"task_id": "PICK-ACTUAL"},
    )

    assert run.configuration_json["admission_task_id"] == "PICK-ACTUAL"
    assert run.configuration_json["manual_bin_admission_result"] == "WORK_REQUIRED"
    report = IntegrationRunStep(
        run_id=run.run_id,
        ordinal=2,
        phase="COMPLETION_REPORT",
        status="WAITING",
        operation="outbound.manual_bin.completion_apply_report@v1",
        request_summary_json={"apply_result": "RECONCILING", "reason_code": "POINT2_BINDING_MISMATCH"},
    )
    IntegrationDebugService._advance_completed_wms_action(
        run,
        report,
        response_result="RECORDED",
        response_data={},
    )
    assert run.status == "NEEDS_ATTENTION"
    assert run.attention_code == "POINT2_BINDING_MISMATCH"


@pytest.mark.asyncio
async def test_refreshing_completed_historical_wms_step_does_not_rewind_phase() -> None:
    run = IntegrationRun(
        run_id="run-history",
        workline_id=3,
        workline_code="sorting-3",
        scenario_key="manual_outbound_picking@v1",
        expected_plugin_key="manual_bin_processing",
        profile="CONTRACT_SIMULATION",
        environment_label="integration",
        operator_user_id=42,
        active_scope="WORKLINE:3",
        status="ACTIVE",
        current_phase="POINT3_ROUTE",
        task_id="PICK-001",
        bin_code="BIN-001",
        device_code="SIM-ECS-01",
    )
    repository = _Repository(run)
    repository.steps.append(
        IntegrationRunStep(
            run_id=run.run_id,
            ordinal=1,
            phase="TASK_PREPARE",
            status="SUCCEEDED",
            client_request_id="prepare-request",
            operation="outbound.picking_task.prepare@v1",
            wms_confirmation_id=9,
        )
    )
    repository.confirmation = SimpleNamespace(
        status=WmsConfirmationStatus.COMPLETED,
        response_evidence_id=None,
        response_result="PREPARE_ACCEPTED",
    )
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        confirmations=AsyncMock(),  # type: ignore[arg-type]
        transport=AsyncMock(),  # type: ignore[arg-type]
        device_commands=AsyncMock(),  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )

    await service.refresh_wms_action(
        run.run_id,
        client_request_id="prepare-request",
        expected_version=0,
        actor_id=42,
    )

    assert run.current_phase == "POINT3_ROUTE"


@pytest.mark.asyncio
async def test_prepare_retry_recovers_the_existing_confirmation() -> None:
    run = IntegrationRun(
        run_id="run-prepare-recovery",
        workline_id=3,
        workline_code="sorting-3",
        scenario_key="manual_outbound_picking@v1",
        expected_plugin_key="manual_bin_processing",
        profile="CONTRACT_SIMULATION",
        environment_label="integration",
        operator_user_id=42,
        active_scope="WORKLINE:3",
        status="ACTIVE",
        current_phase="TASK_PREPARE",
        picking_task_id=19,
        task_id="PICK-001",
        device_code="SIM-ECS-01",
    )
    repository = _Repository(run)
    repository.picking_task = SimpleNamespace(
        id=19,
        task_id="PICK-001",
        status=PickingTaskStatus.PREPARING,
        workline_id=3,
    )
    repository.prepare_confirmation = SimpleNamespace(
        id=27,
        operation_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4490",
        request_payload=encode_prepare_request(
            sdk.wms_operations.outbound_picking_task_prepare(
                operation_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4490",
                task_id="PICK-001",
                work_line_code="sorting-3",
            ),
            timestamp=1_788_390_000_000,
        ),
    )
    prepare = AsyncMock()
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        confirmations=AsyncMock(),  # type: ignore[arg-type]
        transport=AsyncMock(),  # type: ignore[arg-type]
        device_commands=AsyncMock(),  # type: ignore[arg-type]
        prepare=prepare,  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )

    result = await service.send_task_prepare(
        run.run_id,
        client_request_id="prepare-retry",
        expected_version=0,
        actor_id=42,
    )

    prepare.prepare_next_for_workline.assert_not_awaited()
    assert result["steps"][0]["wms_confirmation_id"] == 27
    assert result["steps"][0]["operation_id"] == repository.prepare_confirmation.operation_id


@pytest.mark.asyncio
async def test_prepare_retry_recovers_confirmation_after_plan_already_started_execution() -> None:
    run = IntegrationRun(
        run_id="run-prepare-executing-recovery",
        workline_id=3,
        workline_code="sorting-3",
        scenario_key="manual_outbound_picking@v1",
        expected_plugin_key="manual_bin_processing",
        profile="CONTRACT_SIMULATION",
        environment_label="integration",
        operator_user_id=42,
        active_scope="WORKLINE:3",
        status="ACTIVE",
        current_phase="TASK_PREPARE",
        picking_task_id=19,
        task_id="PICK-001",
    )
    repository = _Repository(run)
    repository.picking_task = SimpleNamespace(
        id=19,
        task_id="PICK-001",
        status=PickingTaskStatus.EXECUTING,
        workline_id=3,
    )
    repository.prepare_confirmation = SimpleNamespace(
        id=27,
        operation_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4490",
        request_payload=encode_prepare_request(
            sdk.wms_operations.outbound_picking_task_prepare(
                operation_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4490",
                task_id="PICK-001",
                work_line_code="sorting-3",
            ),
            timestamp=1_788_390_000_000,
        ),
    )
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        confirmations=AsyncMock(),  # type: ignore[arg-type]
        transport=AsyncMock(),  # type: ignore[arg-type]
        device_commands=AsyncMock(),  # type: ignore[arg-type]
        prepare=AsyncMock(),  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )

    result = await service.send_task_prepare(
        run.run_id,
        client_request_id="prepare-executing-recovery",
        expected_version=0,
        actor_id=42,
    )

    assert result["steps"][0]["wms_confirmation_id"] == 27


@pytest.mark.asyncio
async def test_prepare_retry_rejects_confirmation_bound_to_another_workline() -> None:
    run = IntegrationRun(
        run_id="run-prepare-cross-line",
        workline_id=3,
        workline_code="sorting-3",
        scenario_key="manual_outbound_picking@v1",
        expected_plugin_key="manual_bin_processing",
        profile="CONTRACT_SIMULATION",
        environment_label="integration",
        operator_user_id=42,
        active_scope="WORKLINE:3",
        status="ACTIVE",
        current_phase="TASK_PREPARE",
        picking_task_id=19,
        task_id="PICK-001",
        device_code="SIM-ECS-01",
    )
    repository = _Repository(run)
    repository.picking_task = SimpleNamespace(
        id=19,
        task_id="PICK-001",
        status=PickingTaskStatus.PREPARING,
        workline_id=4,
    )
    repository.prepare_confirmation = SimpleNamespace(
        id=27,
        operation_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4490",
        request_payload={"data": {"task_id": "PICK-001", "work_line_code": "sorting-4"}},
    )
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        confirmations=AsyncMock(),  # type: ignore[arg-type]
        transport=AsyncMock(),  # type: ignore[arg-type]
        device_commands=AsyncMock(),  # type: ignore[arg-type]
        prepare=AsyncMock(),  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )

    with pytest.raises(IntegrationDebugConflict, match="其它 WorkLine"):
        await service.send_task_prepare(
            run.run_id,
            client_request_id="prepare-cross-line",
            expected_version=0,
            actor_id=42,
        )


@pytest.mark.asyncio
async def test_prepare_non_head_selection_returns_run_to_bind_task() -> None:
    run = IntegrationRun(
        run_id="run-prepare-non-head",
        workline_id=3,
        workline_code="sorting-3",
        scenario_key="manual_outbound_picking@v1",
        expected_plugin_key="manual_bin_processing",
        profile="CONTRACT_SIMULATION",
        environment_label="integration",
        operator_user_id=42,
        active_scope="WORKLINE:3",
        status="ACTIVE",
        current_phase="TASK_PREPARE",
        picking_task_id=19,
        task_id="PICK-002",
        issued_operation_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4488",
        device_code="SIM-ECS-01",
    )
    repository = _Repository(run)
    repository.picking_task = SimpleNamespace(
        id=19,
        task_id="PICK-002",
        status=PickingTaskStatus.QUEUED,
        workline_id=None,
    )
    repository.steps.append(
        IntegrationRunStep(
            run_id=run.run_id,
            ordinal=0,
            phase="BIND_TASK",
            status="SUCCEEDED",
            result_summary_json={"task_id": "PICK-002"},
        )
    )
    prepare = AsyncMock()
    prepare.prepare_next_for_workline.return_value = PickingTaskPrepareResult(
        False,
        PickingTaskPrepareNoopReason.SELECTED_TASK_NOT_NEXT,
    )
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        confirmations=AsyncMock(),  # type: ignore[arg-type]
        transport=AsyncMock(),  # type: ignore[arg-type]
        device_commands=AsyncMock(),  # type: ignore[arg-type]
        prepare=prepare,  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )

    result = await service.send_task_prepare(
        run.run_id,
        client_request_id="prepare-non-head",
        expected_version=0,
        actor_id=42,
    )

    assert result["current_phase"] == "BIND_TASK"
    assert result["status"] == "WAITING_TASK"
    assert result["task_id"] is None
    assert run.picking_task_id is None
    assert repository.steps[0].status == "PENDING"
    assert repository.steps[0].result_summary_json == {}


@pytest.mark.asyncio
async def test_work_admission_rejects_a_new_identity_while_the_previous_request_is_open() -> None:
    run = IntegrationRun(
        run_id="run-admission-open",
        workline_id=3,
        workline_code="sorting-3",
        scenario_key="manual_outbound_picking@v1",
        expected_plugin_key="manual_bin_processing",
        profile="CONTRACT_SIMULATION",
        environment_label="integration",
        operator_user_id=42,
        active_scope="WORKLINE:3",
        status="WAITING_EXTERNAL",
        current_phase="WORK_ADMISSION",
        task_id="PICK-001",
        bin_code="BIN-001",
        configuration_json={"point2_scanned_at": 1_788_390_000_000},
    )
    repository = _Repository(run)
    repository.steps.append(
        IntegrationRunStep(
            run_id=run.run_id,
            ordinal=1,
            phase="WORK_ADMISSION",
            status="WAITING",
            client_request_id="admission-original",
            operation="outbound.manual_bin.work_admission_decide@v1",
            operation_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4490",
        )
    )
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        confirmations=AsyncMock(),  # type: ignore[arg-type]
        transport=AsyncMock(),  # type: ignore[arg-type]
        device_commands=AsyncMock(),  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )

    with pytest.raises(IntegrationDebugConflict, match="未闭合"):
        await service.send_work_admission(
            run.run_id,
            client_request_id="admission-replacement",
            expected_version=0,
            actor_id=42,
        )


@pytest.mark.asyncio
async def test_refresh_plan_resources_updates_later_revision_without_rewinding_the_current_phase() -> None:
    run = IntegrationRun(
        run_id="run-plan-refresh",
        workline_id=3,
        workline_code="sorting-3",
        scenario_key="manual_outbound_picking@v1",
        expected_plugin_key="manual_bin_processing",
        profile="CONTRACT_SIMULATION",
        environment_label="integration",
        operator_user_id=42,
        active_scope="WORKLINE:3",
        status="ACTIVE",
        current_phase="BIN_INBOUND_BATCH",
        picking_task_id=19,
        task_id="PICK-001",
        configuration_json={"plan_resources": {"plan_revision": 1}},
    )
    repository = _Repository(run)
    repository.picking_task = SimpleNamespace(
        id=19,
        task_id="PICK-001",
        status=PickingTaskStatus.EXECUTING,
        workline_id=3,
        plan_blocked_evidence_id=None,
        last_applied_plan_revision=2,
        target_rack_id="TARGET-01",
        target_rack_face="0",
    )
    repository.plan_resources = {
        "direct_picks": [],
        "bin_source_racks": [{"rack_id": "RACK-02", "rack_face": "90", "plan_revision": 2}],
    }
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        confirmations=AsyncMock(),  # type: ignore[arg-type]
        transport=AsyncMock(),  # type: ignore[arg-type]
        device_commands=AsyncMock(),  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )

    result = await service.refresh_plan_resources(run.run_id, expected_version=0, actor_id=42)

    assert result["current_phase"] == "BIN_INBOUND_BATCH"
    assert result["plan_resources"]["plan_revision"] == 2
    assert result["plan_resources"]["bin_source_racks"][0]["rack_id"] == "RACK-02"


@pytest.mark.asyncio
async def test_return_transport_requires_ready_decision_for_the_same_rack_and_destination() -> None:
    run = IntegrationRun(
        run_id="run-return-rack",
        workline_id=3,
        workline_code="sorting-3",
        scenario_key="manual_outbound_picking@v1",
        expected_plugin_key="manual_bin_processing",
        profile="CONTRACT_SIMULATION",
        environment_label="integration",
        operator_user_id=42,
        active_scope="WORKLINE:3",
        status="ACTIVE",
        current_phase="RACK_DEPARTURE",
        task_id="PICK-001",
        picking_task_id=101,
        device_code="SIM-ECS-01",
        configuration_json={
            "plan_resources": {
                "target_rack": {"rack_id": "RACK-01"},
                "direct_picks": [],
                "bin_source_racks": [],
            }
        },
    )
    repository = _Repository(run)
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        confirmations=AsyncMock(),  # type: ignore[arg-type]
        transport=AsyncMock(),  # type: ignore[arg-type]
        device_commands=AsyncMock(),  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )
    action = IntegrationTransportAction(
        kind=IntegrationTransportActionKind.MOVE_RACK,
        client_request_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4491",
        rack_id="RACK-01",
        source={"kind": "RACK", "location_code": "RACK-01"},
        target={"kind": "ZONE", "location_code": "WH05"},
        rcs_template_id="CTU03",
    )

    with pytest.raises(IntegrationDebugContractError, match="departure_decide READY"):
        await service.create_transport_action(run.run_id, action=action, expected_version=0, actor_id=42)

    IntegrationDebugService._advance_departure(
        run,
        IntegrationRunStep(
            run_id=run.run_id,
            ordinal=1,
            phase="RACK_DEPARTURE",
            status="WAITING",
            request_summary_json={"rack_id": "RACK-01"},
        ),
        "READY",
        {"rack_destination": {"type": "RACK_POSITION", "location_code": "WH05"}},
    )
    assert run.configuration_json["rack_destination"] == {"kind": "ZONE", "location_code": "WH05"}
    result = await service.create_transport_action(run.run_id, action=action, expected_version=0, actor_id=42)

    assert result["steps"][0]["status"] == "SUCCEEDED"


@pytest.mark.asyncio
async def test_point2_release_confirmation_keeps_evidence_pending_until_report_is_frozen() -> None:
    run = IntegrationRun(
        run_id="run-release",
        workline_id=3,
        workline_code="sorting-3",
        scenario_key="manual_outbound_picking@v1",
        expected_plugin_key="manual_bin_processing",
        profile="CONTRACT_SIMULATION",
        environment_label="integration",
        operator_user_id=42,
        active_scope="WORKLINE:3",
        status="ACTIVE",
        current_phase="POINT2_RELEASE",
        task_id="PICK-001",
        bin_code="BIN-001",
        device_code="SIM-ECS-01",
        configuration_json={"manual_bin_admission_result": "WORK_REQUIRED"},
    )
    repository = _Repository(run)
    repository.evidence = SimpleNamespace(
        apply_status=InboundEvidenceApplyStatus.PENDING,
        processed_at=None,
    )
    repository.steps.extend(
        [
            IntegrationRunStep(
                run_id=run.run_id,
                ordinal=1,
                phase="WORK_COMPLETION",
                status="SUCCEEDED",
                operation="outbound.manual_bin.work_completed@v1",
                operation_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4477",
            ),
            IntegrationRunStep(
                run_id=run.run_id,
                ordinal=2,
                phase="POINT2_RELEASE",
                status="SUCCEEDED",
                result_summary_json={"simulated": True},
            ),
        ]
    )
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        confirmations=AsyncMock(),  # type: ignore[arg-type]
        transport=AsyncMock(),  # type: ignore[arg-type]
        device_commands=AsyncMock(),  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )

    result = await service.confirm_current_phase(
        run.run_id,
        note="point2 已放行",
        expected_version=0,
        actor_id=42,
    )

    assert result["current_phase"] == "COMPLETION_REPORT"
    assert repository.evidence.apply_status == InboundEvidenceApplyStatus.PENDING
    assert repository.evidence.processed_at is None


@pytest.mark.asyncio
async def test_point2_scan_rejects_invalid_bin_before_advancing_the_run() -> None:
    run = IntegrationRun(
        run_id="run-invalid-bin",
        workline_id=3,
        workline_code="sorting-3",
        scenario_key="manual_outbound_picking@v1",
        expected_plugin_key="manual_bin_processing",
        profile="CONTRACT_SIMULATION",
        environment_label="integration",
        operator_user_id=42,
        active_scope="WORKLINE:3",
        status="ACTIVE",
        current_phase="POINT2_SCAN",
    )
    repository = _Repository(run)
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        confirmations=AsyncMock(),  # type: ignore[arg-type]
        transport=AsyncMock(),  # type: ignore[arg-type]
        device_commands=AsyncMock(),  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )

    with pytest.raises(IntegrationDebugContractError, match="扫码 Bin"):
        await service.record_point2_scan(
            run.run_id,
            bin_code="BIN 001",
            scanned_at=1,
            expected_version=0,
            actor_id=42,
        )

    assert run.current_phase == IntegrationDebugPhase.POINT2_SCAN
    assert run.bin_code is None
    assert repository.steps == []


@pytest.mark.parametrize(
    ("apply_result", "reason_code", "phase", "run_status", "expected_status"),
    [
        (
            "APPLIED",
            None,
            IntegrationDebugPhase.COMPLETION_REPORT,
            "ACTIVE",
            InboundEvidenceApplyStatus.APPLIED,
        ),
        (
            "RECONCILING",
            "DEVICE_COMMAND_IDENTITY_CONFLICT",
            IntegrationDebugPhase.POINT2_RELEASE,
            "NEEDS_ATTENTION",
            InboundEvidenceApplyStatus.RECONCILING,
        ),
    ],
)
@pytest.mark.asyncio
async def test_completion_report_freezes_confirmation_and_evidence_state_in_one_transaction(
    apply_result: str,
    reason_code: str | None,
    phase: IntegrationDebugPhase,
    run_status: str,
    expected_status: InboundEvidenceApplyStatus,
) -> None:
    run = IntegrationRun(
        run_id="run-report-atomic",
        workline_id=3,
        workline_code="sorting-3",
        scenario_key="manual_outbound_picking@v1",
        expected_plugin_key="manual_bin_processing",
        profile="CONTRACT_SIMULATION",
        environment_label="integration",
        operator_user_id=42,
        active_scope="WORKLINE:3",
        status=run_status,
        current_phase=phase,
        task_id="PICK-001",
        picking_task_id=101,
        bin_code="BIN-001",
        configuration_json={"admission_task_id": "PICK-001", "manual_bin_admission_result": "WORK_REQUIRED"},
    )
    repository = _Repository(run)
    repository.evidence = SimpleNamespace(
        apply_status=InboundEvidenceApplyStatus.PENDING,
        processed_at=None,
    )
    completion_operation_id = "019f12d0-58d7-7b4d-a23a-1b90aa5d4477"
    repository.steps.extend(
        [
            IntegrationRunStep(
                run_id=run.run_id,
                ordinal=1,
                phase="WORK_COMPLETION",
                status="SUCCEEDED",
                operation="outbound.manual_bin.work_completed@v1",
                operation_id=completion_operation_id,
            ),
            IntegrationRunStep(
                run_id=run.run_id,
                ordinal=2,
                phase="POINT2_RELEASE",
                status="SUCCEEDED",
                result_summary_json={"simulated": True},
            ),
        ]
    )
    confirmations = AsyncMock()
    confirmations.create_or_get.return_value = WmsConfirmationAcceptance(SimpleNamespace(id=9), duplicate=False)
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        confirmations=confirmations,
        transport=AsyncMock(),  # type: ignore[arg-type]
        device_commands=AsyncMock(),  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )

    result = await service.send_completion_apply_report(
        run.run_id,
        client_request_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4478",
        completion_operation_id=completion_operation_id,
        apply_revision=1,
        apply_result=apply_result,
        reason_code=reason_code,
        occurred_at=1_788_389_999_000,
        expected_version=0,
        actor_id=42,
    )

    assert result["status"] == "WAITING_EXTERNAL"
    assert repository.evidence.apply_status == expected_status
    assert repository.evidence.processed_at is not None
    confirmations.create_or_get.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_work_release_skips_completion_evidence_and_report() -> None:
    run = IntegrationRun(
        run_id="run-no-work",
        workline_id=3,
        workline_code="sorting-3",
        scenario_key="manual_outbound_picking@v1",
        expected_plugin_key="manual_bin_processing",
        profile="CONTRACT_SIMULATION",
        environment_label="integration",
        operator_user_id=42,
        active_scope="WORKLINE:3",
        status="ACTIVE",
        current_phase="POINT2_RELEASE",
        bin_code="BIN-001",
        device_code="SIM-ECS-01",
        configuration_json={"manual_bin_admission_result": "NO_WORK"},
    )
    repository = _Repository(run)
    repository.steps.append(
        IntegrationRunStep(
            run_id=run.run_id,
            ordinal=1,
            phase="POINT2_RELEASE",
            status="SUCCEEDED",
            result_summary_json={"simulated": True},
        )
    )
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        confirmations=AsyncMock(),  # type: ignore[arg-type]
        transport=AsyncMock(),  # type: ignore[arg-type]
        device_commands=AsyncMock(),  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )

    result = await service.confirm_current_phase(
        run.run_id,
        note="NO_WORK 已创建放行模拟动作",
        expected_version=0,
        actor_id=42,
    )

    assert result["current_phase"] == "POINT3_ROUTE"


@pytest.mark.asyncio
async def test_point3_ng_route_skips_return_buffer_and_continues_with_rack_departure() -> None:
    run = IntegrationRun(
        run_id="run-point3-ng",
        workline_id=3,
        workline_code="sorting-3",
        scenario_key="manual_outbound_picking@v1",
        expected_plugin_key="manual_bin_processing",
        profile="CONTRACT_SIMULATION",
        environment_label="integration",
        operator_user_id=42,
        active_scope="WORKLINE:3",
        status="ACTIVE",
        current_phase="POINT3_ROUTE",
        task_id="PICK-001",
        bin_code="BIN-001",
        device_code="SIM-ECS-01",
        configuration_json={"manual_bin_admission_result": "WORK_REQUIRED"},
    )
    repository = _Repository(run)
    repository.steps.extend(
        [
            IntegrationRunStep(
                run_id=run.run_id,
                ordinal=1,
                phase="WORK_COMPLETION",
                status="SUCCEEDED",
                operation="outbound.manual_bin.work_completed@v1",
                result_summary_json={"result": "NG"},
            ),
            IntegrationRunStep(
                run_id=run.run_id,
                ordinal=2,
                phase="POINT3_ROUTE",
                status="SUCCEEDED",
                result_summary_json={"simulated": True},
            ),
        ]
    )
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        confirmations=AsyncMock(),  # type: ignore[arg-type]
        transport=AsyncMock(),  # type: ignore[arg-type]
        device_commands=AsyncMock(),  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )

    result = await service.confirm_current_phase(
        run.run_id,
        note="NG 已从 point3 左移",
        expected_version=0,
        actor_id=42,
    )

    assert result["current_phase"] == "RACK_DEPARTURE"


@pytest.mark.asyncio
async def test_full_site_release_cannot_apply_evidence_without_device_command() -> None:
    run = IntegrationRun(
        run_id="run-release-no-command",
        workline_id=3,
        workline_code="sorting-3",
        scenario_key="manual_outbound_picking@v1",
        expected_plugin_key="manual_bin_processing",
        profile="FULL_SITE_INTEGRATION",
        environment_label="integration",
        operator_user_id=42,
        active_scope="WORKLINE:3",
        status="ACTIVE",
        current_phase="POINT2_RELEASE",
        task_id="PICK-001",
        bin_code="BIN-001",
        device_code="STATION_SCAN9",
        configuration_json={"manual_bin_admission_result": "WORK_REQUIRED"},
    )
    repository = _Repository(run)
    repository.evidence = SimpleNamespace(
        apply_status=InboundEvidenceApplyStatus.PENDING,
        processed_at=None,
    )
    repository.steps.extend(
        [
            IntegrationRunStep(
                run_id=run.run_id,
                ordinal=1,
                phase="WORK_COMPLETION",
                status="SUCCEEDED",
                operation="outbound.manual_bin.work_completed@v1",
                operation_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4477",
            ),
            IntegrationRunStep(
                run_id=run.run_id,
                ordinal=2,
                phase="POINT2_RELEASE",
                status="SUCCEEDED",
                result_summary_json={"operator_confirmation": "现场口头确认"},
            ),
        ]
    )
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        confirmations=AsyncMock(),  # type: ignore[arg-type]
        transport=AsyncMock(),  # type: ignore[arg-type]
        device_commands=AsyncMock(),  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )

    with pytest.raises(IntegrationDebugConflict, match="释放 DeviceCommand"):
        await service.confirm_current_phase(
            run.run_id,
            note="缺少命令不能推进",
            expected_version=0,
            actor_id=42,
        )

    assert repository.evidence.apply_status == InboundEvidenceApplyStatus.PENDING


@pytest.mark.asyncio
async def test_full_site_point3_cannot_advance_while_device_command_is_not_succeeded() -> None:
    run = IntegrationRun(
        run_id="run-point3-command-waiting",
        workline_id=3,
        workline_code="sorting-3",
        scenario_key="manual_outbound_picking@v1",
        expected_plugin_key="manual_bin_processing",
        profile="FULL_SITE_INTEGRATION",
        environment_label="integration",
        operator_user_id=42,
        active_scope="WORKLINE:3",
        status="WAITING_EXTERNAL",
        current_phase="POINT3_ROUTE",
        bin_code="BIN-001",
        device_code="STATION_SCAN9",
        configuration_json={"manual_bin_admission_result": "NO_WORK"},
    )
    repository = _Repository(run)
    repository.steps.append(
        IntegrationRunStep(
            run_id=run.run_id,
            ordinal=1,
            phase="POINT3_ROUTE",
            status="WAITING",
            device_command_code="CMD-001",
            request_summary_json={"task_type": "MOVE_FORWARD"},
        )
    )
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        confirmations=AsyncMock(),  # type: ignore[arg-type]
        transport=AsyncMock(),  # type: ignore[arg-type]
        device_commands=AsyncMock(),  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )

    with pytest.raises(IntegrationDebugConflict, match="SUCCEEDED"):
        await service.confirm_current_phase(
            run.run_id,
            note="命令尚未完成",
            expected_version=0,
            actor_id=42,
        )


@pytest.mark.asyncio
async def test_refresh_device_action_copies_succeeded_terminal_state_to_the_run_step() -> None:
    run = IntegrationRun(
        run_id="run-device-refresh",
        workline_id=3,
        workline_code="sorting-3",
        scenario_key="manual_outbound_picking@v1",
        expected_plugin_key="manual_bin_processing",
        profile="FULL_SITE_INTEGRATION",
        environment_label="integration",
        operator_user_id=42,
        active_scope="WORKLINE:3",
        status="WAITING_EXTERNAL",
        current_phase="POINT3_ROUTE",
        bin_code="BIN-001",
    )
    repository = _Repository(run)
    repository.steps.append(
        IntegrationRunStep(
            run_id=run.run_id,
            ordinal=1,
            phase="POINT3_ROUTE",
            status="WAITING",
            client_request_id="device-refresh-1",
            device_command_code="CMD-001",
        )
    )
    commands = AsyncMock()
    commands.get_command_snapshot.return_value = SimpleNamespace(
        command_code="CMD-001",
        status=CommandStatus.SUCCEEDED,
        failure_code=None,
        reconciliation_reason=None,
    )
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        confirmations=AsyncMock(),  # type: ignore[arg-type]
        transport=AsyncMock(),  # type: ignore[arg-type]
        device_commands=commands,  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )

    result = await service.refresh_device_action(
        run.run_id,
        client_request_id="device-refresh-1",
        expected_version=0,
        actor_id=42,
    )

    assert result["steps"][0]["status"] == "SUCCEEDED"
    assert result["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_refresh_historical_device_action_does_not_reopen_completed_run() -> None:
    run = IntegrationRun(
        run_id="run-device-history",
        workline_id=3,
        workline_code="sorting-3",
        scenario_key="manual_outbound_picking@v1",
        expected_plugin_key="manual_bin_processing",
        profile="FULL_SITE_INTEGRATION",
        environment_label="integration",
        operator_user_id=42,
        active_scope="WORKLINE:3",
        status="COMPLETED",
        current_phase="CLEANUP",
    )
    repository = _Repository(run)
    repository.steps.append(
        IntegrationRunStep(
            run_id=run.run_id,
            ordinal=1,
            phase="POINT3_ROUTE",
            status="WAITING",
            client_request_id="device-history-1",
            device_command_code="CMD-001",
        )
    )
    commands = AsyncMock()
    commands.get_command_snapshot.return_value = SimpleNamespace(
        status=CommandStatus.SUCCEEDED,
        failure_code=None,
        reconciliation_reason=None,
    )
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        confirmations=AsyncMock(),  # type: ignore[arg-type]
        transport=AsyncMock(),  # type: ignore[arg-type]
        device_commands=commands,  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )

    result = await service.refresh_device_action(
        run.run_id,
        client_request_id="device-history-1",
        expected_version=0,
        actor_id=42,
    )

    assert result["steps"][0]["status"] == "SUCCEEDED"
    assert result["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_refresh_historical_transport_action_does_not_clear_current_attention() -> None:
    run = IntegrationRun(
        run_id="run-transport-history",
        workline_id=3,
        workline_code="sorting-3",
        scenario_key="manual_outbound_picking@v1",
        expected_plugin_key="manual_bin_processing",
        profile="FULL_SITE_INTEGRATION",
        environment_label="integration",
        operator_user_id=42,
        active_scope="WORKLINE:3",
        status="NEEDS_ATTENTION",
        current_phase="POINT3_ROUTE",
        attention_code="CURRENT_DEVICE_FAILED",
    )
    repository = _Repository(run)
    repository.steps.append(
        IntegrationRunStep(
            run_id=run.run_id,
            ordinal=1,
            phase="RACK_TRANSPORT",
            status="WAITING",
            client_request_id="transport-history-1",
            transport_task_id="TRANSPORT-001",
        )
    )
    transport = AsyncMock()
    transport.get_task_snapshot.return_value = SimpleNamespace(
        status="SUCCEEDED",
        reason_code=None,
        result={"final_position": "KT16"},
    )
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        confirmations=AsyncMock(),  # type: ignore[arg-type]
        transport=transport,  # type: ignore[arg-type]
        device_commands=AsyncMock(),  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )

    result = await service.refresh_transport_action(
        run.run_id,
        client_request_id="transport-history-1",
        expected_version=0,
        actor_id=42,
    )

    assert result["steps"][0]["status"] == "SUCCEEDED"
    assert result["status"] == "NEEDS_ATTENTION"
    assert result["attention_code"] == "CURRENT_DEVICE_FAILED"


@pytest.mark.asyncio
async def test_full_site_retry_reuses_the_single_transport_task_identity() -> None:
    run = IntegrationRun(
        run_id="run-1",
        workline_id=3,
        workline_code="sorting-3",
        scenario_key="manual_outbound_picking@v1",
        expected_plugin_key="manual_bin_processing",
        profile="FULL_SITE_INTEGRATION",
        environment_label="integration",
        operator_user_id=42,
        active_scope="WORKLINE:3",
        status="ACTIVE",
        current_phase="RACK_TRANSPORT",
        task_id="PICK-001",
        picking_task_id=101,
        device_code="ECS-01",
        configuration_json={
            "plan_resources": {
                "target_rack": {"rack_id": "RACK-01", "rack_face": "90"},
                "direct_picks": [],
                "bin_source_racks": [],
            }
        },
    )
    repository = _Repository(run)
    transport = AsyncMock()
    action_id = "019f12d0-58d7-7b4d-a23a-1b90aa5d4475"
    transport.create_debug_task_in_session.return_value = TransportHandle("transport-1", action_id)
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        confirmations=AsyncMock(),  # type: ignore[arg-type]
        transport=transport,
        device_commands=AsyncMock(),  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )
    action = IntegrationTransportAction(
        kind=IntegrationTransportActionKind.MOVE_RACK,
        client_request_id=action_id,
        rack_id="RACK-01",
        source={"kind": "RACK", "location_code": "RACK-01"},
        target={"kind": "RACK_POSITION", "location_code": "KT16"},
        target_face="90",
        rcs_template_id="CTU01",
    )

    first = await service.create_transport_action("run-1", action=action, expected_version=0, actor_id=42)
    second = await service.create_transport_action("run-1", action=action, expected_version=1, actor_id=42)

    transport.create_debug_task_in_session.assert_awaited_once()
    assert first["steps"][0]["transport_task_id"] == "transport-1"
    assert second["steps"][0]["transport_task_id"] == "transport-1"


@pytest.mark.asyncio
async def test_sorting3_rejects_rack_transport_outside_the_fixed_site_contract() -> None:
    run = IntegrationRun(
        run_id="run-site-contract",
        workline_id=3,
        workline_code="sorting-3",
        scenario_key="manual_outbound_picking@v1",
        expected_plugin_key="manual_bin_processing",
        profile="CONTRACT_SIMULATION",
        environment_label="integration",
        operator_user_id=42,
        active_scope="WORKLINE:3",
        status="ACTIVE",
        current_phase="RACK_TRANSPORT",
        task_id="PICK-001",
        picking_task_id=101,
        device_code="STATION_SCAN12",
        configuration_json={
            "plan_resources": {
                "target_rack": {"rack_id": "RACK-01", "rack_face": "90"},
                "direct_picks": [],
                "bin_source_racks": [],
            }
        },
    )
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=_Repository(run),  # type: ignore[arg-type]
        confirmations=AsyncMock(),  # type: ignore[arg-type]
        transport=AsyncMock(),  # type: ignore[arg-type]
        device_commands=AsyncMock(),  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )
    action = IntegrationTransportAction(
        kind=IntegrationTransportActionKind.MOVE_RACK,
        client_request_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4491",
        rack_id="RACK-01",
        source={"kind": "ZONE", "location_code": "WH01"},
        target={"kind": "RACK_POSITION", "location_code": "KT16"},
        target_face="90",
        rcs_template_id="CTU01",
    )

    with pytest.raises(IntegrationDebugContractError, match="来源必须直接使用货架号"):
        await service.create_transport_action("run-site-contract", action=action, expected_version=0, actor_id=42)


def test_sorting3_rack_return_uses_ctu03_and_wh05() -> None:
    action = IntegrationTransportAction(
        kind=IntegrationTransportActionKind.MOVE_RACK,
        client_request_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4492",
        rack_id="RACK-01",
        source={"kind": "RACK", "location_code": "RACK-01"},
        target={"kind": "ZONE", "location_code": "WH05"},
        rcs_template_id="CTU03",
    )

    IntegrationDebugService._validate_sorting3_transport(IntegrationDebugPhase.RACK_DEPARTURE, action)

    action.target["location_code"] = "RETURN53"
    with pytest.raises(IntegrationDebugContractError, match="WH05"):
        IntegrationDebugService._validate_sorting3_transport(IntegrationDebugPhase.RACK_DEPARTURE, action)


@pytest.mark.asyncio
async def test_bin_inbound_batch_is_fixed_to_one_bin_for_the_temporary_console() -> None:
    run = IntegrationRun(
        run_id="run-inbound-batch",
        workline_id=3,
        workline_code="sorting-3",
        scenario_key="manual_outbound_picking@v1",
        expected_plugin_key="manual_bin_processing",
        profile="CONTRACT_SIMULATION",
        environment_label="integration",
        operator_user_id=42,
        active_scope="WORKLINE:3",
        status="ACTIVE",
        current_phase="BIN_INBOUND_BATCH",
        task_id="PICK-001",
        picking_task_id=101,
        configuration_json={
            "plan_resources": {
                "target_rack": {"rack_id": "TARGET-01", "rack_face": "0"},
                "direct_picks": [],
                "bin_source_racks": [
                    {"rack_id": "RACK-01", "rack_face": "90"},
                    {"rack_id": "RACK-02", "rack_face": "180"},
                ],
            }
        },
    )
    repository = _Repository(run)
    confirmations = AsyncMock()
    confirmations.create_or_get.return_value = WmsConfirmationAcceptance(SimpleNamespace(id=7), duplicate=False)
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        confirmations=confirmations,
        transport=AsyncMock(),  # type: ignore[arg-type]
        device_commands=AsyncMock(),  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )

    result = await service.send_bin_inbound_batch(
        "run-inbound-batch",
        client_request_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4493",
        rack_id="RACK-01",
        rack_face="90",
        max_bin_count=1,
        expected_version=0,
        actor_id=42,
    )

    assert result["steps"][0]["request"]["max_bin_count"] == 1

    with pytest.raises(IntegrationDebugConflict, match="WMS 请求内容已变化"):
        await service.send_bin_inbound_batch(
            "run-inbound-batch",
            client_request_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4493",
            rack_id="RACK-02",
            rack_face="180",
            max_bin_count=1,
            expected_version=1,
            actor_id=42,
        )

    with pytest.raises(IntegrationDebugContractError, match="max_bin_count=1"):
        await service.send_bin_inbound_batch(
            "run-inbound-batch",
            client_request_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4494",
            rack_id="RACK-01",
            rack_face="90",
            max_bin_count=2,
            expected_version=1,
            actor_id=42,
        )


@pytest.mark.asyncio
async def test_transport_action_rejects_rack_outside_the_applied_plan() -> None:
    run = IntegrationRun(
        run_id="run-rack",
        workline_id=3,
        workline_code="sorting-3",
        scenario_key="manual_outbound_picking@v1",
        expected_plugin_key="manual_bin_processing",
        profile="FULL_SITE_INTEGRATION",
        environment_label="integration",
        operator_user_id=42,
        active_scope="WORKLINE:3",
        status="ACTIVE",
        current_phase="RACK_TRANSPORT",
        task_id="PICK-001",
        picking_task_id=101,
        device_code="ECS-01",
        configuration_json={
            "plan_resources": {
                "target_rack": {"rack_id": "RACK-01", "rack_face": "90"},
                "direct_picks": [],
                "bin_source_racks": [],
            }
        },
    )
    transport = AsyncMock()
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=_Repository(run),  # type: ignore[arg-type]
        confirmations=AsyncMock(),  # type: ignore[arg-type]
        transport=transport,
        device_commands=AsyncMock(),  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )
    action = IntegrationTransportAction(
        kind=IntegrationTransportActionKind.MOVE_RACK,
        client_request_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4490",
        rack_id="RACK-OTHER",
        source={"kind": "ZONE", "location_code": "STORAGE-01"},
        target={"kind": "RACK_POSITION", "location_code": "LINE3-RACK"},
        target_face="90",
        rcs_template_id="CTU01",
    )

    with pytest.raises(IntegrationDebugContractError, match="plan_delta"):
        await service.create_transport_action("run-rack", action=action, expected_version=0, actor_id=42)
    transport.create_debug_task_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_contract_simulation_records_ecs_action_without_creating_device_command() -> None:
    run = IntegrationRun(
        run_id="run-sim",
        workline_id=3,
        workline_code="sorting-3",
        scenario_key="manual_outbound_picking@v1",
        expected_plugin_key="manual_bin_processing",
        profile="CONTRACT_SIMULATION",
        environment_label="integration",
        operator_user_id=42,
        active_scope="WORKLINE:3",
        status="ACTIVE",
        current_phase="POINT2_RELEASE",
        device_code="SIM-ECS-01",
        configuration_json={
            "manual_bin_admission_result": "NO_WORK",
            "site_configuration": SORTING_3_SITE_CONFIGURATION,
        },
    )
    repository = _Repository(run)
    device_commands = AsyncMock()
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        confirmations=AsyncMock(),  # type: ignore[arg-type]
        transport=AsyncMock(),  # type: ignore[arg-type]
        device_commands=device_commands,  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )

    result = await service.create_device_action(
        "run-sim",
        client_request_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4476",
        device_code="STATION_SCAN10",
        task_type="MOVE_FORWARD",
        params={"point": "point2"},
        timeout_ms=30_000,
        reason="合同模拟",
        expected_version=0,
        actor_id=42,
    )

    device_commands.create_manual_debug_command.assert_not_awaited()
    assert result["steps"][0]["status"] == "SUCCEEDED"
    assert result["steps"][0]["request"]["device_code"] == "STATION_SCAN10"
    assert result["steps"][0]["result"] == {"simulated": True}


@pytest.mark.asyncio
async def test_point2_release_rejects_a_command_for_another_sorting3_station() -> None:
    run = IntegrationRun(
        run_id="run-wrong-station",
        workline_id=3,
        workline_code="sorting-3",
        scenario_key="manual_outbound_picking@v1",
        expected_plugin_key="manual_bin_processing",
        profile="CONTRACT_SIMULATION",
        environment_label="integration",
        operator_user_id=42,
        active_scope="WORKLINE:3",
        status="ACTIVE",
        current_phase="POINT2_RELEASE",
        bin_code="BIN-001",
        configuration_json={
            "manual_bin_admission_result": "NO_WORK",
            "site_configuration": SORTING_3_SITE_CONFIGURATION,
        },
    )
    commands = AsyncMock()
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=_Repository(run),  # type: ignore[arg-type]
        confirmations=AsyncMock(),  # type: ignore[arg-type]
        transport=AsyncMock(),  # type: ignore[arg-type]
        device_commands=commands,  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )

    with pytest.raises(IntegrationDebugContractError, match="point2"):
        await service.create_device_action(
            run.run_id,
            client_request_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4576",
            device_code="STATION_SCAN9",
            task_type="MOVE_FORWARD",
            params={"point": "point2"},
            timeout_ms=30_000,
            reason="错误点位",
            expected_version=0,
            actor_id=42,
        )

    commands.create_manual_debug_command.assert_not_awaited()


@pytest.mark.asyncio
async def test_device_command_recovery_reuses_the_original_creator_and_endpoint() -> None:
    run = IntegrationRun(
        run_id="run-device-recovery",
        workline_id=3,
        workline_code="sorting-3",
        scenario_key="manual_outbound_picking@v1",
        expected_plugin_key="manual_bin_processing",
        profile="FULL_SITE_INTEGRATION",
        environment_label="integration",
        operator_user_id=42,
        active_scope="WORKLINE:3",
        status="ACTIVE",
        current_phase="POINT2_RELEASE",
        bin_code="BIN-001",
        configuration_json={
            "manual_bin_admission_result": "NO_WORK",
            "site_configuration": SORTING_3_SITE_CONFIGURATION,
        },
    )
    repository = _Repository(run)
    client_request_id = "019f12d0-58d7-7b4d-a23a-1b90aa5d4577"
    repository.steps.append(
        IntegrationRunStep(
            run_id=run.run_id,
            ordinal=1,
            phase="POINT2_RELEASE",
            status="WAITING",
            client_request_id=client_request_id,
            request_summary_json={
                "device_code": "STATION_SCAN10",
                "task_type": "MOVE_FORWARD",
                "params": {"point": "point2"},
                "timeout_ms": 30_000,
                "reason": "首次创建",
                "created_by": 41,
                "endpoint_base_url": "http://10.24.209.26:8080/",
            },
        )
    )
    commands = AsyncMock()
    commands.create_manual_debug_command.return_value = SimpleNamespace(command_code="CMD-001")
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        confirmations=AsyncMock(),  # type: ignore[arg-type]
        transport=AsyncMock(),  # type: ignore[arg-type]
        device_commands=commands,  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )

    result = await service.create_device_action(
        run.run_id,
        client_request_id=client_request_id,
        device_code="STATION_SCAN10",
        task_type="MOVE_FORWARD",
        params={"point": "point2"},
        timeout_ms=30_000,
        reason="首次创建",
        expected_version=0,
        actor_id=42,
    )

    assert result["steps"][0]["device_command_code"] == "CMD-001"
    commands.create_manual_debug_command.assert_awaited_once_with(
        client_request_id=client_request_id,
        endpoint_base_url="http://10.24.209.26:8080/",
        device_code="STATION_SCAN10",
        contract_key="ecs.manual-debug.command",
        contract_version="1.0",
        command_timeout_ms=30_000,
        task_type="MOVE_FORWARD",
        params={"point": "point2"},
        trace_id=run.run_id,
        execution_reason="首次创建",
        created_by=41,
    )


def test_bin_transports_must_match_the_single_wms_batch_member() -> None:
    inbound = IntegrationTransportAction(
        kind=IntegrationTransportActionKind.MOVE_BINS,
        client_request_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4480",
        rack_id="RACK-01",
        bin_code="BIN-001",
        source={"kind": "RACK_BIN_SLOT", "rack_id": "RACK-01", "rack_face": "90", "slot_id": "SLOT-01"},
        target={"kind": "HANDOFF_POSITION", "location_code": "CNV0301"},
        rcs_template_id="CTU01",
    )
    configuration = {
        "inbound_bins": [
            {
                "bin_code": "BIN-001",
                "source_locator": {
                    "type": "RACK_BIN_SLOT",
                    "rack_id": "RACK-01",
                    "rack_face": "90",
                    "slot_id": "SLOT-01",
                },
            }
        ]
    }

    IntegrationDebugService._validate_batch_transport(IntegrationDebugPhase.BIN_TRANSPORT, configuration, inbound)

    inbound.source["slot_id"] = "SLOT-OTHER"
    with pytest.raises(IntegrationDebugContractError, match="inbound_batch READY"):
        IntegrationDebugService._validate_batch_transport(IntegrationDebugPhase.BIN_TRANSPORT, configuration, inbound)


@pytest.mark.asyncio
async def test_same_inbound_batch_member_cannot_be_moved_again_with_a_new_identity() -> None:
    run = IntegrationRun(
        run_id="run-bin-duplicate",
        workline_id=3,
        workline_code="sorting-3",
        scenario_key="manual_outbound_picking@v1",
        expected_plugin_key="manual_bin_processing",
        profile="CONTRACT_SIMULATION",
        environment_label="integration",
        operator_user_id=42,
        active_scope="WORKLINE:3",
        status="ACTIVE",
        current_phase="BIN_TRANSPORT",
        task_id="PICK-001",
        picking_task_id=101,
        configuration_json={
            "plan_resources": {
                "target_rack": {"rack_id": "TARGET-01", "rack_face": "0"},
                "direct_picks": [],
                "bin_source_racks": [{"rack_id": "RACK-01", "rack_face": "90"}],
            },
            "inbound_bins": [
                {
                    "bin_code": "BIN-001",
                    "source_locator": {
                        "type": "RACK_BIN_SLOT",
                        "rack_id": "RACK-01",
                        "rack_face": "90",
                        "slot_id": "SLOT-01",
                    },
                }
            ],
        },
    )
    repository = _Repository(run)
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        confirmations=AsyncMock(),  # type: ignore[arg-type]
        transport=AsyncMock(),  # type: ignore[arg-type]
        device_commands=AsyncMock(),  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )

    def action(client_request_id: str) -> IntegrationTransportAction:
        return IntegrationTransportAction(
            kind=IntegrationTransportActionKind.MOVE_BINS,
            client_request_id=client_request_id,
            rack_id="RACK-01",
            bin_code="BIN-001",
            source={
                "kind": "RACK_BIN_SLOT",
                "rack_id": "RACK-01",
                "rack_face": "90",
                "slot_id": "SLOT-01",
            },
            target={"kind": "HANDOFF_POSITION", "location_code": "CNV0301"},
            rcs_template_id="CTU01",
        )

    await service.create_transport_action(
        run.run_id,
        action=action("019f12d0-58d7-7b4d-a23a-1b90aa5d4480"),
        expected_version=0,
        actor_id=42,
    )

    with pytest.raises(IntegrationDebugConflict, match="批次成员"):
        await service.create_transport_action(
            run.run_id,
            action=action("019f12d0-58d7-7b4d-a23a-1b90aa5d4481"),
            expected_version=1,
            actor_id=42,
        )


@pytest.mark.asyncio
async def test_plan_blocked_task_cannot_create_a_new_transport() -> None:
    run = IntegrationRun(
        run_id="run-plan-blocked",
        workline_id=3,
        workline_code="sorting-3",
        scenario_key="manual_outbound_picking@v1",
        expected_plugin_key="manual_bin_processing",
        profile="FULL_SITE_INTEGRATION",
        environment_label="integration",
        operator_user_id=42,
        active_scope="WORKLINE:3",
        status="ACTIVE",
        current_phase="RACK_TRANSPORT",
        task_id="PICK-001",
        picking_task_id=101,
        configuration_json={
            "plan_resources": {
                "target_rack": {"rack_id": "RACK-01", "rack_face": "90"},
                "direct_picks": [],
                "bin_source_racks": [],
            }
        },
    )
    repository = _Repository(run)
    repository.picking_task.plan_blocked_evidence_id = 77
    transport = AsyncMock()
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        confirmations=AsyncMock(),  # type: ignore[arg-type]
        transport=transport,
        device_commands=AsyncMock(),  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )
    action = IntegrationTransportAction(
        kind=IntegrationTransportActionKind.MOVE_RACK,
        client_request_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4481",
        rack_id="RACK-01",
        source={"kind": "RACK", "location_code": "RACK-01"},
        target={"kind": "RACK_POSITION", "location_code": "KT16"},
        target_face="90",
        rcs_template_id="CTU01",
    )

    with pytest.raises(IntegrationDebugConflict, match="plan_delta"):
        await service.create_transport_action(run.run_id, action=action, expected_version=0, actor_id=42)
    transport.create_debug_task_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_ecs_command_cannot_bypass_work_completion() -> None:
    run = IntegrationRun(
        run_id="run-wait-completion",
        workline_id=3,
        workline_code="sorting-3",
        scenario_key="manual_outbound_picking@v1",
        expected_plugin_key="manual_bin_processing",
        profile="FULL_SITE_INTEGRATION",
        environment_label="integration",
        operator_user_id=42,
        active_scope="WORKLINE:3",
        status="WAITING_EXTERNAL",
        current_phase="WORK_COMPLETION",
        bin_code="BIN-001",
        configuration_json={"manual_bin_admission_result": "WORK_REQUIRED"},
    )
    commands = AsyncMock()
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=_Repository(run),  # type: ignore[arg-type]
        confirmations=AsyncMock(),  # type: ignore[arg-type]
        transport=AsyncMock(),  # type: ignore[arg-type]
        device_commands=commands,  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )

    with pytest.raises(IntegrationDebugConflict, match="point2 释放或 point3 分流"):
        await service.create_device_action(
            run.run_id,
            client_request_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4482",
            device_code="STATION_SCAN9",
            task_type="MOVE_FORWARD",
            params={},
            timeout_ms=30_000,
            reason="非法提前释放",
            expected_version=0,
            actor_id=42,
        )
    commands.create_manual_debug_command.assert_not_awaited()


@pytest.mark.asyncio
async def test_completed_wms_confirmation_closes_the_local_picking_task() -> None:
    run = IntegrationRun(
        run_id="run-task-complete",
        workline_id=3,
        workline_code="sorting-3",
        scenario_key="manual_outbound_picking@v1",
        expected_plugin_key="manual_bin_processing",
        profile="CONTRACT_SIMULATION",
        environment_label="integration",
        operator_user_id=42,
        active_scope="WORKLINE:3",
        status="WAITING_EXTERNAL",
        current_phase="TASK_COMPLETION",
        task_id="PICK-001",
        picking_task_id=101,
    )
    repository = _Repository(run)
    repository.steps.append(
        IntegrationRunStep(
            run_id=run.run_id,
            ordinal=1,
            phase="TASK_COMPLETION",
            status="WAITING",
            client_request_id="complete-request",
            operation="outbound.picking_task.completion_confirm@v1",
            wms_confirmation_id=9,
        )
    )
    repository.confirmation = SimpleNamespace(
        status=WmsConfirmationStatus.COMPLETED,
        response_evidence_id=None,
        response_result="COMPLETED",
    )
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        confirmations=AsyncMock(),  # type: ignore[arg-type]
        transport=AsyncMock(),  # type: ignore[arg-type]
        device_commands=AsyncMock(),  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )

    result = await service.refresh_wms_action(
        run.run_id,
        client_request_id="complete-request",
        expected_version=0,
        actor_id=42,
    )

    assert result["current_phase"] == "CLEANUP"
    assert repository.picking_task.status == PickingTaskStatus.EXECUTION_COMPLETED


@pytest.mark.asyncio
async def test_run_cannot_be_completed_before_cleanup_phase() -> None:
    run = IntegrationRun(
        run_id="run-active",
        workline_id=3,
        workline_code="sorting-3",
        scenario_key="manual_outbound_picking@v1",
        expected_plugin_key="manual_bin_processing",
        profile="CONTRACT_SIMULATION",
        environment_label="integration",
        operator_user_id=42,
        active_scope="WORKLINE:3",
        status="ACTIVE",
        current_phase="POINT2_RELEASE",
        device_code="SIM-ECS-01",
    )
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=_Repository(run),  # type: ignore[arg-type]
        confirmations=AsyncMock(),  # type: ignore[arg-type]
        transport=AsyncMock(),  # type: ignore[arg-type]
        device_commands=AsyncMock(),  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )

    with pytest.raises(IntegrationDebugConflict, match="CLEANUP"):
        await service.mark_completed("run-active", expected_version=0, actor_id=42)
