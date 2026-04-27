"""SMT 粗分机插件协议模型。"""

from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, BaseModel, Field, model_validator

from src.workline_runtime.contracts import DeviceErrorCode, SixInOne


def _normalize_contract_data(payload: Any, **extra_fields: Any) -> Any:
    """将设备 data 归一化为插件内部统一字段。"""

    if not isinstance(payload, dict):
        return payload

    normalized = normalize_six_in_one_payload(payload) or {}
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


def resolve_smt_business_key(payload_json: dict[str, Any]) -> str | None:
    """从 SMT 事件包络中解析稳定业务键。"""

    data = payload_json.get("data")
    six_in_one = parse_six_in_one_payload(data if isinstance(data, dict) else None)
    return six_in_one.business_key if six_in_one and six_in_one.business_key else None


def classify_smt_command_result(payload_json: dict[str, Any]) -> str | None:
    """按 SMT 插件合同分类命令结果。

    业务检测 NG 是业务判定；设备执行失败、急停等仍由 failure 路径承载。
    """

    result = str(payload_json.get("result") or "").strip().upper()
    raw_data = payload_json.get("data")
    data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
    raw_error_detail = payload_json.get("error_detail")
    error_detail: dict[str, Any] = raw_error_detail if isinstance(raw_error_detail, dict) else {}

    inspection_result = str(data.get("inspection_result") or "").strip().upper()
    if result == "SUCCESS" and inspection_result == "NG":
        return "business_decision"

    error_code = error_detail.get("error_code") or error_detail.get("code")
    if error_code in {
        DeviceErrorCode.INSPECTION_SIZE_NG.value,
        DeviceErrorCode.INSPECTION_THICKNESS_NG.value,
    }:
        return "business_decision"
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

    return {
        "barcode": barcode,
        "source_type": "INPUT_PLATFORM",
        "target_type": "NG_PLATFORM",
        "source_loc": location,
        "target_loc": "STATION_NG_PLATFORM1",
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
        "bin_type": bin_location["bin_type"],
    }


def build_default_bin_allocation(pkg_id: str) -> dict[str, str]:
    """构造无外部分配服务时的确定性料箱分配结果。"""

    checksum = sum(ord(char) for char in pkg_id) or 1
    bin_types = ("三格箱", "五格箱", "九格箱")
    return {
        "bin_id": f"BIN_{checksum % 900 + 100}",
        "bin_type": bin_types[checksum % len(bin_types)],
        "bin_cell_location": str(checksum % 9 + 1),
    }


class ScanEventData(SixInOne, BaseModel):
    """扫码事件 data 字段 - 包含六合一码 + 扫描位置。"""

    location: str = Field(description="扫描位置，如 STATION_INPUT1")

    @model_validator(mode="before")
    @classmethod
    def _normalize_payload(cls, data: Any) -> Any:
        return _normalize_contract_data(data, location=data.get("location") if isinstance(data, dict) else None)


class ScanEventPayload(BaseModel):
    """扫码完成事件 Payload。"""

    device_code: str
    event_type: str = Field(default="SCAN_COMPLETED")
    timestamp: int | None = Field(default=None)
    data: ScanEventData | None = Field(default=None)


class MeasurementResultData(SixInOne, BaseModel):
    """测量结果数据。

    测量成功回调后会直接推进到流水线传输，因此这里必须携带可继续路由的业务标识 `PkgID`。
    """

    reel_diameter: float | None = Field(default=None, description="料盘直径测量值")
    reel_thickness: float | None = Field(default=None, description="料盘厚度测量值")

    @model_validator(mode="before")
    @classmethod
    def _normalize_payload(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        return _normalize_contract_data(
            data,
            reel_diameter=data.get("reel_diameter"),
            reel_thickness=data.get("reel_thickness"),
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
]
