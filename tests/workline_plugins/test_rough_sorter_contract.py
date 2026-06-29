"""粗分机插件合同层测试。"""

import pytest

from src.app.runtime.orchestration.runtime_intent import BlockScope, RuntimeIntentKind
from src.app.workline.domain.contracts import SixInOne
from src.workline_plugins.rough_sorter.context import RoughSorterContext
from src.workline_plugins.rough_sorter.contract import (
    ACTION_MOVE_FORWARD,
    ACTION_MOVE_TO_NG,
    ACTION_PICK_AND_PUT,
    ACTION_PUT_TO_BIN,
    ACTION_TARGET_ROLES,
    PHASE_COMPLETED,
    PHASE_MOVING_FORWARD,
    PHASE_NG_MOVING,
    PHASE_PICK_TO_PIPELINE,
    PHASE_PUTTING_TO_BIN,
    ROLE_CONVEYOR,
    ROLE_INPUT_ARM,
    ROLE_OUTPUT_ARM,
    ROUGH_SORTER_RACK_WAIT_CONTEXT_STATE,
    ROUGH_SORTER_SCANNED_CONTEXT_STATE,
    build_move_forward_payload,
    build_move_to_ng_payload,
    build_pick_and_put_payload,
    build_put_to_bin_payload,
    classify_rough_sorter_result,
    normalize_six_in_one_payload,
    resolve_rough_sorter_business_key,
)
from src.workline_plugins.rough_sorter.plugin import RoughSorterPlugin


def _payload_data() -> dict[str, str]:
    return {
        "HHPN": "HH-001",
        "MfrPN": "MFR-001",
        "Qty": "1500",
        "DateCode": "260528",
        "LotCode": "LOT-A",
        "PkgID": "PKG-001",
    }


def test_normalize_six_in_one_payload_reads_only_data_and_normalizes_aliases() -> None:
    payload = {
        "PkgID": "TOP-LEVEL-MUST-BE-IGNORED",
        "LotCode": "TOP-LOT-MUST-BE-IGNORED",
        "data": {
            "ProductNo": "HH-ALIAS",
            "MfrPN": "MFR-001",
            "Qty": "1500",
            "DateCode": "260528",
            "LotCode": "LOT-A",
            "PONumber": "PKG-ALIAS",
        },
    }

    six_in_one = normalize_six_in_one_payload(payload)

    assert six_in_one.HHPN == "HH-ALIAS"
    assert six_in_one.PkgID == "PKG-ALIAS"
    assert six_in_one.LotCode == "LOT-A"


def test_business_key_is_derived_only_from_data_pkg_id() -> None:
    payload = {
        "PkgID": "TOP-LEVEL-MUST-BE-IGNORED",
        "business_key": "UPSTREAM-MUST-BE-IGNORED",
        "data": {"PkgID": "PKG-001"},
    }

    assert resolve_rough_sorter_business_key(payload) == SixInOne(PkgID="PKG-001").business_key


def test_business_key_returns_none_when_data_pkg_id_missing() -> None:
    assert resolve_rough_sorter_business_key({"PkgID": "TOP-LEVEL-MUST-BE-IGNORED", "data": {}}) is None


def test_phase_and_role_contracts_are_declared() -> None:
    assert {
        ROUGH_SORTER_SCANNED_CONTEXT_STATE,
        PHASE_PICK_TO_PIPELINE,
        PHASE_MOVING_FORWARD,
        ROUGH_SORTER_RACK_WAIT_CONTEXT_STATE,
        PHASE_PUTTING_TO_BIN,
        PHASE_NG_MOVING,
        PHASE_COMPLETED,
    } == {
        "SCANNED",
        "PICK_TO_PIPELINE",
        "MOVING_FORWARD",
        "WAITING_RACK",
        "PUTTING_TO_BIN",
        "NG_MOVING",
        "COMPLETED",
    }
    assert ACTION_TARGET_ROLES == {
        ACTION_PICK_AND_PUT: ROLE_INPUT_ARM,
        ACTION_MOVE_FORWARD: ROLE_CONVEYOR,
        ACTION_PUT_TO_BIN: ROLE_OUTPUT_ARM,
        ACTION_MOVE_TO_NG: ROLE_INPUT_ARM,
    }
    assert {ROLE_INPUT_ARM, ROLE_CONVEYOR, ROLE_OUTPUT_ARM} == {
        "ROUGH_SORTER_INPUT_ARM",
        "ROUGH_SORTER_CONVEYOR",
        "ROUGH_SORTER_OUTPUT_ARM",
    }


@pytest.mark.parametrize(
    ("builder", "expected_task_type"),
    [
        (
            lambda six: build_pick_and_put_payload(
                business_key=six.business_key or "",
                source_location="SCAN_POINT",
                target_location="PIPELINE_IN",
                six_in_one=six,
                trace_id="trace-001",
            ),
            "PICK_AND_PUT",
        ),
        (
            lambda six: build_move_forward_payload(
                business_key=six.business_key or "",
                source_location="PIPELINE_IN",
                target_location="PIPELINE_OUT",
            ),
            "MOVE_FORWARD",
        ),
        (
            lambda six: build_put_to_bin_payload(
                business_key=six.business_key or "",
                source_location="PIPELINE_OUT",
                bin_location="RACK-A-01",
            ),
            ACTION_PUT_TO_BIN,
        ),
        (
            lambda six: build_move_to_ng_payload(
                business_key=six.business_key or "",
                source_location="PIPELINE_OUT",
                ng_location="NG-01",
                reason_code="BARCODE_INVALID",
            ),
            ACTION_MOVE_TO_NG,
        ),
    ],
)
def test_command_builders_emit_concrete_task_type_without_params_action(
    builder,
    expected_task_type: str,
) -> None:
    six_in_one = SixInOne.model_validate(_payload_data())

    command_payload = builder(six_in_one)

    assert command_payload["task_type"] == expected_task_type
    assert "action" not in command_payload["params"]


def test_pick_and_put_payload_keeps_business_fields_under_params() -> None:
    six_in_one = SixInOne.model_validate(_payload_data())

    payload = build_pick_and_put_payload(
        business_key=six_in_one.business_key or "",
        source_location="SCAN_POINT",
        target_location="PIPELINE_IN",
        six_in_one=six_in_one,
        trace_id="trace-001",
    )

    assert payload["task_type"] == ACTION_PICK_AND_PUT
    assert payload["params"]["business_key"] == six_in_one.business_key
    assert payload["params"]["source_location"] == "SCAN_POINT"
    assert payload["params"]["target_location"] == "PIPELINE_IN"
    assert payload["params"]["trace_id"] == "trace-001"
    assert payload["params"]["six_in_one"]["PkgID"] == "PKG-001"
    assert "PkgID" not in payload


def test_result_classifier_marks_measurement_ng_as_business_decision() -> None:
    payload = {"result": "SUCCESS", "data": {"measurement_result": "NG"}}

    assert classify_rough_sorter_result(payload) == "business_decision"


def test_result_classifier_marks_thickness_ng_when_size_is_ok() -> None:
    payload = {
        "result": "SUCCESS",
        "data": {
            "size_judgement": "OK",
            "thickness_judgement": "NG",
        },
    }

    assert classify_rough_sorter_result(payload) == "business_decision"


def test_result_classifier_marks_failed_hardware_result() -> None:
    payload = {"result": "FAILED", "error_detail": {"error_code": "AXIS_ALARM"}}

    assert classify_rough_sorter_result(payload) == "hardware_failure"


def test_rough_sorter_context_is_serializable() -> None:
    context = RoughSorterContext(
        six_in_one=_payload_data(),
        business_key="biz-001",
        measurement={"reel_diameter": 12.3},
        wms_validation={"matched": True},
        target_bin_location="RACK-A-01",
        rack_operation={"operation_key": "op-001"},
        ng_reason={"reason_code": "BARCODE_INVALID"},
        phase=ROUGH_SORTER_SCANNED_CONTEXT_STATE,
    )

    dumped = context.model_dump(mode="json")

    assert dumped["six_in_one"]["PkgID"] == "PKG-001"
    assert dumped["phase"] == ROUGH_SORTER_SCANNED_CONTEXT_STATE


@pytest.mark.asyncio
async def test_registered_scan_event_dispatches_after_task2_handler_ships() -> None:
    plugin = RoughSorterPlugin()
    ctx = type(
        "Ctx",
        (),
        {
            "config": {},
            "logger": type("Logger", (), {"warning": lambda *_args: None})(),
            "trace_id": "trace-rough-sorter-contract",
        },
    )()
    inbox = type("Inbox", (), {"payload_json": {"event_type": "SCAN_COMPLETED", "data": _payload_data()}})()

    intents = await plugin.on_device_event(ctx, inbox)

    assert [intent.kind for intent in intents] == [
        RuntimeIntentKind.CREATE_MATERIAL_UNIT,
        RuntimeIntentKind.UPDATE_CONTEXT,
        RuntimeIntentKind.COMMAND,
    ]
    assert intents[1].context_patch["phase"] == PHASE_PICK_TO_PIPELINE
    assert intents[2].action == ACTION_PICK_AND_PUT
