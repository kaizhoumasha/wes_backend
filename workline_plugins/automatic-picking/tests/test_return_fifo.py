"""SCAN4 精确关联输入的类型边界，未定义生产扫码 wire。"""

from dataclasses import replace

import pytest
from automatic_picking.application.return_flow import MatchedScan4


@pytest.mark.parametrize(
    "changes",
    [
        {"evidence_id": True},
        {"return_id": 0},
        {"workline_id": -1},
        {"timestamp": False},
        {"timestamp": 0},
        {"timestamp": 2**63},
        {"return_id": 2**63},
        {"bin_code": ""},
        {"device_code": " "},
    ],
)
def test_exact_scan4_identity_rejects_invalid_values(changes):
    value = MatchedScan4(1, 2, 3, "A123456789", "SCAN4", 1000)
    with pytest.raises(ValueError):
        replace(value, **changes)
