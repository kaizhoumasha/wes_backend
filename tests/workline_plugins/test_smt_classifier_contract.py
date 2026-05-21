"""SMT 插件 contract helper 测试。"""

from src.workline_plugins.smt_classifier.contract import (
    build_default_bin_allocation,
    build_measurement_reel_params,
    build_move_forward_params,
    build_output_to_bin_params,
    build_pick_inspection_ng_params,
    build_pick_scan_ng_params,
    resolve_smt_business_key,
    resolve_smt_material_identity,
    smt_ng_reason_catalog,
)
from src.workline_runtime.contracts import DeviceErrorCode
from src.workline_runtime.material_identity import MaterialIdentityInput, MaterialIdentityResolutionStatus


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
            "rack_id": "NHW-1CLJ-0096",
            "rack_slot_code": "C",
            "rack_slot_location_code": "NHW-1CLJ-0096-1C-1",
            "bin_id": "BIN-001",
            "bin_orientation_code": "BIN-001-A",
            "bin_type": "6格箱",
            "bin_cell_location": "BIN-001-3",
            "bin_cell_index": "3",
        },
    )

    assert params == {
        "barcode": "PKG-001",
        "reel_diameter": "178.5",
        "target_type": "BIN",
        "target_loc": "BIN-001",
        "rack_id": "NHW-1CLJ-0096",
        "rack_slot_code": "C",
        "rack_slot_location_code": "NHW-1CLJ-0096-1C-1",
        "bin_id": "BIN-001",
        "bin_orientation_code": "BIN-001-A",
        "bin_type": "6格箱",
        "bin_cell_location": "BIN-001-3",
        "bin_cell_index": "3",
    }


def test_default_bin_allocation_is_deterministic() -> None:
    first = build_default_bin_allocation("PKG-001")
    second = build_default_bin_allocation("PKG-001")

    assert first == second
    assert set(first) == {
        "rack_id",
        "rack_slot_code",
        "rack_slot_location_code",
        "bin_id",
        "bin_orientation_code",
        "bin_type",
        "bin_cell_location",
        "bin_cell_index",
    }
    assert first["rack_slot_code"] in {"A", "B", "C", "D"}
    assert first["bin_cell_location"].startswith(f"{first['bin_id']}-")


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


def test_resolve_smt_business_key_uses_event_identity_for_incomplete_scan() -> None:
    """测试数据可用 event_id 显式表达不同扫码事件实例。"""

    payload = {
        "device_code": "ARM01",
        "event_type": "SCAN_COMPLETED",
        "canonical_event_type": "SCAN_COMPLETED",
        "timestamp": 1777338994000,
        "data": {
            "location": "ARM01",
            "event_id": "SCAN-NG-001",
            "LotCode": "X",
        },
    }

    later_payload = {**payload, "data": {**payload["data"], "event_id": "SCAN-NG-002"}}

    key1 = resolve_smt_business_key(payload)
    key2 = resolve_smt_business_key(later_payload)

    assert key1 is not None
    assert key2 is not None
    assert key1.startswith("incomplete-scan:")
    assert key2.startswith("incomplete-scan:")
    assert key1 != key2


def test_resolve_smt_material_identity_uses_pkg_id_as_plugin_owned_key() -> None:
    identity = resolve_smt_material_identity(
        MaterialIdentityInput(
            source_payload={
                "data": {
                    "PkgID": "PKG-001",
                    "HHPN": "620100L00-011-G",
                    "LotCode": "8904936031",
                }
            },
            material_scan_payload={"PkgID": "PKG-001"},
        )
    )

    assert identity.resolution_status == MaterialIdentityResolutionStatus.RESOLVED
    assert identity.idempotency_key == "smt:PKG-001"
    assert identity.business_key == "PKG-001"
    assert identity.display["PkgID"] == "PKG-001"
    assert identity.display["HHPN"] == "620100L00-011-G"
    assert identity.raw_evidence_hash.startswith("sha256:")


def test_resolve_smt_material_identity_rejects_ambiguous_pkg_id_evidence() -> None:
    identity = resolve_smt_material_identity(
        MaterialIdentityInput(
            source_payload={"data": {"PkgID": "PKG-001"}},
            material_scan_payload={"PkgID": "PKG-002"},
        )
    )

    assert identity.resolution_status == MaterialIdentityResolutionStatus.AMBIGUOUS
    assert identity.idempotency_key is None


def test_smt_ng_reason_catalog_maps_existing_business_decisions() -> None:
    reasons = {reason.canonical_code: reason for reason in smt_ng_reason_catalog()}

    assert {
        "SCAN_NG",
        "SCAN_NG_BY_RULE",
        "INSPECTION_SIZE_NG",
        "INSPECTION_THICKNESS_NG",
        "BARCODE_INVALID",
        "BARCODE_INCOMPLETE",
    } <= set(reasons)
    assert reasons["SCAN_NG"].maps_from == ("SCAN_NG",)


def test_inspection_ng_reasons_are_not_device_error_codes() -> None:
    """检测 NG 是业务 NG reason，不属于设备错误码。"""

    assert "INSPECTION_SIZE_NG" not in DeviceErrorCode._value2member_map_
    assert "INSPECTION_THICKNESS_NG" not in DeviceErrorCode._value2member_map_
