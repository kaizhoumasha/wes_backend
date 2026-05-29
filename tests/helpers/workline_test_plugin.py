"""测试专用 Workline 插件桩。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from src.workline_runtime.material_identity import (
    MaterialIdentity,
    MaterialIdentityInput,
    MaterialIdentityResolutionStatus,
    hash_material_evidence,
)
from src.workline_runtime.ng_reason import NgReasonDefinition, NgReasonSource
from src.workline_runtime.plugin_manifest import DeviceRoleRequirement, WorklinePluginManifest

PLUGIN_KEY = "test_workline_plugin"
CONTRACT_VERSION = "test.v1"


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def _payload_data(payload_json: dict[str, Any]) -> dict[str, Any]:
    data = payload_json.get("data")
    return data if isinstance(data, dict) else {}


def _resolve_business_key(payload_json: dict[str, Any]) -> str | None:
    data = _payload_data(payload_json)
    item_id = data.get("item_id")
    if isinstance(item_id, str) and item_id:
        return _stable_hash(item_id)

    required_without_item_id = ("part_no", "vendor_part_no", "quantity", "production_date", "lot_no")
    if all(isinstance(data.get(field), str) and data.get(field) for field in required_without_item_id):
        evidence = {field: data[field] for field in required_without_item_id}
        return f"incomplete-test-item:{_stable_hash(evidence)}"
    return None


def _resolve_material_identity(input_value: MaterialIdentityInput) -> MaterialIdentity:
    source_payload = dict(input_value.source_payload or {})
    source_data = source_payload.get("data")
    data = source_data if isinstance(source_data, dict) else {}
    if not data:
        data = source_payload
    scan_payload = dict(input_value.material_scan_payload or {})
    source_item_id = data.get("item_id")
    scan_item_id = scan_payload.get("item_id")
    item_id = source_item_id or scan_item_id

    if not isinstance(item_id, str) or not item_id:
        return MaterialIdentity(
            resolution_status=MaterialIdentityResolutionStatus.MISSING,
            raw_evidence_hash=hash_material_evidence({"source": data, "scan": scan_payload}),
        )

    if isinstance(source_item_id, str) and isinstance(scan_item_id, str) and source_item_id != scan_item_id:
        return MaterialIdentity(
            resolution_status=MaterialIdentityResolutionStatus.AMBIGUOUS,
            raw_evidence_hash=hash_material_evidence({"source": data, "scan": scan_payload}),
        )

    return MaterialIdentity(
        resolution_status=MaterialIdentityResolutionStatus.RESOLVED,
        idempotency_key=f"test-material:{item_id}",
        business_key=_stable_hash(item_id),
        display={key: value for key, value in data.items() if isinstance(value, str) and value},
        raw_evidence_hash=hash_material_evidence(data),
    )


def _classify_result(payload_json: dict[str, Any]) -> str | None:
    data = _payload_data(payload_json)
    if payload_json.get("result") == "SUCCESS" and data.get("inspection_result") == "NG":
        return "business_decision"
    return None


class TestWorklinePlugin:
    """仅供测试通过 registry 加载的插件类。"""

    manifest = WorklinePluginManifest(
        plugin_key=PLUGIN_KEY,
        contract_version=CONTRACT_VERSION,
        required_device_roles=(DeviceRoleRequirement(role="TEST_DEVICE", min_count=0),),
        business_key_resolver=_resolve_business_key,
        result_classifier=_classify_result,
        material_identity_resolver=_resolve_material_identity,
        ng_reason_catalog=(
            NgReasonDefinition(
                canonical_code="SCAN_NG",
                label="扫码异常",
                source=NgReasonSource.PLUGIN,
                plugin_key=PLUGIN_KEY,
                contract_version=CONTRACT_VERSION,
                maps_from=("SCAN_NG",),
            ),
            NgReasonDefinition(
                canonical_code="BARCODE_INVALID",
                label="条码无效",
                source=NgReasonSource.PLUGIN,
                plugin_key=PLUGIN_KEY,
                contract_version=CONTRACT_VERSION,
                maps_from=("BARCODE_INVALID",),
            ),
            NgReasonDefinition(
                canonical_code="BARCODE_INCOMPLETE",
                label="条码不完整",
                source=NgReasonSource.PLUGIN,
                plugin_key=PLUGIN_KEY,
                contract_version=CONTRACT_VERSION,
                maps_from=("BARCODE_INCOMPLETE",),
            ),
        ),
    )
