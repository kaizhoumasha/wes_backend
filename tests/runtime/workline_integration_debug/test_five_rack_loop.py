from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from src.app.wms_adapter.outbound_picking.departure_wire import RackDepartureData
from src.app.workline_integration_debug.contracts import (
    MANUAL_OUTBOUND_SITE_CONFIGURATION,
    IntegrationDebugPhase,
    IntegrationTransportAction,
    IntegrationTransportActionKind,
)
from src.app.workline_integration_debug.models import IntegrationRun, IntegrationRunStep
from src.app.workline_integration_debug.service import (
    IntegrationDebugConflict,
    IntegrationDebugContractError,
    IntegrationDebugService,
)


def _run(*, phase: str = "BIN_INBOUND_BATCH", rack_count: int = 2) -> IntegrationRun:
    racks = [
        {"rack_id": f"RACK-{index + 1:02d}", "faces": ["90", "270"] if index == 0 else ["180"], "completed_faces": []}
        for index in range(rack_count)
    ]
    plan_members = [{"rack_id": rack["rack_id"], "rack_face": face} for rack in racks for face in rack["faces"]]
    return IntegrationRun(
        run_id="run-five-rack-loop",
        workline_id=3,
        workline_code="KT16",
        scenario_key="manual_outbound_picking@v1",
        expected_plugin_key="manual_bin_processing",
        profile="CONTRACT_SIMULATION",
        environment_label="integration",
        operator_user_id=42,
        active_scope="WORKLINE:3",
        status="ACTIVE",
        current_phase=phase,
        task_id="PICK-001",
        picking_task_id=101,
        configuration_json={
            "plan_resources": {
                "target_rack": {"rack_id": "TARGET-01", "rack_face": "0"},
                "direct_picks": [],
                "bin_source_racks": plan_members,
            },
            "source_rack_progress": {
                "work_position": "KT16",
                "current_rack_index": 0,
                "current_face_index": 0,
                "racks": racks,
            },
            "current_source_rack": {"rack_id": "RACK-01", "rack_face": "90"},
        },
    )


def test_source_members_are_grouped_by_rack_and_reuse_one_work_position() -> None:
    run = _run(rack_count=0)
    IntegrationDebugService._sync_source_rack_progress(
        run,
        [
            {"rack_id": "RACK-01", "rack_face": "90"},
            {"rack_id": "RACK-01", "rack_face": "270"},
            {"rack_id": "RACK-02", "rack_face": "180"},
        ],
    )

    assert run.configuration_json["source_rack_progress"] == {
        "work_position": "KT16",
        "current_rack_index": 0,
        "current_face_index": 0,
        "racks": [
            {"rack_id": "RACK-01", "faces": ["90", "270"], "completed_faces": []},
            {"rack_id": "RACK-02", "faces": ["180"], "completed_faces": []},
        ],
    }
    assert run.configuration_json["current_source_rack"] == {"rack_id": "RACK-01", "rack_face": "90"}
    assert run.configuration_json["pending_source_racks"] == [{"rack_id": "RACK-02", "faces": ["180"]}]


def test_rack_face_done_rotates_same_rack_then_departure_releases_next_rack() -> None:
    run = _run()

    IntegrationDebugService._advance_inbound_batch(run, "RACK_FACE_DONE", {})
    assert run.current_phase == "RACK_TRANSPORT"
    assert run.configuration_json["rack_transport_mode"] == "ROTATE_SOURCE_RACK"
    assert run.configuration_json["current_source_rack"] == {"rack_id": "RACK-01", "rack_face": "270"}

    IntegrationDebugService._advance_inbound_batch(run, "RACK_FACE_DONE", {})
    assert run.current_phase == "RACK_DEPARTURE"
    assert run.configuration_json["departure_candidate"]["rack_id"] == "RACK-01"

    phase = IntegrationDebugService._finish_current_source_departure(run, "RACK-01")
    assert phase == IntegrationDebugPhase.RACK_TRANSPORT
    assert run.configuration_json["current_source_rack"] == {"rack_id": "RACK-02", "rack_face": "180"}


@pytest.mark.parametrize(
    ("pending", "expected_phase"),
    [
        (["BIN-01", "BIN-02"], IntegrationDebugPhase.POINT1_ARRIVAL),
        (["BIN-01"], IntegrationDebugPhase.BIN_INBOUND_BATCH),
    ],
)
def test_completed_bin_continues_current_batch_or_requests_next_batch(
    pending: list[str], expected_phase: IntegrationDebugPhase
) -> None:
    run = _run()
    run.bin_code = "BIN-01"
    run.configuration_json = {**run.configuration_json, "pending_inbound_bin_codes": pending, "source_cycle_no": 0}

    assert IntegrationDebugService._finish_current_bin(run) == expected_phase
    assert run.bin_code is None
    assert run.configuration_json["pending_inbound_bin_codes"] == pending[1:]
    assert run.configuration_json["source_cycle_no"] == 1


def test_existing_return_batch_without_inbound_history_recovers_from_current_bin() -> None:
    run = _run(phase="BIN_RETURN_BATCH")
    run.bin_code = "BIN-LEGACY-01"

    assert IntegrationDebugService._finish_current_bin(run) == IntegrationDebugPhase.BIN_INBOUND_BATCH
    assert run.bin_code is None
    assert run.configuration_json["pending_inbound_bin_codes"] == []
    assert run.configuration_json["source_cycle_no"] == 1


@pytest.mark.asyncio
async def test_historical_return_transport_confirmation_restores_source_from_persisted_step() -> None:
    run = _run(phase="BIN_RETURN_TRANSPORT")
    run.bin_code = "BIN-LEGACY-01"
    run.configuration_json = {"plan_resources": run.configuration_json["plan_resources"]}
    steps = [
        IntegrationRunStep(
            run_id=run.run_id,
            ordinal=0,
            phase="BIN_RETURN_TRANSPORT",
            status="SUCCEEDED",
            request_summary_json={
                "kind": "MOVE_BINS",
                "rack_id": "RACK-02",
                "bin_code": "BIN-LEGACY-01",
                "target": {
                    "kind": "RACK_BIN_SLOT",
                    "rack_id": "RACK-02",
                    "rack_face": "180",
                    "slot_id": "SLOT-01",
                },
            },
        )
    ]
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=_Repository(run, steps),  # type: ignore[arg-type]
        confirmations=AsyncMock(),  # type: ignore[arg-type]
        transport=AsyncMock(),  # type: ignore[arg-type]
        device_commands=AsyncMock(),  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )

    result = await service.confirm_current_phase(run.run_id, note="回库完成", expected_version=0, actor_id=42)

    assert result["current_phase"] == "BIN_INBOUND_BATCH"
    assert result["operation_context"]["current_source_rack"] == {"rack_id": "RACK-02", "rack_face": "180"}
    assert result["operation_context"]["pending_source_racks"] == [{"rack_id": "RACK-01", "faces": ["90", "270"]}]
    assert result["operation_context"]["pending_inbound_bin_codes"] == []
    assert result["operation_context"]["source_cycle_no"] == 1


def test_historical_run_without_source_evidence_does_not_assume_the_first_planned_rack() -> None:
    run = _run(phase="BIN_RETURN_BATCH")
    run.configuration_json = {"plan_resources": run.configuration_json["plan_resources"]}

    IntegrationDebugService._ensure_source_rack_progress(run, [])

    assert "source_rack_progress" not in run.configuration_json
    assert "current_source_rack" not in run.configuration_json


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("phase", "transport_request", "configuration"),
    [
        (
            "BIN_TRANSPORT",
            {"kind": "MOVE_BINS", "rack_id": "RACK-01", "source_cycle_no": 1},
            {},
        ),
        (
            "BIN_RETURN_TRANSPORT",
            {"kind": "MOVE_BINS", "rack_id": "RACK-01", "bin_code": "BIN-02", "source_cycle_no": 1},
            {"pending_inbound_bin_codes": ["BIN-02"]},
        ),
        (
            "RACK_DEPARTURE",
            {"kind": "MOVE_RACK", "rack_id": "RACK-01", "source_cycle_no": 1},
            {"departure_ready_rack_id": "RACK-01"},
        ),
    ],
)
async def test_transport_confirmation_cannot_reuse_a_previous_cycle_success(
    phase: str,
    transport_request: dict[str, object],
    configuration: dict[str, object],
) -> None:
    run = _run(phase=phase)
    run.bin_code = "BIN-02"
    run.configuration_json = {**run.configuration_json, **configuration, "source_cycle_no": 2}
    steps = [
        IntegrationRunStep(
            run_id=run.run_id,
            ordinal=0,
            phase=phase,
            status="SUCCEEDED",
            request_summary_json=transport_request,
        )
    ]
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=_Repository(run, steps),  # type: ignore[arg-type]
        confirmations=AsyncMock(),  # type: ignore[arg-type]
        transport=AsyncMock(),  # type: ignore[arg-type]
        device_commands=AsyncMock(),  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )

    with pytest.raises(IntegrationDebugConflict, match="本阶段 Transport"):
        await service.confirm_current_phase(run.run_id, note="本轮完成", expected_version=0, actor_id=42)


def test_completed_bin_rejects_out_of_order_batch_member_without_mutating_progress() -> None:
    run = _run()
    run.bin_code = "BIN-02"
    run.configuration_json = {
        **run.configuration_json,
        "pending_inbound_bin_codes": ["BIN-01", "BIN-02"],
        "source_cycle_no": 7,
    }
    original_configuration = deepcopy(run.configuration_json)

    with pytest.raises(IntegrationDebugConflict, match="批次队头"):
        IntegrationDebugService._finish_current_bin(run)

    assert run.bin_code == "BIN-02"
    assert run.configuration_json == original_configuration


def test_transport_member_is_unique_within_one_cycle_but_can_repeat_in_a_later_cycle() -> None:
    request = {
        "kind": "MOVE_BINS",
        "rack_id": "RACK-01",
        "bin_code": "BIN-01",
        "source": {"kind": "RACK_BIN_SLOT", "rack_id": "RACK-01", "rack_face": "90", "slot_id": "SLOT-01"},
        "target": {"kind": "HANDOFF_POSITION", "location_code": "CNV0301"},
        "rcs_template_id": "CTU01",
        "target_face": None,
        "source_cycle_no": 3,
        "inbound_bins": [{"bin_code": "BIN-01"}],
    }
    steps = [
        IntegrationRunStep(
            run_id="run-five-rack-loop",
            ordinal=0,
            phase="BIN_TRANSPORT",
            status="SUCCEEDED",
            request_summary_json=request,
        )
    ]

    with pytest.raises(IntegrationDebugConflict, match="已为该批次成员创建 Transport"):
        IntegrationDebugService._assert_transport_member_not_created(
            steps,
            IntegrationDebugPhase.BIN_TRANSPORT,
            request,
        )

    IntegrationDebugService._assert_transport_member_not_created(
        steps,
        IntegrationDebugPhase.BIN_TRANSPORT,
        {**request, "source_cycle_no": 4},
    )


def test_last_source_departure_schedules_target_return_before_task_completion() -> None:
    run = _run(rack_count=1)
    run.configuration_json["source_rack_progress"]["current_face_index"] = 1
    run.configuration_json["current_source_rack"] = {"rack_id": "RACK-01", "rack_face": "270"}

    phase = IntegrationDebugService._finish_current_source_departure(run, "RACK-01")

    assert phase == IntegrationDebugPhase.RACK_DEPARTURE
    assert run.configuration_json["departure_candidate"] == {
        "rack_id": "TARGET-01",
        "rack_face": "0",
        "current_location": "OUT65",
        "role": "TARGET_RACK",
    }


def test_target_rack_departure_uses_f01_after_all_source_racks_leave() -> None:
    run = _run(rack_count=1)
    run.configuration_json["source_rack_progress"]["current_face_index"] = 1
    run.configuration_json["current_source_rack"] = {"rack_id": "RACK-01", "rack_face": "270"}
    IntegrationDebugService._finish_current_source_departure(run, "RACK-01")
    action = IntegrationTransportAction(
        kind=IntegrationTransportActionKind.MOVE_RACK,
        client_request_id="target-return",
        rack_id="TARGET-01",
        source={"kind": "RACK", "location_code": "TARGET-01"},
        target={"kind": "ZONE", "location_code": "WH05"},
        target_face="0",
        rcs_template_id="F01",
    )

    IntegrationDebugService._validate_manual_outbound_transport(
        IntegrationDebugPhase.RACK_DEPARTURE,
        action,
        run.configuration_json["plan_resources"],
        run.configuration_json,
    )

    with pytest.raises(IntegrationDebugContractError, match="F01"):
        IntegrationDebugService._validate_manual_outbound_transport(
            IntegrationDebugPhase.RACK_DEPARTURE,
            replace(action, rcs_template_id="CTU03"),
            run.configuration_json["plan_resources"],
            run.configuration_json,
        )


def test_source_departure_does_not_advance_on_a_different_rack() -> None:
    run = _run()

    with pytest.raises(IntegrationDebugConflict, match="当前占用 KT16"):
        IntegrationDebugService._finish_current_source_departure(run, "RACK-02")


def test_capacity_one_rejects_next_rack_and_next_face_uses_ctu02_at_kt16() -> None:
    run = _run(phase="RACK_TRANSPORT")
    plan = run.configuration_json["plan_resources"]
    with pytest.raises(IntegrationDebugContractError, match="容量为 1"):
        IntegrationDebugService._validate_manual_outbound_transport(
            IntegrationDebugPhase.RACK_TRANSPORT,
            IntegrationTransportAction(
                kind=IntegrationTransportActionKind.MOVE_RACK,
                client_request_id="next-rack",
                rack_id="RACK-02",
                source={"kind": "RACK", "location_code": "RACK-02"},
                target={"kind": "RACK_POSITION", "location_code": "KT16"},
                target_face="180",
                rcs_template_id="CTU01",
            ),
            plan,
            run.configuration_json,
        )

    run.configuration_json["rack_transport_mode"] = "ROTATE_SOURCE_RACK"
    run.configuration_json["source_rack_progress"]["current_face_index"] = 1
    run.configuration_json["current_source_rack"] = {"rack_id": "RACK-01", "rack_face": "270"}
    IntegrationDebugService._validate_manual_outbound_transport(
        IntegrationDebugPhase.RACK_TRANSPORT,
        IntegrationTransportAction(
            kind=IntegrationTransportActionKind.ROTATE_RACK,
            client_request_id="next-face",
            rack_id="RACK-01",
            source={"kind": "RACK_POSITION", "location_code": "KT16"},
            target={},
            target_face="270",
            rcs_template_id="CTU02",
        ),
        plan,
        run.configuration_json,
    )


def test_rack_transport_rejects_a_planned_face_that_is_not_the_current_face() -> None:
    run = _run(phase="RACK_TRANSPORT")

    with pytest.raises(IntegrationDebugContractError, match="当前排队来源货架及面向"):
        IntegrationDebugService._validate_manual_outbound_transport(
            IntegrationDebugPhase.RACK_TRANSPORT,
            IntegrationTransportAction(
                kind=IntegrationTransportActionKind.MOVE_RACK,
                client_request_id="wrong-current-face",
                rack_id="RACK-01",
                source={"kind": "RACK", "location_code": "RACK-01"},
                target={"kind": "RACK_POSITION", "location_code": "KT16"},
                target_face="270",
                rcs_template_id="CTU01",
            ),
            run.configuration_json["plan_resources"],
            run.configuration_json,
        )


def test_move_bins_duplicate_uses_physical_member_identity_not_ignored_fields() -> None:
    stored = IntegrationRunStep(
        run_id="run-five-rack-loop",
        ordinal=1,
        phase="BIN_RETURN_TRANSPORT",
        status="SUCCEEDED",
        request_summary_json={
            "kind": "MOVE_BINS",
            "rack_id": "RACK-01",
            "bin_code": "BIN-01",
            "source": {"kind": "HANDOFF_POSITION", "location_code": "CNV0302"},
            "target": {
                "kind": "RACK_BIN_SLOT",
                "rack_id": "RACK-01",
                "rack_face": "90",
                "slot_id": "SLOT-01",
            },
            "rcs_template_id": "CTU01",
            "target_face": None,
            "source_cycle_no": 3,
        },
    )

    with pytest.raises(IntegrationDebugConflict, match="已为该批次成员创建"):
        IntegrationDebugService._assert_transport_member_not_created(
            [stored],
            IntegrationDebugPhase.BIN_RETURN_TRANSPORT,
            {
                **stored.request_summary_json,
                "rcs_template_id": "CTU02",
                "target_face": "270",
            },
        )


@pytest.mark.parametrize("phase", [IntegrationDebugPhase.RACK_TRANSPORT, IntegrationDebugPhase.RACK_DEPARTURE])
def test_rack_duplicate_uses_physical_member_identity_not_ignored_fields(phase: IntegrationDebugPhase) -> None:
    stored = IntegrationRunStep(
        run_id="run-five-rack-loop",
        ordinal=1,
        phase=phase,
        status="SUCCEEDED",
        request_summary_json={
            "kind": "ROTATE_RACK" if phase is IntegrationDebugPhase.RACK_TRANSPORT else "MOVE_RACK",
            "rack_id": "RACK-01",
            "bin_code": None,
            "source": {"kind": "RACK_POSITION", "location_code": "KT16"},
            "target": {},
            "rcs_template_id": "CTU02",
            "target_face": "270",
            "source_cycle_no": 3,
        },
    )

    with pytest.raises(IntegrationDebugConflict, match="已为该批次成员创建"):
        IntegrationDebugService._assert_transport_member_not_created(
            [stored],
            phase,
            {
                **stored.request_summary_json,
                "bin_code": "IGNORED-BIN",
                "target": {"kind": "RACK_POSITION", "location_code": "OTHER"},
            },
        )


def test_rack_actions_reject_fields_not_used_by_the_physical_request() -> None:
    run = _run(phase="RACK_TRANSPORT")
    rotate = IntegrationTransportAction(
        kind=IntegrationTransportActionKind.ROTATE_RACK,
        client_request_id="rotate-extra-fields",
        rack_id="RACK-01",
        bin_code=None,
        source={"kind": "RACK_POSITION", "location_code": "KT16"},
        target={"kind": "RACK_POSITION", "location_code": "OTHER"},
        target_face="90",
        rcs_template_id="CTU02",
    )
    run.configuration_json["rack_transport_mode"] = "ROTATE_SOURCE_RACK"

    with pytest.raises(IntegrationDebugContractError, match="不得携带 target"):
        IntegrationDebugService._validate_manual_outbound_transport(
            IntegrationDebugPhase.RACK_TRANSPORT,
            rotate,
            run.configuration_json["plan_resources"],
            run.configuration_json,
        )

    move_with_bin = IntegrationTransportAction(
        kind=IntegrationTransportActionKind.MOVE_RACK,
        client_request_id="move-extra-bin",
        rack_id="RACK-01",
        bin_code="IGNORED-BIN",
        source={"kind": "RACK", "location_code": "RACK-01"},
        target={"kind": "RACK_POSITION", "location_code": "KT16"},
        target_face="90",
        rcs_template_id="CTU01",
    )
    run.configuration_json.pop("rack_transport_mode", None)

    with pytest.raises(IntegrationDebugContractError, match="不得携带 bin_code"):
        IntegrationDebugService._validate_manual_outbound_transport(
            IntegrationDebugPhase.RACK_TRANSPORT,
            move_with_bin,
            run.configuration_json["plan_resources"],
            run.configuration_json,
        )


@pytest.mark.asyncio
async def test_point2_release_does_not_reuse_a_previous_source_cycle_device_action() -> None:
    run = _run(phase="POINT2_RELEASE")
    run.configuration_json.update({"manual_bin_admission_result": "NO_WORK", "source_cycle_no": 2})
    old_step = IntegrationRunStep(
        run_id=run.run_id,
        ordinal=1,
        phase="POINT2_RELEASE",
        status="SUCCEEDED",
        request_summary_json={"task_type": "MOVE_FORWARD", "source_cycle_no": 1},
        result_summary_json={"simulated": True},
    )
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=_Repository(run, [old_step]),  # type: ignore[arg-type]
        confirmations=AsyncMock(),  # type: ignore[arg-type]
        transport=AsyncMock(),  # type: ignore[arg-type]
        device_commands=AsyncMock(),  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )

    with pytest.raises(IntegrationDebugConflict, match="释放 DeviceCommand"):
        await service.confirm_current_phase(
            run.run_id,
            note="第二箱需要自己的释放命令",
            expected_version=0,
            actor_id=42,
        )


def test_device_request_legacy_cycle_is_only_compatible_with_cycle_zero() -> None:
    stored = {"device_code": "SCAN10", "task_type": "MOVE_FORWARD"}
    assert IntegrationDebugService._device_request_matches(stored, {**stored, "source_cycle_no": 0})
    assert not IntegrationDebugService._device_request_matches(stored, {**stored, "source_cycle_no": 1})


class _Transaction:
    async def __aenter__(self):  # type: ignore[no-untyped-def]
        return object()

    async def __aexit__(self, *_args: object) -> None:
        return None


class _Sessions:
    def begin(self) -> _Transaction:
        return _Transaction()


class _Repository:
    def __init__(self, run: IntegrationRun, steps: list[IntegrationRunStep]) -> None:
        self.run = run
        self.steps = steps

    async def get_run(self, _db, _run_id, *, for_update=False):  # type: ignore[no-untyped-def]
        return self.run

    async def list_steps(self, _db, _run_id):  # type: ignore[no-untyped-def]
        return self.steps

    async def get_step_by_client_request_id(self, _db, client_request_id, *, for_update=False):  # type: ignore[no-untyped-def]
        return next((step for step in self.steps if step.client_request_id == client_request_id), None)

    async def next_ordinal(self, _db, _run_id):  # type: ignore[no-untyped-def]
        return len(self.steps)

    async def add_step(self, _db, step):  # type: ignore[no-untyped-def]
        self.steps.append(step)


@pytest.mark.asyncio
async def test_next_source_cycle_can_create_its_own_device_action() -> None:
    run = _run(phase="POINT2_RELEASE")
    run.configuration_json.update(
        {
            "manual_bin_admission_result": "NO_WORK",
            "source_cycle_no": 2,
            "site_configuration": MANUAL_OUTBOUND_SITE_CONFIGURATION,
        }
    )
    old_step = IntegrationRunStep(
        run_id=run.run_id,
        ordinal=1,
        phase="POINT2_RELEASE",
        status="SUCCEEDED",
        client_request_id="old-device-cycle",
        request_summary_json={
            "device_code": "STATION_SCAN10",
            "task_type": "MOVE_FORWARD",
            "params": {},
            "timeout_ms": 30_000,
            "reason": "第一箱",
            "created_by": 42,
            "source_cycle_no": 1,
        },
        result_summary_json={"simulated": True},
    )
    repository = _Repository(run, [old_step])
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        confirmations=AsyncMock(),  # type: ignore[arg-type]
        transport=AsyncMock(),  # type: ignore[arg-type]
        device_commands=AsyncMock(),  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )

    result = await service.create_device_action(
        run.run_id,
        client_request_id="new-device-cycle",
        device_code="STATION_SCAN10",
        task_type="MOVE_FORWARD",
        params={},
        timeout_ms=30_000,
        reason="第二箱",
        expected_version=0,
        actor_id=42,
    )

    assert len(result["steps"]) == 2
    assert result["steps"][-1]["request"]["source_cycle_no"] == 2


@pytest.mark.asyncio
async def test_departure_decision_rejects_a_rack_face_outside_the_current_candidate() -> None:
    run = _run(phase="RACK_DEPARTURE")
    run.configuration_json["departure_candidate"] = {
        "rack_id": "RACK-01",
        "rack_face": "90",
        "current_location": "KT16",
        "role": "SOURCE_RACK",
    }
    confirmations = AsyncMock()
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=_Repository(run, []),  # type: ignore[arg-type]
        confirmations=confirmations,
        transport=AsyncMock(),  # type: ignore[arg-type]
        device_commands=AsyncMock(),  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )

    with pytest.raises(IntegrationDebugContractError, match="当前待离场货架"):
        await service.send_rack_departure(
            run.run_id,
            client_request_id="departure-wrong-face",
            request_data=RackDepartureData.model_validate(
                {
                    "task_id": "PICK-001",
                    "rack_id": "RACK-01",
                    "current_location": {"type": "RACK_POSITION", "location_code": "KT16"},
                    "current_face": "270",
                }
            ),
            expected_version=0,
            actor_id=42,
        )

    confirmations.create_or_get.assert_not_awaited()


@pytest.mark.asyncio
async def test_departure_decision_requires_a_persisted_current_candidate() -> None:
    run = _run(phase="RACK_DEPARTURE")
    run.configuration_json.pop("departure_candidate", None)
    confirmations = AsyncMock()
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=_Repository(run, []),  # type: ignore[arg-type]
        confirmations=confirmations,
        transport=AsyncMock(),  # type: ignore[arg-type]
        device_commands=AsyncMock(),  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )

    with pytest.raises(IntegrationDebugContractError, match="实物上下文"):
        await service.send_rack_departure(
            run.run_id,
            client_request_id="departure-without-candidate",
            request_data=RackDepartureData.model_validate(
                {
                    "task_id": "PICK-001",
                    "rack_id": "TARGET-01",
                    "current_location": {"type": "RACK_POSITION", "location_code": "OUT65"},
                    "current_face": "0",
                }
            ),
            expected_version=0,
            actor_id=42,
        )

    confirmations.create_or_get.assert_not_awaited()


@pytest.mark.asyncio
async def test_rack_phase_cannot_advance_before_current_source_arrives() -> None:
    run = _run(phase="RACK_TRANSPORT")
    steps = [
        IntegrationRunStep(
            run_id=run.run_id,
            ordinal=0,
            phase="RACK_TRANSPORT",
            status="SUCCEEDED",
            request_summary_json={"kind": "MOVE_RACK", "rack_id": "TARGET-01", "target_face": "0"},
        )
    ]
    service = IntegrationDebugService(
        _Sessions(),  # type: ignore[arg-type]
        repository=_Repository(run, steps),  # type: ignore[arg-type]
        confirmations=AsyncMock(),  # type: ignore[arg-type]
        transport=AsyncMock(),  # type: ignore[arg-type]
        device_commands=AsyncMock(),  # type: ignore[arg-type]
        publisher=AsyncMock(),  # type: ignore[arg-type]
    )

    with pytest.raises(IntegrationDebugConflict, match="当前来源货架"):
        await service.confirm_current_phase(run.run_id, note="已到位", expected_version=0, actor_id=42)
