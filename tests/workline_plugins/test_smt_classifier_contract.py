"""SMT 插件 contract helper 测试。"""

from src.workline_plugins.smt_classifier.contract import (
    build_default_bin_allocation,
    build_measurement_reel_params,
    build_move_forward_params,
    build_output_to_bin_params,
    build_pick_inspection_ng_params,
    build_pick_scan_ng_params,
    resolve_smt_business_key,
)


def test_command_param_helpers_return_business_params_only() -> None:
    """命令 helper 只返回业务 params，不返回派发包络字段。"""

    assert build_measurement_reel_params("PKG-001") == {"pkg_id": "PKG-001"}
    assert build_move_forward_params("PKG-001") == {"pkg_id": "PKG-001"}
    assert build_pick_scan_ng_params(barcode="PKG-NG", location="LOC-1") == {
        "barcode": "PKG-NG",
        "source_type": "INPUT_PLATFORM",
        "target_type": "NG_PLATFORM",
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


def test_resolve_smt_business_key_uses_stable_incomplete_scan_key_when_pkg_id_missing() -> None:
    """缺 PkgID 的扫码仍需稳定建会话，让插件能生成 NG 分流指令。"""

    payload = {
        "device_code": "ARM01",
        "event_type": "SCAN_COMPLETED",
        "canonical_event_type": "SCAN_COMPLETED",
        "timestamp": 1777338994000,
        "data": {
            "location": "ARM01",
            "HHPN": "620100L00-011-G",
            "MfrPN": "CC0402JRNPO9BN220",
            "Qty": "7387",
            "DateCode": "122625",
            "LotCode": "8904936031",
        },
    }

    key1 = resolve_smt_business_key(payload)
    key2 = resolve_smt_business_key(payload)

    assert key1 is not None
    assert key1.startswith("incomplete-scan:")
    assert key1 == key2
