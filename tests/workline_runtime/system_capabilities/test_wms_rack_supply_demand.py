"""rack supply demand 保留单一 WMS 履约投影 owner。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.app.runtime.orchestration.material_flow_owner import MaterialFlowOwner
from src.app.runtime.orchestration.services.rack_demand_service import RackDemandService
from src.app.runtime.orchestration.wms_rack_demand import WmsRackDemand


class _Repository:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] | None = None

    async def reserve_preparing_demand(self, _db: Any, **kwargs: Any) -> tuple[Any, bool]:
        self.kwargs = kwargs
        return (
            SimpleNamespace(
                id=41,
                workline_id=22,
                station_code=kwargs["station_code"],
                rack_type=kwargs["rack_type"],
                demand_generation=kwargs["demand_generation"],
            ),
            True,
        )


@pytest.mark.asyncio
async def test_rack_demand_service_only_reserves_rack_supply_root() -> None:
    repository = _Repository()
    ctx = {
        "db": object(),
        "session": SimpleNamespace(id=11),
        "workline": SimpleNamespace(id=22),
    }

    reservation = await RackDemandService(repository=repository, now_ms=lambda: 123).reserve_root(
        ctx,
        station_code="STATION-001",
        rack_type="SINGLE_LAYER",
        demand_generation=3,
        dispatch_key="rack-supply-001",
    )

    assert repository.kwargs == {
        "workline_id": 22,
        "station_code": "STATION-001",
        "rack_type": "SINGLE_LAYER",
        "demand_generation": 3,
        "opened_at_ms": 123,
    }
    assert reservation.operation is not None
    assert reservation.operation.identity == "wms.fulfillment.request_rack_supply@v1"
    assert reservation.request is not None
    assert reservation.request.model_dump() == {
        "dispatch_key": "rack-supply-001",
        "station_code": "STATION-001",
        "rack_type": "SINGLE_LAYER",
        "demand_generation": 3,
    }


def test_wms_rack_demand_schema_contains_only_rack_supply_fields() -> None:
    columns = set(WmsRackDemand.__table__.columns.keys())
    constraints = {constraint.name for constraint in WmsRackDemand.__table__.constraints}

    assert columns == {
        "id",
        "workline_id",
        "station_code",
        "rack_type",
        "demand_generation",
        "root_intent_id",
        "lifecycle_state",
        "opened_at_ms",
        "closed_at_ms",
        "reconciliation_case_id",
    }
    assert constraints == {
        "ck_wms_rack_demands_generation",
        "ck_wms_rack_demands_lifecycle",
        "fk_wms_rack_demands_reconciliation_case",
        "fk_wms_rack_demands_root_intent",
        "pk_wms_rack_demands",
        "uq_wms_rack_demands_generation",
        "uq_wms_rack_demands_root_intent",
    }


def test_material_flow_owner_schema_keeps_shared_owners_without_e09_transport_owner() -> None:
    owner_type_constraint = next(
        constraint
        for constraint in MaterialFlowOwner.__table__.constraints
        if constraint.name == "ck_material_flow_owners_owner_type"
    )
    expression = str(owner_type_constraint.sqltext)

    assert "FULL_BOX_EXCHANGE" in expression
    assert "PIECE_SORTING" in expression
    assert "STATION_TRANSPORT" not in expression
