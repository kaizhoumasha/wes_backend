"""粗分业务资源 blocker 合同。"""

from __future__ import annotations

import pytest

from rough_sorter.application.business_blocker import RoughSorterBusinessBlocker


class _Placements:
    def __init__(self, count: int, sample: dict[str, str] | None) -> None:
        self._summary = {"count": count, "sample": sample}

    async def get_active_workline_summary(self, _db: object, workline_id: int) -> dict[str, object]:
        assert workline_id == 7
        return self._summary


@pytest.mark.asyncio
async def test_business_blocker_returns_exact_counts_and_per_type_samples() -> None:
    rack_sample = {"type": "rack_placement", "id": "11", "status": "ARRIVED", "identity": "RACK-1"}
    bin_sample = {"type": "bin_placement", "id": "21", "status": "UNKNOWN", "identity": "BIN-1"}
    projection_sample = {
        "type": "position_projection",
        "id": "31",
        "status": "OUTLET-1",
        "identity": "RACK:RACK-1",
    }
    blocker = RoughSorterBusinessBlocker(
        rack_placements=_Placements(2, rack_sample),
        bin_placements=_Placements(3, bin_sample),
        position_projections=_Placements(1, projection_sample),
    )

    summary = await blocker.get_unfinished_workload_summary(object(), 7)

    assert summary == {
        "count": 6,
        "by_type": {"rack_placements": 2, "bin_placements": 3, "position_projections": 1},
        "sample": rack_sample,
        "samples": {
            "rack_placements": rack_sample,
            "bin_placements": bin_sample,
            "position_projections": projection_sample,
        },
    }
