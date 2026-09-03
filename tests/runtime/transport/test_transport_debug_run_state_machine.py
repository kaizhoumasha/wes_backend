from __future__ import annotations

from datetime import datetime

import pytest

from src.app.transport.contracts import (
    BinMove,
    HandoffPosition,
    MoveBinsRequest,
    MoveRackRequest,
    RackBinSlot,
    RackPosition,
    RackReference,
    RcsTemplateId,
    RotateRackRequest,
    TransportCaller,
    ZonePosition,
)
from src.app.transport.debug_run_state_machine import (
    build_debug_transport_request,
    evaluate_debug_transport_task,
    next_debug_step,
)
from src.app.transport.models import TransportDebugRun, TransportDebugRunStep, TransportMember, TransportTask

NOW = datetime(2026, 9, 2, 12, 0, 0)
CLIENT_ID = "0199-INVALID"
CLIENT_IDS = (
    "01990f0d-1800-7000-8000-000000000001",
    "01990f0d-1800-7000-8000-000000000002",
    "01990f0d-1800-7000-8000-000000000003",
    "01990f0d-1800-7000-8000-000000000004",
    "01990f0d-1800-7000-8000-000000000005",
)


def _run(*, phase: str = "RACK_TO_STATION", group_index: int = 0) -> TransportDebugRun:
    return TransportDebugRun(
        run_id="debug-run-1",
        status="RUNNING",
        active_scope="GLOBAL",
        rack_id="510056",
        configuration_json={
            "rack_id": "510056",
            "face_groups": [
                {
                    "face": " 90 ",
                    "bins": [
                        {"bin_id": "A000001922", "slot_id": "510056A3F2C101"},
                        {"bin_id": "A000002653", "slot_id": "510056A3F2C102"},
                    ],
                },
                {
                    "face": "270",
                    "bins": [{"bin_id": "A000003001", "slot_id": "510056A2F2C101"}],
                },
            ],
            "storage_zone": "WH01",
            "workstation": "KT16",
            "infeed_position": "CNV0301",
            "outfeed_position": "CNV0302",
            "rack_out_template": "CTU01",
            "rack_rotate_template": "CTU02",
            "rack_return_template": "CTU03",
            "rack_return_face": "90",
        },
        current_group_index=group_index,
        current_phase=phase,
        current_step_ordinal=0,
        version=1,
        created_by_user_id=7,
        created_at=NOW,
        updated_at=NOW,
    )


def _step(phase: str, *, group_index: int = 0, client_id: str = CLIENT_IDS[0]) -> TransportDebugRunStep:
    return TransportDebugRunStep(
        run_id="debug-run-1",
        ordinal=0,
        group_index=group_index,
        phase=phase,
        status="PENDING",
        client_request_id=None if phase == "WAIT_SCAN12" else client_id,
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.mark.parametrize(
    ("phase", "group_index", "expected"),
    [
        (
            "RACK_TO_STATION",
            0,
            MoveRackRequest(
                CLIENT_IDS[0],
                TransportCaller("TRANSPORT_DEBUG", "TRANSPORT_DEBUG_AUTO"),
                "510056",
                RackReference("510056"),
                RackPosition("KT16"),
                " 90 ",
                RcsTemplateId.CTU01,
            ),
        ),
        (
            "BINS_TO_INFEED",
            0,
            MoveBinsRequest(
                CLIENT_IDS[0],
                TransportCaller("TRANSPORT_DEBUG", "TRANSPORT_DEBUG_AUTO"),
                (
                    BinMove(
                        "A000001922",
                        RackBinSlot("510056", " 90 ", "510056A3F2C101"),
                        HandoffPosition("CNV0301"),
                    ),
                    BinMove(
                        "A000002653",
                        RackBinSlot("510056", " 90 ", "510056A3F2C102"),
                        HandoffPosition("CNV0301"),
                    ),
                ),
            ),
        ),
        (
            "BINS_TO_RACK",
            0,
            MoveBinsRequest(
                CLIENT_IDS[0],
                TransportCaller("TRANSPORT_DEBUG", "TRANSPORT_DEBUG_AUTO"),
                (
                    BinMove(
                        "A000001922",
                        HandoffPosition("CNV0302"),
                        RackBinSlot("510056", " 90 ", "510056A3F2C101"),
                    ),
                    BinMove(
                        "A000002653",
                        HandoffPosition("CNV0302"),
                        RackBinSlot("510056", " 90 ", "510056A3F2C102"),
                    ),
                ),
            ),
        ),
        (
            "ROTATE_TO_NEXT_FACE",
            1,
            RotateRackRequest(
                CLIENT_IDS[0],
                TransportCaller("TRANSPORT_DEBUG", "TRANSPORT_DEBUG_AUTO"),
                "510056",
                RackReference("510056"),
                "270",
                RcsTemplateId.CTU02,
            ),
        ),
        (
            "RACK_TO_STORAGE",
            1,
            MoveRackRequest(
                CLIENT_IDS[0],
                TransportCaller("TRANSPORT_DEBUG", "TRANSPORT_DEBUG_AUTO"),
                "510056",
                RackReference("510056"),
                ZonePosition("WH01"),
                "90",
                RcsTemplateId.CTU03,
            ),
        ),
        ("WAIT_SCAN12", 0, None),
    ],
)
def test_build_debug_transport_request_preserves_frozen_values(
    phase: str,
    group_index: int,
    expected: object,
) -> None:
    run = _run(phase=phase, group_index=group_index)

    assert build_debug_transport_request(run, _step(phase, group_index=group_index)) == expected


@pytest.mark.parametrize(
    ("phase", "group_index", "expected"),
    [
        ("RACK_TO_STATION", 0, ("BINS_TO_INFEED", 0)),
        ("BINS_TO_INFEED", 0, ("WAIT_SCAN12", 0)),
        ("WAIT_SCAN12", 0, ("BINS_TO_RACK", 0)),
        ("BINS_TO_RACK", 0, ("ROTATE_TO_NEXT_FACE", 1)),
        ("ROTATE_TO_NEXT_FACE", 1, ("BINS_TO_INFEED", 1)),
        ("BINS_TO_RACK", 1, ("RACK_TO_STORAGE", 1)),
        ("RACK_TO_STORAGE", 1, None),
    ],
)
def test_next_debug_step_follows_multi_face_sequence(
    phase: str,
    group_index: int,
    expected: tuple[str, int] | None,
) -> None:
    run = _run(phase=phase, group_index=group_index)

    assert next_debug_step(run, _step(phase, group_index=group_index)) == expected


def _task(status: str, *, reason_code: str | None = None) -> TransportTask:
    return TransportTask(
        transport_task_id="transport-1",
        client_request_id=CLIENT_IDS[0],
        request_digest="0" * 64,
        kind="RACK_MOVE",
        caller_json={"workline_id": "TRANSPORT_DEBUG", "station_id": "TRANSPORT_DEBUG_AUTO"},
        request_json={},
        submit_operation_id=CLIENT_IDS[1],
        submit_timestamp_ms=1,
        submit_request_body="{}",
        submit_request_body_digest="1" * 64,
        status=status,
        reason_code=reason_code,
        created_at=NOW,
        updated_at=NOW,
    )


def _member(
    *,
    object_id: str = "510056",
    final_position: dict[str, str] | None = None,
    arrival_face: str | None = " 90 ",
    position_unknown: bool = False,
) -> TransportMember:
    return TransportMember(
        transport_task_id="transport-1",
        ordinal=0,
        object_type="RACK",
        object_id=object_id,
        source_json={"kind": "RACK", "location_code": "510056"},
        target_json={"kind": "RACK_POSITION", "location_code": "KT16"},
        status="SUCCEEDED",
        final_position_json=final_position or {"kind": "RACK_POSITION", "location_code": "KT16"},
        position_unknown=position_unknown,
        arrival_face=arrival_face,
        updated_at=NOW,
    )


@pytest.mark.parametrize(
    ("status", "reason_code", "disposition", "expected_reason"),
    [
        ("PENDING", None, "WAIT", None),
        ("ACCEPTED", None, "WAIT", None),
        ("REJECTED", "NO_ROUTE", "FAILED", "NO_ROUTE"),
        ("FAILED", "DEVICE_ERROR", "FAILED", "DEVICE_ERROR"),
        ("RECONCILING", "TRANSPORT_DELIVERY_UNKNOWN", "ATTENTION", "TRANSPORT_DELIVERY_UNKNOWN"),
    ],
)
def test_evaluate_debug_transport_task_maps_authoritative_statuses(
    status: str,
    reason_code: str | None,
    disposition: str,
    expected_reason: str | None,
) -> None:
    result = evaluate_debug_transport_task(
        _step("RACK_TO_STATION"),
        _task(status, reason_code=reason_code),
        (),
        _run(),
    )

    assert result.disposition == disposition
    assert result.reason_code == expected_reason


def test_evaluate_debug_transport_task_requires_exact_success_result() -> None:
    step = _step("RACK_TO_STATION")
    task = _task("SUCCEEDED")

    succeeded = evaluate_debug_transport_task(step, task, (_member(),), _run())
    wrong_face = evaluate_debug_transport_task(
        step,
        task,
        (_member(arrival_face="90"),),
        _run(),
    )
    unknown_position = evaluate_debug_transport_task(
        step,
        task,
        (_member(final_position=None, position_unknown=True),),
        _run(),
    )

    assert succeeded.disposition == "SUCCEEDED"
    assert wrong_face == type(wrong_face)("ATTENTION", "TRANSPORT_RESULT_MISMATCH")
    assert unknown_position == type(unknown_position)("ATTENTION", "TRANSPORT_RESULT_MISMATCH")


def test_evaluate_bin_return_accepts_formal_result_without_arrival_face() -> None:
    run = _run(phase="BINS_TO_RACK")
    step = _step("BINS_TO_RACK")
    request = build_debug_transport_request(run, step)
    assert isinstance(request, MoveBinsRequest)
    task = _task("SUCCEEDED")
    task.kind = "BIN_MOVE"
    members = tuple(
        TransportMember(
            transport_task_id="transport-1",
            ordinal=ordinal,
            object_type="BIN",
            object_id=move.bin_id,
            source_json={"kind": "HANDOFF_POSITION", "location_code": "CNV0302"},
            target_json={
                "kind": "RACK_BIN_SLOT",
                "rack_id": move.target.rack_id,
                "rack_face": move.target.rack_face,
                "slot_id": move.target.slot_id,
            },
            status="SUCCEEDED",
            final_position_json={
                "kind": "RACK_BIN_SLOT",
                "rack_id": move.target.rack_id,
                "rack_face": move.target.rack_face,
                "slot_id": move.target.slot_id,
            },
            position_unknown=False,
            arrival_face=None,
            updated_at=NOW,
        )
        for ordinal, move in enumerate(request.moves)
        if isinstance(move.target, RackBinSlot)
    )

    assert evaluate_debug_transport_task(step, task, members, run).disposition == "SUCCEEDED"


def test_evaluate_debug_transport_task_rejects_missing_or_duplicate_members() -> None:
    step = _step("RACK_TO_STATION")
    task = _task("SUCCEEDED")

    assert evaluate_debug_transport_task(step, task, (), _run()).disposition == "ATTENTION"
    assert evaluate_debug_transport_task(step, task, (_member(), _member()), _run()).disposition == "ATTENTION"


def test_evaluate_rotate_accepts_rack_reference_intent_and_exact_station_result() -> None:
    run = _run(phase="ROTATE_TO_NEXT_FACE", group_index=1)
    step = _step("ROTATE_TO_NEXT_FACE", group_index=1)
    task = _task("SUCCEEDED")
    task.kind = "RACK_ROTATE"
    member = _member(
        final_position={"kind": "RACK_POSITION", "location_code": "KT16"},
        arrival_face="270",
    )
    member.source_json = {"kind": "RACK_POSITION", "location_code": "KT16"}
    member.target_json = {"kind": "RACK_POSITION", "location_code": "KT16"}

    assert evaluate_debug_transport_task(step, task, (member,), run).disposition == "SUCCEEDED"
