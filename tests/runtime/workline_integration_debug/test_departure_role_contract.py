"""人工出库联调按冻结货架角色解释离场去向。"""

from types import SimpleNamespace

import pytest

from src.app.workline_integration_debug.service import IntegrationDebugConflict, IntegrationDebugService


@pytest.mark.parametrize(
    ("role", "accepted_type", "rejected_type"),
    [
        ("SOURCE_RACK", "ZONE", "RACK_POSITION"),
        ("TARGET_RACK", "RACK_POSITION", "ZONE"),
    ],
)
def test_departure_ready_destination_type_follows_frozen_rack_role(
    role: str, accepted_type: str, rejected_type: str
) -> None:
    run = SimpleNamespace(
        workline_code="KT16",
        status="WAITING_EXTERNAL",
        configuration_json={
            "departure_candidate": {
                "rack_id": "RACK-01",
                "rack_face": "90",
                "current_location": "KT16" if role == "SOURCE_RACK" else "OUT65",
                "role": role,
            }
        },
    )
    step = SimpleNamespace(request_summary_json={"rack_id": "RACK-01"})

    with pytest.raises(IntegrationDebugConflict, match="rack_destination"):
        IntegrationDebugService._advance_departure(
            run,
            step,
            "READY",
            {"rack_destination": {"type": rejected_type, "location_code": "WH05"}},
        )

    IntegrationDebugService._advance_departure(
        run,
        step,
        "READY",
        {"rack_destination": {"type": accepted_type, "location_code": "WH05"}},
    )

    assert run.configuration_json["rack_destination"] == {"kind": "ZONE", "location_code": "WH05"}


def test_departure_ready_rejects_missing_frozen_rack_role() -> None:
    run = SimpleNamespace(
        workline_code="KT16",
        status="WAITING_EXTERNAL",
        configuration_json={"departure_candidate": {"rack_id": "RACK-01"}},
    )
    step = SimpleNamespace(request_summary_json={"rack_id": "RACK-01"})

    with pytest.raises(IntegrationDebugConflict, match="rack_destination"):
        IntegrationDebugService._advance_departure(
            run,
            step,
            "READY",
            {"rack_destination": {"type": "ZONE", "location_code": "WH05"}},
        )
