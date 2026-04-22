"""SMT 粗分机插件协议模型。"""

from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, BaseModel, Field, model_validator

from src.workline_runtime.contracts import SixInOne


def normalize_six_in_one_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """将 SMT 插件当前设备协议字段映射为方案 A 的 SixInOne 字段。"""

    if not isinstance(payload, dict):
        return None

    return {
        "business_key": payload.get("business_key"),
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
    return six_in_one if six_in_one.has_any_value or six_in_one.business_key else None


class ScanEventData(SixInOne, BaseModel):
    """扫码事件 data 字段 - 包含六合一码 + 扫描位置。"""

    location: str = Field(description="扫描位置，如 STATION_INPUT1")

    @model_validator(mode="before")
    @classmethod
    def _normalize_payload(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        normalized = normalize_six_in_one_payload(data) or {}
        normalized["location"] = data.get("location")
        return normalized


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

    PkgID: str = Field(description="业务包裹标识，测量成功后继续推进流程必填")
    reel_diameter: float | None = Field(default=None, description="料盘直径测量值")
    reel_thickness: float | None = Field(default=None, description="料盘厚度测量值")

    @model_validator(mode="before")
    @classmethod
    def _normalize_payload(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        normalized = normalize_six_in_one_payload(data) or {}
        normalized["reel_diameter"] = data.get("reel_diameter")
        normalized["reel_thickness"] = data.get("reel_thickness")
        return normalized


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
    "normalize_six_in_one_payload",
    "parse_six_in_one_payload",
]
