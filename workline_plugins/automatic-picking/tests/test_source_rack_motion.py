"""普通来源换面只接收显式完整输入，不补设备默认值。"""

from dataclasses import FrozenInstanceError

import pytest
from automatic_picking.application.source_rack_motion import MatchedSourceRackRotation
from wes_plugin_sdk import TransportRackPosition


@pytest.mark.parametrize(
    "field,value",
    [
        ("motion_id", True),
        ("motion_id", 0),
        ("next_member_id", -1),
        ("next_member_id", 2**63),
        ("rack_id", " "),
        ("target_face", ""),
        ("position", {"location_code": "P"}),
    ],
)
def test_rejects_incomplete_or_untyped_rotation(field, value):
    values = {
        "motion_id": 1,
        "next_member_id": 2,
        "rack_id": "R",
        "position": TransportRackPosition("P"),
        "target_face": "B",
    }
    values[field] = value
    with pytest.raises(ValueError):
        MatchedSourceRackRotation(**values)


def test_rotation_input_is_immutable():
    value = MatchedSourceRackRotation(1, 2, "R", TransportRackPosition("P"), "B")
    with pytest.raises(FrozenInstanceError):
        value.target_face = "C"
