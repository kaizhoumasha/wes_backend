"""入库料箱称重复核插件协议模型。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from src.workline_runtime.utils import non_empty_str


class ToteArrivedData(BaseModel):
    """料箱到位事件 data。"""

    model_config = ConfigDict(extra="forbid")

    tote_id: str
    station_code: str
    expected_weight_kg: float = Field(gt=0)
    tolerance_kg: float = Field(gt=0)


class ToteArrivedPayload(BaseModel):
    """料箱到位事件 payload。"""

    device_code: str
    event_type: str = "TOTE_ARRIVED"
    timestamp: int | None = None
    data: ToteArrivedData | None = None


class WeighCompletedData(BaseModel):
    """称重结果 data。"""

    model_config = ConfigDict(extra="forbid")

    tote_id: str
    actual_weight_kg: float = Field(gt=0)


def resolve_tote_business_key(payload_json: dict[str, Any]) -> str | None:
    """从 TOTE_ARRIVED.data.tote_id 解析业务键。"""

    data = payload_json.get("data")
    if not isinstance(data, Mapping):
        raise TypeError("TOTE_ARRIVED.data is required")
    data_map = cast("Mapping[str, Any]", data)
    tote_id = non_empty_str(data_map.get("tote_id"))
    if not tote_id:
        raise ValueError("TOTE_ARRIVED.data.tote_id is required")
    return tote_id


def classify_inbound_tote_result(payload_json: dict[str, Any]) -> str | None:
    """入库料箱复核命令结果分类。"""

    result = str(payload_json.get("result") or "").strip().upper()
    raw_error_detail = payload_json.get("error_detail")
    error_detail: dict[str, Any] = (
        cast("dict[str, Any]", raw_error_detail) if isinstance(raw_error_detail, dict) else {}
    )
    if result == "FAILED" and error_detail:
        return "hardware_failure"
    return None


def build_weigh_tote_params(*, tote_id: str, station_code: str) -> dict[str, str]:
    """构造称重命令业务参数。"""

    return {"tote_id": tote_id, "station_code": station_code}


def build_divert_tote_params(
    *,
    tote_id: str,
    destination_lane: Literal["PASS_LANE", "HOLD_LANE"],
    reason_code: str,
) -> dict[str, str]:
    """构造料箱分流命令业务参数。"""

    return {
        "tote_id": tote_id,
        "destination_lane": destination_lane,
        "reason_code": reason_code,
    }


__all__ = [
    "ToteArrivedData",
    "ToteArrivedPayload",
    "WeighCompletedData",
    "build_divert_tote_params",
    "build_weigh_tote_params",
    "classify_inbound_tote_result",
    "resolve_tote_business_key",
]
