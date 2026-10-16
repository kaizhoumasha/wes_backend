"""8C typed reader 的精确身份与后置条件边界。"""

import pytest
from automatic_picking.application.return_flow import MatchedReturnDirection


@pytest.mark.parametrize(
    "change",
    [
        {"evidence_id": True},
        {"return_id": 0},
        {"workline_id": 2**63},
        {"role": "SCAN2"},
        {"at_outlet": 1},
        {"bin_code": " "},
        {"command_code": ""},
        {"device_code": None},
    ],
)
def test_invalid_result_identity_cannot_be_given_to_application(change):
    value = {
        "evidence_id": 1,
        "return_id": 2,
        "workline_id": 3,
        "bin_code": "A123456789",
        "role": "SCAN4",
        "command_code": "CMD-8C",
        "device_code": "OFFLINE-SCAN4",
        "at_outlet": True,
    }
    with pytest.raises(ValueError):
        MatchedReturnDirection(**{**value, **change})
