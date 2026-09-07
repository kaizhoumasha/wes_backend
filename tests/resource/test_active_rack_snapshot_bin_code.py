"""资源快照使用实际料箱码，不再读写 bin_id 别名。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.resource.services.active_rack_snapshot_service import SmtActiveRackSnapshotService


@pytest.mark.asyncio
@pytest.mark.parametrize("occupied", [False, True])
@pytest.mark.parametrize("field", ["bin_code", "bin_id"])
async def test_active_rack_cells_use_bin_code_only(field: str, occupied: bool) -> None:
    bin_code = "000a/B-01"
    occupancy = SimpleNamespace(bin_code=bin_code, bin_cell_index="1", id=1)
    service = SmtActiveRackSnapshotService(
        rack_placement_repo=SimpleNamespace(
            list_active_by_workline_position=AsyncMock(return_value=[SimpleNamespace(rack_code="RACK-1")])
        ),
        rack_bin_mount_repo=SimpleNamespace(
            list_active_by_rack_code=AsyncMock(return_value=[SimpleNamespace(rack_slot_code="A01", bin_code=bin_code)])
        ),
        bin_cell_occupancy_repo=SimpleNamespace(
            list_active_by_bin_codes=AsyncMock(return_value=[occupancy] if occupied else [])
        ),
        bin_material_mount_repo=SimpleNamespace(list_active_by_bin_codes=AsyncMock(return_value=[])),
    )
    snapshot = await service.get_active_bin_rack(
        object(),
        workline=SimpleNamespace(line_code="LINE-1"),
        context={
            "position_code": "POSITION-1",
            "active_bin_rack": {
                "rack_code": "RACK-1",
                "cells": [{"rack_slot_code": "A01", field: bin_code, "bin_cell_index": "1"}],
            },
        },
    )
    if field == "bin_id":
        assert snapshot is None
    else:
        assert snapshot is not None
        cell = snapshot["cells"][0]
        assert cell["bin_code"] == bin_code
        assert "bin_id" not in cell
        assert cell["status"] == ("OCCUPIED" if occupied else "EMPTY")
