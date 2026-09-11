"""第 4 步按已应用计划中的资源角色创建货架出库搬运。"""

from dataclasses import replace

import pytest

from src.app.transport.submit_snapshot import build_submit_data
from src.app.workline_integration_debug.contracts import (
    IntegrationDebugPhase,
    IntegrationTransportAction,
    IntegrationTransportActionKind,
)
from src.app.workline_integration_debug.service import IntegrationDebugContractError, IntegrationDebugService
from src.app.workline_integration_debug.transport import build_transport_request

PLAN = {
    "target_rack": {"rack_id": "610007", "rack_face": "90"},
    "direct_picks": [],
    "bin_source_racks": [
        {"rack_id": "510002", "rack_face": "90", "plan_revision": 1},
        {"rack_id": "510012", "rack_face": "270", "plan_revision": 1},
    ],
}


def action(rack_id="610007", template="F01", position="OUT65", face="90"):
    return IntegrationTransportAction(
        kind=IntegrationTransportActionKind.MOVE_RACK,
        client_request_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4472",
        rack_id=rack_id,
        source={"kind": "RACK", "location_code": rack_id},
        target={"kind": "RACK_POSITION", "location_code": position},
        target_face=face,
        rcs_template_id=template,
    )


@pytest.mark.parametrize(
    "transport_action", [action(), action("510002", "CTU01", "KT16"), action("510012", "CTU01", "KT17", "270")]
)
def test_role_selects_template_position_and_preserves_plan_face(transport_action):
    IntegrationDebugService._validate_manual_outbound_transport(
        IntegrationDebugPhase.RACK_TRANSPORT, transport_action, PLAN
    )
    wire = build_submit_data(build_transport_request(transport_action), "transport-1")
    assert wire["source"] == {"kind": "RACK", "location_code": transport_action.rack_id}
    assert wire["target"] == transport_action.target
    assert wire["rcs_template_id"] == transport_action.rcs_template_id
    assert wire["target_face"] == transport_action.target_face


@pytest.mark.parametrize(
    "transport_action",
    [
        action(template="CTU01"),
        action(position="KT16"),
        action(face="270"),
        action("510002", "F01", "KT16"),
        action("510002", "CTU01", "OUT65"),
        action("unplanned"),
        replace(action(), source={"kind": "RACK_POSITION", "location_code": "610007"}),
        replace(action(), kind=IntegrationTransportActionKind.ROTATE_RACK),
    ],
)
def test_invalid_resource_combinations_are_rejected(transport_action):
    with pytest.raises(IntegrationDebugContractError):
        IntegrationDebugService._validate_manual_outbound_transport(
            IntegrationDebugPhase.RACK_TRANSPORT, transport_action, PLAN
        )
