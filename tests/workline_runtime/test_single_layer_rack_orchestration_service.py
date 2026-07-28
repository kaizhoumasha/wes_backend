"""旧单层货架 transport 编排的 fail-closed 回归。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.app.runtime.capabilities.material_flow.single_layer_rack_orchestration_service import (
    SingleLayerRackOrchestrationService,
)


@pytest.mark.asyncio
async def test_single_layer_rack_dispatch_waits_for_t5() -> None:
    with pytest.raises(RuntimeError, match="T5 dispatcher is not implemented"):
        await SingleLayerRackOrchestrationService().plan_single_layer_rack_dispatch(
            None,
            business_demand_key="demand-001",
            demand_type="SUPPLY",
            workline=SimpleNamespace(id=1, line_code="LINE-1"),
            station_code="STATION-1",
        )
