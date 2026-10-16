"""精确 SCAN3 输入边界；不定义设备映射、扫码规则或私有 wire。"""

from dataclasses import replace

import pytest
from automatic_picking.application.return_flow import MatchedScan3


@pytest.mark.parametrize(
    "changes",
    [
        {"evidence_id": True},
        {"work_id": 0},
        {"workline_id": -1},
        {"timestamp": True},
        {"timestamp": -1},
        {"bin_code": ""},
        {"command_code": None},
        {"device_code": " "},
    ],
)
def test_scan3_requires_exact_typed_identity(changes):
    value = MatchedScan3(1, 2, 3, "A123456789", "original-command", "SCAN3", 123)
    with pytest.raises(ValueError):
        replace(value, **changes)
