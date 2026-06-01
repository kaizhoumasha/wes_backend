from __future__ import annotations

import inspect

from src.app.resource.services.smt_bin_cell_allocation_policy import SmtBinCellAllocationPolicy

MATERIAL_IDENTITY_KEY = "MAT:620100L00-011-G:VENDOR-A:122625:8904936031"


def test_compatible_cell_with_enough_decimal_depth_wins_before_empty_cell() -> None:
    result = SmtBinCellAllocationPolicy().allocate(
        active_snapshot={
            "snapshot_version": "snap-20260601-001",
            "cells": [
                _cell("BIN-A", "1", status="EMPTY", capacity_depth_mm="100.000", used_depth_mm="0"),
                _cell(
                    "BIN-B",
                    "2",
                    status="OCCUPIED",
                    material_identity_key=MATERIAL_IDENTITY_KEY,
                    capacity_depth_mm="10.005",
                    used_depth_mm="7.500",
                ),
            ],
        },
        material_identity_key=MATERIAL_IDENTITY_KEY,
        reel_thickness_mm="2.505",
    )

    assert result.kind == "ALLOCATED"
    assert result.target_bin_code == "BIN-B"
    assert result.target_cell_index == "2"
    assert result.source_snapshot_version == "snap-20260601-001"
    assert result.capacity_evidence == {
        "selection_reason": "compatible-material",
        "cell_status": "OCCUPIED",
        "reel_thickness_mm": "2.505",
        "capacity_depth_mm": "10.005",
        "used_depth_mm": "7.500",
        "remaining_depth_mm": "2.505",
        "projected_used_depth_mm": "10.005",
    }


def test_empty_cell_with_enough_decimal_depth_selected_when_no_compatible_cell_exists() -> None:
    result = SmtBinCellAllocationPolicy().allocate(
        active_snapshot={
            "cells": [
                _cell(
                    "BIN-A",
                    "1",
                    status="OCCUPIED",
                    material_identity_key="MAT:OTHER:VENDOR-A:122625:8904936031",
                    capacity_depth_mm="9",
                    used_depth_mm="1",
                ),
                _cell("BIN-B", "2", status="EMPTY", capacity_depth_mm="3.10", used_depth_mm="0"),
            ],
        },
        material_identity_key=MATERIAL_IDENTITY_KEY,
        reel_thickness_mm="3.1",
    )

    assert result.kind == "ALLOCATED"
    assert result.target_bin_code == "BIN-B"
    assert result.target_cell_index == "2"
    assert result.capacity_evidence["selection_reason"] == "empty-cell"
    assert result.capacity_evidence["remaining_depth_mm"] == "3.10"
    assert result.capacity_evidence["projected_used_depth_mm"] == "3.1"


def test_no_empty_cell_and_compatible_cell_capacity_insufficient_returns_no_capacity_reason() -> None:
    result = SmtBinCellAllocationPolicy().allocate(
        active_snapshot={
            "cells": [
                _cell(
                    "BIN-A",
                    "1",
                    status="OCCUPIED",
                    material_identity_key=MATERIAL_IDENTITY_KEY,
                    capacity_depth_mm="5.0",
                    used_depth_mm="4.9",
                ),
                _cell(
                    "BIN-B",
                    "2",
                    status="OCCUPIED",
                    material_identity_key="MAT:OTHER:VENDOR-A:122625:8904936031",
                    capacity_depth_mm="5.0",
                    used_depth_mm="1.0",
                ),
            ],
        },
        material_identity_key=MATERIAL_IDENTITY_KEY,
        reel_thickness_mm="0.2",
    )

    assert result.kind == "REJECTED"
    assert result.reason_code == "NO_CAPACITY"
    assert result.message == "当前快照无同物料且容量足够的料格，也无容量足够的空格"
    assert result.capacity_evidence["reel_thickness_mm"] == "0.2"
    assert result.capacity_evidence["compatible_cells_checked"] == "1"
    assert result.capacity_evidence["empty_cells_checked"] == "0"


def test_reel_thickness_missing_invalid_zero_or_negative_is_rejected() -> None:
    policy = SmtBinCellAllocationPolicy()
    for reel_thickness in (None, "", "abc", "0", "-0.01"):
        result = policy.allocate(
            active_snapshot={"cells": [_cell("BIN-A", "1", status="EMPTY")]},
            material_identity_key=MATERIAL_IDENTITY_KEY,
            reel_thickness_mm=reel_thickness,
        )

        assert result.kind == "REJECTED"
        assert result.reason_code == "INVALID_REEL_THICKNESS"
        assert result.capacity_evidence["reel_thickness_mm"] == str(reel_thickness or "")


def test_capacity_or_used_depth_missing_invalid_or_negative_is_rejected() -> None:
    policy = SmtBinCellAllocationPolicy()
    invalid_cells = [
        _cell("BIN-A", "1", status="EMPTY", capacity_depth_mm=None, used_depth_mm="0"),
        _cell("BIN-A", "1", status="EMPTY", capacity_depth_mm="abc", used_depth_mm="0"),
        _cell("BIN-A", "1", status="EMPTY", capacity_depth_mm="-1", used_depth_mm="0"),
        _cell("BIN-A", "1", status="EMPTY", capacity_depth_mm="5", used_depth_mm=None),
        _cell("BIN-A", "1", status="EMPTY", capacity_depth_mm="5", used_depth_mm="abc"),
        _cell("BIN-A", "1", status="EMPTY", capacity_depth_mm="5", used_depth_mm="-0.1"),
    ]

    for invalid_cell in invalid_cells:
        result = policy.allocate(
            active_snapshot={"cells": [invalid_cell]},
            material_identity_key=MATERIAL_IDENTITY_KEY,
            reel_thickness_mm="1",
        )

        assert result.kind == "REJECTED"
        assert result.reason_code == "INVALID_CELL_DEPTH"
        assert result.target_bin_code == "BIN-A"
        assert result.target_cell_index == "1"


def test_used_depth_greater_than_total_depth_returns_projection_inconsistent_reason() -> None:
    result = SmtBinCellAllocationPolicy().allocate(
        active_snapshot={
            "cells": [
                _cell(
                    "BIN-A",
                    "1",
                    status="OCCUPIED",
                    material_identity_key=MATERIAL_IDENTITY_KEY,
                    capacity_depth_mm="5.00",
                    used_depth_mm="5.01",
                )
            ]
        },
        material_identity_key=MATERIAL_IDENTITY_KEY,
        reel_thickness_mm="1",
    )

    assert result.kind == "REJECTED"
    assert result.reason_code == "PROJECTION_INCONSISTENT"
    assert result.target_bin_code == "BIN-A"
    assert result.target_cell_index == "1"
    assert result.capacity_evidence["capacity_depth_mm"] == "5.00"
    assert result.capacity_evidence["used_depth_mm"] == "5.01"


def test_policy_constructor_and_allocate_signature_do_not_accept_persistence_dependencies() -> None:
    policy_signature = inspect.signature(SmtBinCellAllocationPolicy)
    allocate_signature = inspect.signature(SmtBinCellAllocationPolicy.allocate)
    dependency_words = ("repository", "repo", "session", "db", "database")

    assert not policy_signature.parameters
    for parameter in allocate_signature.parameters:
        assert all(word not in parameter.lower() for word in dependency_words)


def _cell(
    bin_code: str,
    cell_index: str,
    *,
    status: str,
    capacity_depth_mm: str | None = "10",
    used_depth_mm: str | None = "0",
    material_identity_key: str | None = None,
) -> dict[str, object]:
    cell = {
        "bin_code": bin_code,
        "bin_cell_index": cell_index,
        "status": status,
        "capacity_depth_mm": capacity_depth_mm,
        "used_depth_mm": used_depth_mm,
    }
    if material_identity_key is not None:
        cell["material_identity_key"] = material_identity_key
    return cell
