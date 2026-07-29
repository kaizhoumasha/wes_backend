"""E08/E09 root claim 前的 demand 占位合同。"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Index

from src.app.runtime.orchestration.wms_rack_demand import WmsRackDemand


def _lifecycle_check_sql() -> str:
    return next(
        str(constraint.sqltext)
        for constraint in WmsRackDemand.__table__.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name == "ck_wms_rack_demands_lifecycle"
    )


def _active_demand_index() -> Index:
    return next(
        index
        for index in WmsRackDemand.__table__.indexes
        if index.name == "ux_wms_rack_demands_active_station_rack_type"
    )


def test_preparing_demand_allows_a_null_root_until_runtime_intent_claim() -> None:
    root_field = WmsRackDemand.model_fields["root_intent_id"]

    assert root_field.is_required() is False
    assert root_field.default is None
    assert WmsRackDemand.__table__.c.root_intent_id.nullable is True


def test_lifecycle_shape_requires_root_only_after_preparing() -> None:
    lifecycle = _lifecycle_check_sql()

    assert "lifecycle_state = 'PREPARING' AND root_intent_id IS NULL" in lifecycle
    assert "lifecycle_state = 'ACTIVE' AND root_intent_id IS NOT NULL" in lifecycle
    assert "lifecycle_state = 'RECONCILING' AND root_intent_id IS NOT NULL" in lifecycle
    assert "lifecycle_state = 'CLOSED' AND root_intent_id IS NOT NULL" in lifecycle


def test_preparing_demand_participates_in_the_active_station_rack_mutex() -> None:
    active = _active_demand_index()
    where = str(active.dialect_options["postgresql"]["where"])

    assert "PREPARING" in where
    assert "ACTIVE" in where
    assert "RECONCILING" in where
