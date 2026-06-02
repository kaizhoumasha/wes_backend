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
