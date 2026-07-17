"""粗分机类型化配置、状态与输入合同。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.app.runtime.workline_plugins.rough_sorter.config import RoughSorterConfig
from src.app.runtime.workline_plugins.rough_sorter.inputs import (
    BusinessTimeoutInput,
    PickAndPutResultInput,
    ReplayRequestInput,
    ScanCompletedInput,
    parse_business_timeout,
    parse_pick_and_put_result,
    parse_replay_request,
    parse_scan_completed,
)
from src.app.runtime.workline_plugins.rough_sorter.state import RoughSorterState


def _config_payload() -> dict[str, object]:
    return {
        "device_roles": {
            "input_arm": "ROUGH_SORTER_INPUT_ARM",
            "conveyor": "ROUGH_SORTER_CONVEYOR",
            "output_arm": "ROUGH_SORTER_OUTPUT_ARM",
        },
        "pipeline_input_location": "PIPELINE-IN-01",
        "pipeline_output_location": "PIPELINE-OUT-01",
        "ng_location": "NG-01",
        "warehouse_code": "WH-01",
        "owner_code": "OWNER-01",
        "provider_profile": "wms.2026-07-06.material-flow.sandbox",
    }


def test_config_locks_roles_locations_and_provider_profile() -> None:
    config = RoughSorterConfig.model_validate(_config_payload())

    assert config.device_roles.input_arm == "ROUGH_SORTER_INPUT_ARM"
    assert config.pipeline_input_location == "PIPELINE-IN-01"
    assert config.warehouse_code == "WH-01"
    assert config.provider_profile == "wms.2026-07-06.material-flow.sandbox"


@pytest.mark.parametrize("field", ["warehouse_code", "owner_code", "provider_profile"])
def test_config_rejects_missing_admission_fields(field: str) -> None:
    payload = _config_payload()
    payload.pop(field)

    with pytest.raises(ValidationError):
        RoughSorterConfig.model_validate(payload)


def test_state_contains_only_local_orchestration_references() -> None:
    state = RoughSorterState(
        phase="PICK_TO_PIPELINE",
        measurement_evidence_ref="timeline:11",
        wms_evidence_ref="timeline:12",
        current_correlation="CMD-PICK-001",
    )

    assert set(state.model_dump()) == {
        "phase",
        "measurement_evidence_ref",
        "wms_evidence_ref",
        "current_correlation",
    }
    assert not {"material_unit", "command", "six_in_one", "business_key"} & set(RoughSorterState.model_fields)


def test_logical_input_parsers_are_typed_and_forbid_unknown_fields() -> None:
    scan = parse_scan_completed({"data": {"PkgID": "PKG-001"}})
    result = parse_pick_and_put_result(
        {
            "command_code": "CMD-001",
            "command_type": "PICK_AND_PUT",
            "result": "SUCCESS",
            "data": {"measurement_result": "OK", "reel_diameter": 180, "reel_thickness": 16},
        }
    )
    timeout = parse_business_timeout({"command_code": "CMD-001", "wait_type": "COMMAND_RESULT"})
    replay = parse_replay_request(
        {"idempotency_key": "rough-sorter:PKG-001:scan-decision", "payload_digest": "sha256:same"}
    )

    assert isinstance(scan, ScanCompletedInput)
    assert isinstance(result, PickAndPutResultInput)
    assert isinstance(timeout, BusinessTimeoutInput)
    assert isinstance(replay, ReplayRequestInput)
    with pytest.raises(ValidationError):
        ScanCompletedInput.model_validate({"payload": {"data": {}}, "unexpected": True})
