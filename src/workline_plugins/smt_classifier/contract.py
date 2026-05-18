"""SMT 粗分机插件协议模型。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, cast

from pydantic import AliasChoices, BaseModel, Field, model_validator

from src.app.resource.services import SmtRackBinSchedulingService
from src.workline_runtime.contracts import SixInOne
from src.workline_runtime.material_identity import (
    MaterialIdentity,
    MaterialIdentityInput,
    MaterialIdentityResolutionStatus,
    material_identity_input_to_hash,
)
from src.workline_runtime.ng_reason import NgReasonDefinition, NgReasonSource
from src.workline_runtime.utils import non_empty_str

_SCAN_COMPLETED_EVENT = "SCAN_COMPLETED"
WMS_RACK_EXCHANGE_PROGRESS = "WMS_RACK_EXCHANGE_PROGRESS"
WMS_RACK_ARRIVED = "WMS_RACK_ARRIVED"
WMS_RACK_EXCHANGE_FAILED = "WMS_RACK_EXCHANGE_FAILED"
INSPECTION_SIZE_NG_REASON = "INSPECTION_SIZE_NG"
INSPECTION_THICKNESS_NG_REASON = "INSPECTION_THICKNESS_NG"
INSPECTION_NG_REASONS = frozenset({INSPECTION_SIZE_NG_REASON, INSPECTION_THICKNESS_NG_REASON})


def _normalize_contract_data(payload: Any, **extra_fields: Any) -> Any:
    """将设备 data 归一化为插件内部统一字段。"""

    if not isinstance(payload, dict):
        return payload

    payload_map = cast("dict[str, Any]", payload)
    normalized: dict[str, Any] = normalize_six_in_one_payload(payload_map) or {}
    normalized.update(extra_fields)
    return normalized


def normalize_six_in_one_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """将 SMT 插件当前设备协议字段映射为方案 A 的 SixInOne 字段。"""

    if not isinstance(payload, dict):
        return None

    return {
        "HHPN": payload.get("HHPN") or payload.get("ProductNo"),
        "MfrPN": payload.get("MfrPN"),
        "Qty": payload.get("Qty"),
        "DateCode": payload.get("DateCode"),
        "LotCode": payload.get("LotCode"),
        "PkgID": payload.get("PkgID") or payload.get("PONumber") or payload.get("pkg_id"),
    }


def parse_six_in_one_payload(payload: dict[str, Any] | None) -> SixInOne | None:
    """按 SMT 插件协议解析 SixInOne。"""

    normalized = normalize_six_in_one_payload(payload)
    if not normalized:
        return None

    six_in_one = SixInOne.model_validate(normalized)
    return six_in_one if six_in_one.has_any_value else None


def _build_incomplete_scan_business_key(payload_json: dict[str, Any], six_in_one: SixInOne) -> str | None:
    """为缺 PkgID 的扫码事件生成稳定会话键，让插件能执行 NG 分流。"""

    event_type = non_empty_str(payload_json.get("canonical_event_type"))
    if event_type is None:
        event_type = non_empty_str(payload_json.get("event_type"))
    if event_type != _SCAN_COMPLETED_EVENT:
        return None

    device_code = non_empty_str(payload_json.get("device_code"))
    if device_code is None:
        return None

    raw_data = payload_json.get("data")
    data: dict[str, Any] = cast("dict[str, Any]", raw_data) if isinstance(raw_data, dict) else {}
    event_identity = payload_json.get("event_id") or data.get("event_id") or data.get("vendor_event_id")
    business_fields: dict[str, Any] = {field: value for field, value in six_in_one.iter_business_fields() if value}
    identity_payload: dict[str, Any] = {
        "device_code": device_code,
        "event_type": event_type,
        "fields": business_fields,
    }
    if event_identity:
        identity_payload["event_identity"] = event_identity
    serialized = json.dumps(identity_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
    return f"incomplete-scan:{digest}"


def resolve_smt_business_key(payload_json: dict[str, Any]) -> str | None:
    """从 SMT 事件包络中解析稳定业务键。"""

    raw_data = payload_json.get("data")
    data: dict[str, Any] | None = cast("dict[str, Any]", raw_data) if isinstance(raw_data, dict) else None
    six_in_one = parse_six_in_one_payload(data)
    if six_in_one is None:
        return None
    if six_in_one.business_key:
        return six_in_one.business_key
    return _build_incomplete_scan_business_key(payload_json, six_in_one)


def _payload_data(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    raw_data = payload.get("data")
    if isinstance(raw_data, dict):
        return cast("dict[str, Any]", raw_data)
    return dict(payload)


def _normalized_material_display(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    normalized = normalize_six_in_one_payload(_payload_data(payload)) or {}
    return {key: value for key, value in normalized.items() if value not in (None, "")}


def _pkg_id_candidates(input_value: MaterialIdentityInput) -> list[str]:
    candidates: list[str] = []
    for payload in (
        input_value.material_scan_payload,
        input_value.source_payload,
        input_value.command_payload,
        input_value.session_context,
        input_value.plugin_context,
    ):
        pkg_id = non_empty_str(_normalized_material_display(payload).get("PkgID"))
        if pkg_id is not None:
            candidates.append(pkg_id)
    return candidates


def resolve_smt_material_identity(input_value: MaterialIdentityInput) -> MaterialIdentity:
    """Resolve SMT material identity from plugin-owned SixInOne evidence."""

    evidence_hash = material_identity_input_to_hash(input_value)
    unique_pkg_ids: tuple[str, ...] = tuple(dict.fromkeys(_pkg_id_candidates(input_value)))
    if not unique_pkg_ids:
        return MaterialIdentity(
            resolution_status=MaterialIdentityResolutionStatus.MISSING,
            raw_evidence_hash=evidence_hash,
        )
    if len(unique_pkg_ids) > 1:
        return MaterialIdentity(
            resolution_status=MaterialIdentityResolutionStatus.AMBIGUOUS,
            display={"PkgID_candidates": list(unique_pkg_ids)},
            raw_evidence_hash=evidence_hash,
        )

    pkg_id = next(iter(unique_pkg_ids))
    display: dict[str, Any] = {"PkgID": pkg_id}
    for payload in (
        input_value.source_payload,
        input_value.material_scan_payload,
        input_value.command_payload,
        input_value.session_context,
        input_value.plugin_context,
    ):
        display.update(_normalized_material_display(payload))
    display["PkgID"] = pkg_id
    return MaterialIdentity(
        resolution_status=MaterialIdentityResolutionStatus.RESOLVED,
        idempotency_key=f"smt:{pkg_id}",
        business_key=pkg_id,
        display=display,
        raw_evidence_hash=evidence_hash,
    )


def smt_ng_reason_catalog() -> tuple[NgReasonDefinition, ...]:
    """Map SMT business-decision reason codes into the canonical NG taxonomy."""

    return (
        NgReasonDefinition(
            canonical_code="SCAN_NG",
            label="扫码异常",
            source=NgReasonSource.PLUGIN,
            plugin_key="smt_classifier",
            contract_version="1.0",
            maps_from=("SCAN_NG",),
        ),
        NgReasonDefinition(
            canonical_code="SCAN_NG_BY_RULE",
            label="扫码规则判定 NG",
            source=NgReasonSource.PLUGIN,
            plugin_key="smt_classifier",
            contract_version="1.0",
            maps_from=("SCAN_NG_BY_RULE",),
        ),
        NgReasonDefinition(
            canonical_code=INSPECTION_SIZE_NG_REASON,
            label="尺寸检测异常",
            source=NgReasonSource.PLUGIN,
            plugin_key="smt_classifier",
            contract_version="1.0",
            maps_from=(INSPECTION_SIZE_NG_REASON,),
        ),
        NgReasonDefinition(
            canonical_code=INSPECTION_THICKNESS_NG_REASON,
            label="厚度检测异常",
            source=NgReasonSource.PLUGIN,
            plugin_key="smt_classifier",
            contract_version="1.0",
            maps_from=(INSPECTION_THICKNESS_NG_REASON,),
        ),
        NgReasonDefinition(
            canonical_code="BARCODE_INVALID",
            label="条码无效",
            source=NgReasonSource.PLUGIN,
            plugin_key="smt_classifier",
            contract_version="1.0",
            maps_from=("BARCODE_INVALID",),
        ),
        NgReasonDefinition(
            canonical_code="BARCODE_INCOMPLETE",
            label="条码不完整",
            source=NgReasonSource.PLUGIN,
            plugin_key="smt_classifier",
            contract_version="1.0",
            maps_from=("BARCODE_INCOMPLETE",),
        ),
    )


def classify_smt_command_result(payload_json: dict[str, Any]) -> str | None:
    """按 SMT 插件合同分类命令结果。

    业务检测 NG 是业务判定；设备执行失败、急停等仍由 failure 路径承载。
    """

    result = str(payload_json.get("result") or "").strip().upper()
    raw_data = payload_json.get("data")
    data: dict[str, Any] = cast("dict[str, Any]", raw_data) if isinstance(raw_data, dict) else {}
    raw_error_detail = payload_json.get("error_detail")
    error_detail: dict[str, Any] = (
        cast("dict[str, Any]", raw_error_detail) if isinstance(raw_error_detail, dict) else {}
    )

    inspection_result = str(data.get("inspection_result") or "").strip().upper()
    if result == "SUCCESS" and inspection_result == "NG":
        return "business_decision"

    error_code = error_detail.get("error_code") or error_detail.get("code")
    if error_code:
        return "hardware_failure"

    return None


def build_measurement_reel_params(pkg_id: str) -> dict[str, str]:
    """构造测量命令业务参数。"""

    return {"pkg_id": pkg_id}


def build_move_forward_params(pkg_id: str) -> dict[str, str]:
    """构造流水线前进命令业务参数。"""

    return {"pkg_id": pkg_id}


def build_pick_scan_ng_params(*, barcode: str, location: str) -> dict[str, str]:
    """构造扫码 NG 分流命令业务参数。"""

    # 保留 location 入参兼容插件调用；实际点位由硬件 mock 按平台类型解析。
    _ = location
    return {
        "barcode": barcode,
        "source_type": "INPUT_PLATFORM",
        "target_type": "NG_PLATFORM",
    }


def build_pick_inspection_ng_params(*, barcode: str) -> dict[str, str]:
    """构造检测 NG 分流命令业务参数。"""

    return {
        "barcode": barcode,
        "source_type": "PIPELINE_PLATFORM",
        "target_type": "NG_PLATFORM",
    }


def build_output_to_bin_params(
    *,
    pkg_id: str,
    reel_diameter: str,
    bin_location: dict[str, Any],
) -> dict[str, Any]:
    """构造出料到料箱命令业务参数。"""

    return {
        "barcode": pkg_id,
        "reel_diameter": reel_diameter,
        "target_type": "BIN",
        "target_loc": bin_location["bin_id"],
        "rack_id": bin_location["rack_id"],
        "rack_slot_code": bin_location["rack_slot_code"],
        "rack_slot_location_code": bin_location["rack_slot_location_code"],
        "bin_id": bin_location["bin_id"],
        "bin_orientation_code": bin_location["bin_orientation_code"],
        "bin_type": bin_location["bin_type"],
        "bin_cell_location": bin_location["bin_cell_location"],
        "bin_cell_index": bin_location["bin_cell_index"],
    }


def build_default_bin_allocation(pkg_id: str) -> dict[str, str]:
    """兼容旧调用：实际料箱调度逻辑已收敛到领域服务。"""

    allocation = SmtRackBinSchedulingService().allocate(pkg_id)
    return {key: str(value) for key, value in allocation.items()}


class ScanEventData(SixInOne, BaseModel):
    """扫码事件 data 字段 - 包含六合一码 + 扫描位置。"""

    location: str = Field(description="扫描位置，如 STATION_INPUT1")

    @model_validator(mode="before")
    @classmethod
    def _normalize_payload(cls, data: Any) -> Any:
        data_map = cast("dict[str, Any]", data) if isinstance(data, dict) else None
        return _normalize_contract_data(data, location=data_map.get("location") if data_map is not None else None)


class ScanEventPayload(BaseModel):
    """扫码完成事件 Payload。"""

    device_code: str
    event_type: str = Field(default=_SCAN_COMPLETED_EVENT)
    timestamp: int | None = Field(default=None)
    data: ScanEventData | None = Field(default=None)


class MeasurementResultData(SixInOne, BaseModel):
    """测量结果数据。

    测量成功回调后会直接推进到流水线传输，因此这里必须携带可继续路由的业务标识 `PkgID`。
    """

    reel_diameter: float | None = Field(default=None, description="料盘直径测量值")
    reel_thickness: float | None = Field(default=None, description="料盘厚度测量值")
    inspection_result: str | None = Field(default=None, description="检测结果：OK/NG")
    reason_code: str | None = Field(default=None, description="业务 NG 原因码")
    reason_message: str | None = Field(default=None, description="业务 NG 原因说明")

    @model_validator(mode="before")
    @classmethod
    def _normalize_payload(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data_map = cast("dict[str, Any]", data)
        return _normalize_contract_data(
            data_map,
            reel_diameter=data_map.get("reel_diameter"),
            reel_thickness=data_map.get("reel_thickness"),
            inspection_result=data_map.get("inspection_result"),
            reason_code=data_map.get("reason_code") or data_map.get("ng_reason"),
            reason_message=data_map.get("reason_message") or data_map.get("ng_message"),
        )


class PickPlaceResultData(BaseModel):
    """机械臂抓取放置执行结果 data 字段。"""

    actual_qty: int = Field(default=1, description="实际搬运数量")
    location: str | None = Field(default=None, description="实际放置位置")
    reel_diameter: str | None = Field(default=None, description="料盘直径测量值")
    reel_thickness: str | None = Field(default=None, description="料盘厚度测量值")
    pick_and_place_result: str | None = Field(
        default=None,
        validation_alias=AliasChoices("pick_and_place_result", "pick_and_put_result"),
        description="抓取放置具体结果",
    )


class EStopEventPayload(BaseModel):
    """急停事件 Payload。"""

    device_code: str
    event_type: str = Field(default="ESTOP_PRESSED")
    timestamp: int | None = Field(default=None)
    data: dict | None = Field(default=None)


__all__ = [
    "WMS_RACK_ARRIVED",
    "WMS_RACK_EXCHANGE_FAILED",
    "WMS_RACK_EXCHANGE_PROGRESS",
    "EStopEventPayload",
    "MeasurementResultData",
    "PickPlaceResultData",
    "ScanEventData",
    "ScanEventPayload",
    "build_default_bin_allocation",
    "build_measurement_reel_params",
    "build_move_forward_params",
    "build_output_to_bin_params",
    "build_pick_inspection_ng_params",
    "build_pick_scan_ng_params",
    "classify_smt_command_result",
    "normalize_six_in_one_payload",
    "parse_six_in_one_payload",
    "resolve_smt_business_key",
    "resolve_smt_material_identity",
    "smt_ng_reason_catalog",
]
