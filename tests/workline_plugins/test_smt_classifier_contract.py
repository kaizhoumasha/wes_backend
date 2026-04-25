"""SMT 插件 contract helper 测试。"""

from src.workline_plugins.smt_classifier.contract import (
    build_default_bin_allocation,
    build_measurement_reel_params,
    build_move_forward_params,
    build_output_to_bin_params,
    build_pick_inspection_ng_params,
    build_pick_scan_ng_params,
)


def test_command_param_helpers_return_business_params_only() -> None:
    """命令 helper 只返回业务 params，不返回派发包络字段。"""

    assert build_measurement_reel_params("PKG-001") == {"pkg_id": "PKG-001"}
    assert build_move_forward_params("PKG-001") == {"pkg_id": "PKG-001"}
    assert build_pick_scan_ng_params(barcode="PKG-NG", location="LOC-1") == {
        "barcode": "PKG-NG",
        "source_type": "INPUT_PLATFORM",
        "target_type": "NG_PLATFORM",
        "source_loc": "LOC-1",
        "target_loc": "STATION_NG_PLATFORM1",
    }
    assert build_pick_inspection_ng_params(barcode="PKG-INSPECT-NG") == {
        "barcode": "PKG-INSPECT-NG",
        "source_type": "PIPELINE_PLATFORM",
        "target_type": "NG_PLATFORM",
    }


def test_output_to_bin_params_use_allocated_bin_business_fields() -> None:
    params = build_output_to_bin_params(
        pkg_id="PKG-001",
        reel_diameter="178.5",
        bin_location={
            "bin_id": "BIN-001",
            "bin_type": "五格箱",
            "bin_cell_location": "3",
        },
    )

    assert params == {
        "barcode": "PKG-001",
        "reel_diameter": "178.5",
        "target_type": "BIN",
        "target_loc": "BIN-001",
        "bin_type": "五格箱",
    }


def test_default_bin_allocation_is_deterministic() -> None:
    first = build_default_bin_allocation("PKG-001")
    second = build_default_bin_allocation("PKG-001")

    assert first == second
    assert set(first) == {"bin_id", "bin_type", "bin_cell_location"}
