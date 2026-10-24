"""离线位置/架面读口的严格身份边界。"""

import pytest
from automatic_picking.application.return_allocation_flow import MatchedReturnFace, MatchedReturnLocation


@pytest.mark.parametrize(
    "bad",
    [
        {"arrival_evidence_id": True},
        {"workline_id": 0},
        {"arrival_evidence_id": 2**63},
        {"rack_id": 123},
        {"rack_face": 123},
        {"rack_id": " "},
        {"rack_face": ""},
    ],
)
def test_face_requires_explicit_typed_identity(bad):
    with pytest.raises(ValueError):
        MatchedReturnFace(**{"arrival_evidence_id": 1, "workline_id": 2, "rack_id": "R1", "rack_face": "A", **bad})


@pytest.mark.parametrize(
    "bad",
    [
        {"return_id": False},
        {"workline_id": 0},
        {"bin_code": ""},
        {"location_code": " "},
    ],
)
def test_location_cannot_guess_identity_or_handoff_code(bad):
    with pytest.raises(ValueError):
        MatchedReturnLocation(
            **{"return_id": 1, "workline_id": 2, "bin_code": "BIN-1", "location_code": "OUTLET-1", **bad}
        )
