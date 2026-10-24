"""运动前输入是明确业务 Evidence 与当前实际架面，不解析设备私有协议。"""

import pytest
from automatic_picking.application.return_allocation_flow import MatchedReturnFace, MatchedReturnMotion


@pytest.mark.parametrize("source", [True, 0, -1, 2**63, "1", None])
def test_motion_requires_exact_evidence_identity(source):
    with pytest.raises(ValueError):
        MatchedReturnMotion(MatchedReturnFace(1, 2, "R", "A"), source, "root", 4, 5)


def test_motion_does_not_accept_a_raw_face_mapping():
    with pytest.raises(ValueError):
        MatchedReturnMotion(
            {"arrival_evidence_id": 1, "workline_id": 2, "rack_id": "R", "rack_face": "A"}, 3, "root", 4, 5
        )


@pytest.mark.parametrize("field", ["source_feed_id", "service_end_evidence_id"])
@pytest.mark.parametrize("value", [True, 0, -1, 2**63, "1", None])
def test_motion_source_references_require_exact_bigint(field, value):
    kwargs = {"source_feed_id": 4, "service_end_evidence_id": 5}
    kwargs[field] = value
    with pytest.raises(ValueError):
        MatchedReturnMotion(MatchedReturnFace(1, 2, "R", "A"), 3, "root", **kwargs)


@pytest.mark.parametrize("root", [None, True, "", " ", "x" * 121])
def test_motion_requires_explicit_original_client_identity(root):
    with pytest.raises(ValueError):
        MatchedReturnMotion(MatchedReturnFace(1, 2, "R", "A"), 3, root, 4, 5)
