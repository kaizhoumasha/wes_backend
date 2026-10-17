"""运动前输入是明确业务 Evidence 与当前实际架面，不解析设备私有协议。"""

import pytest
from automatic_picking.application.return_allocation_flow import MatchedReturnFace, MatchedReturnMotion


@pytest.mark.parametrize("source", [True, 0, -1, 2**63, "1", None])
def test_motion_requires_exact_evidence_identity(source):
    with pytest.raises(ValueError):
        MatchedReturnMotion(MatchedReturnFace(1, 2, "R", "A"), source)


def test_motion_does_not_accept_a_raw_face_mapping():
    with pytest.raises(ValueError):
        MatchedReturnMotion({"arrival_evidence_id": 1, "workline_id": 2, "rack_id": "R", "rack_face": "A"}, 3)
