from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.execution.services.wms_confirmation_service import WmsConfirmationAcceptance
from src.app.transport.contracts import (
    TRANSPORT_DEBUG_CALLER_WORKLINE_ID,
    MoveBinsRequest,
    MoveRackRequest,
    RotateRackRequest,
    TransportHandle,
)
from src.app.workline_integration_debug.contracts import (
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
    def begin(self) -> _Transaction:
        return _Transaction()


class _Repository:
    def __init__(self, run: IntegrationRun) -> None:
        self.run = run
        self.steps = []
        self.workline_for_update = False
        self.evidence = None

    async def get_run(self, _db, _run_id, *, for_update=False):  # type: ignore[no-untyped-def]
        return self.run

    async def get_step_by_client_request_id(self, _db, client_request_id, *, for_update=False):  # type: ignore[no-untyped-def]
        return next((step for step in self.steps if step.client_request_id == client_request_id), None)

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

    async def get_evidence_by_operation(self, _db, _operation, _operation_id):  # type: ignore[no-untyped-def]
        return self.evidence


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

    with pytest.raises(IntegrationDebugConflict, match="COMPLETION_REPORT"):
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
        configuration_json={"point2_scanned_at": 2000},
    )
    repository = _Repository(run)
    repository.evidence = SimpleNamespace(
        normalized_payload={
            "data": {
                "task_id": "PICK-001",
                "bin_code": "BIN-001",
                "result": "NORMAL",
                "completed_at": 1999,
            }
        }
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
async def test_bin_inbound_batch_uses_the_admin_selected_max_count() -> None:
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
                "bin_source_racks": [{"rack_id": "RACK-01", "rack_face": "90"}],
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
        max_bin_count=3,
        expected_version=0,
        actor_id=42,
    )

    assert result["steps"][0]["request"]["max_bin_count"] == 3


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
        task_type="RELEASE_BIN",
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
