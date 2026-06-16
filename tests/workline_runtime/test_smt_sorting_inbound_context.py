"""SMT 分拣入库 typed Session context 合同测试。"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.workline_plugins.smt_sorting_inbound.constants import SORTING_CONTEXT_SCHEMA_VERSION
from src.workline_plugins.smt_sorting_inbound.context import (
    SortingInboundContext,
    SortingInboundContextError,
)


def _session(context_json: dict[str, object] | None = None) -> SimpleNamespace:
    return SimpleNamespace(context_json=context_json or {})


def _source_pick_request_kwargs(**overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "handoff_demand_id": 1,
        "handoff_source_item_id": 2,
        "claim_attempt_no": 1,
        "event_id": "smt-inbound-handoff-source-item:2:claim:1",
        "target_workline_code": "WL-SMT-SORT-01",
        "manifest_contract_version": "2026-06-01.p0",
        "source_rack_position_code": "SOURCE_STATION_A",
        "target_rack_position_code": "TARGET_STATION",
        "route_evidence": {"usage": Decimal("0.42")},
    }
    kwargs.update(overrides)
    return kwargs


def test_load_for_automatic_refuses_missing_sorting_schema() -> None:
    session = _session()

    with pytest.raises(SortingInboundContextError, match="context_schema_version"):
        SortingInboundContext.load_for_automatic(session)


def test_load_for_automatic_refuses_incompatible_sorting_schema() -> None:
    session = _session({"sorting": {"context_schema_version": 999}})

    with pytest.raises(SortingInboundContextError, match="不兼容"):
        SortingInboundContext.load_for_automatic(session)


def test_initialize_writes_schema_version_by_replacing_session_context() -> None:
    original_context: dict[str, object] = {"operator": "alice"}
    session = _session(original_context)

    context = SortingInboundContext.initialize(session)

    assert session.context_json is not original_context
    assert session.context_json["operator"] == "alice"
    assert session.context_json["sorting"]["context_schema_version"] == SORTING_CONTEXT_SCHEMA_VERSION
    assert context.sorting["context_schema_version"] == SORTING_CONTEXT_SCHEMA_VERSION


def test_current_material_can_be_opened_updated_and_closed() -> None:
    session = _session()
    context = SortingInboundContext.initialize(session)

    context.open_current_material(
        source_bin_code="SRC-BIN-01",
        source_cell_code="A01",
        material_identity_key="mid:pkg-001",
        reel_thickness_mm=Decimal("7.125"),
        evidence={"source_command_id": 101},
    )

    assert session.context_json["sorting"]["current_material"] == {
        "source_bin_code": "SRC-BIN-01",
        "source_cell_code": "A01",
        "material_identity_key": "mid:pkg-001",
        "reel_thickness_mm": "7.125",
        "evidence": {"source_command_id": 101},
    }

    context.update_current_material(scan_barcode="PKG-001", reel_thickness_mm="7.250")

    assert session.context_json["sorting"]["current_material"]["scan_barcode"] == "PKG-001"
    assert session.context_json["sorting"]["current_material"]["reel_thickness_mm"] == "7.250"

    context.close_current_material()

    assert "current_material" not in session.context_json["sorting"]


def test_pending_target_placement_is_written_and_cleared() -> None:
    session = _session()
    context = SortingInboundContext.initialize(session)

    context.write_pending_target_placement(
        target_bin_code="TGT-BIN-01",
        target_cell_code="B02",
        material_identity_key="mid:pkg-001",
        reel_thickness_mm=Decimal("7.125"),
        allocation_snapshot_version=12,
        capacity_evidence={"remaining_depth_mm": Decimal("30.500")},
    )

    assert session.context_json["sorting"]["pending_target_placement"] == {
        "target_bin_code": "TGT-BIN-01",
        "target_cell_code": "B02",
        "material_identity_key": "mid:pkg-001",
        "reel_thickness_mm": "7.125",
        "allocation_snapshot_version": 12,
        "capacity_evidence": {"remaining_depth_mm": "30.500"},
    }

    context.clear_pending_target_placement()

    assert "pending_target_placement" not in session.context_json["sorting"]


def test_active_target_bin_and_station_fields_are_typed_writes() -> None:
    session = _session()
    context = SortingInboundContext.initialize(session)

    context.set_active_target_bin("TGT-BIN-01")
    context.set_station_state(scan_platform="OCCUPIED", business_phase="WAITING_SCAN")

    sorting_context = session.context_json["sorting"]
    assert sorting_context["active_target_bin_code"] == "TGT-BIN-01"
    assert sorting_context["stations"] == {"scan_platform": "OCCUPIED"}
    assert sorting_context["business_phase"] == "WAITING_SCAN"


def test_source_pick_request_is_written_with_schema_and_station_state() -> None:
    session = _session()
    context = SortingInboundContext.initialize(session)

    context.write_source_pick_request(**_source_pick_request_kwargs())
    context.set_station_state(scan_platform="EMPTY")

    sorting = session.context_json["sorting"]
    assert sorting["context_schema_version"] == SORTING_CONTEXT_SCHEMA_VERSION
    assert sorting["stations"]["scan_platform"] == "EMPTY"
    assert sorting["source_pick_request"]["handoff_source_item_id"] == 2
    assert sorting["source_pick_request"]["route_evidence"]["usage"] == "0.42"


@pytest.mark.parametrize("field_name", ["handoff_demand_id", "claim_attempt_no"])
def test_source_pick_request_rejects_non_positive_integer_fields(field_name: str) -> None:
    session = _session()
    context = SortingInboundContext.initialize(session)

    with pytest.raises(SortingInboundContextError, match=field_name):
        context.write_source_pick_request(**_source_pick_request_kwargs(**{field_name: 0}))


def test_source_pick_request_rejects_empty_required_string() -> None:
    session = _session()
    context = SortingInboundContext.initialize(session)

    with pytest.raises(SortingInboundContextError, match="event_id"):
        context.write_source_pick_request(**_source_pick_request_kwargs(event_id=""))


def test_source_pick_request_rejects_non_json_safe_route_evidence() -> None:
    session = _session()
    context = SortingInboundContext.initialize(session)

    with pytest.raises(SortingInboundContextError, match="route_evidence"):
        context.write_source_pick_request(**_source_pick_request_kwargs(route_evidence={"bad": {"x"}}))
