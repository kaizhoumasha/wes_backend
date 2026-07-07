"""粗分机插件业务合同。"""

from __future__ import annotations

from typing import Any, cast

from src.app.workline.domain.contracts import SixInOne

ROUGH_SORTER_PLUGIN_KEY = "rough_sorter"
ROUGH_SORTER_CONTRACT_VERSION = "rough_sorter.v2"

EVENT_SCAN_COMPLETED = "SCAN_COMPLETED"
EVENT_ROUGH_SORTER_STORAGE_RETRY = "ROUGH_SORTER_STORAGE_RETRY"

ACTION_PICK_AND_PUT = "PICK_AND_PUT"
ACTION_MOVE_FORWARD = "MOVE_FORWARD"
ACTION_PUT_TO_BIN = "PUT_TO_BIN"
ACTION_MOVE_TO_NG = "MOVE_TO_NG"

ROUGH_SORTER_SCANNED_CONTEXT_STATE = "SCANNED"
PHASE_PICK_TO_PIPELINE = "PICK_TO_PIPELINE"
PHASE_MOVING_FORWARD = "MOVING_FORWARD"
ROUGH_SORTER_RACK_WAIT_CONTEXT_STATE = "WAITING_RACK"
PHASE_PUTTING_TO_BIN = "PUTTING_TO_BIN"
PHASE_NG_MOVING = "NG_MOVING"
PHASE_COMPLETED = "COMPLETED"

ROLE_INPUT_ARM = "ROUGH_SORTER_INPUT_ARM"
ROLE_CONVEYOR = "ROUGH_SORTER_CONVEYOR"
ROLE_OUTPUT_ARM = "ROUGH_SORTER_OUTPUT_ARM"

NG_REASON_BARCODE_INVALID = "BARCODE_INVALID"
NG_REASON_BARCODE_INCOMPLETE = "BARCODE_INCOMPLETE"
NG_REASON_BARCODE_RULE_NG = "BARCODE_RULE_NG"
NG_REASON_MEASUREMENT_NG = "MEASUREMENT_NG"
NG_REASON_WMS_REJECTED = "WMS_REJECTED"

ACTION_TARGET_ROLES: dict[str, str] = {
    ACTION_PICK_AND_PUT: ROLE_INPUT_ARM,
    ACTION_MOVE_FORWARD: ROLE_CONVEYOR,
    ACTION_PUT_TO_BIN: ROLE_OUTPUT_ARM,
    ACTION_MOVE_TO_NG: ROLE_INPUT_ARM,
}


def _payload_data(payload_json: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload_json, dict):
        return {}
    data = payload_json.get("data")
    return cast("dict[str, Any]", data.copy()) if isinstance(data, dict) else {}


def _non_empty_str(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _six_in_one_dict(six_in_one: SixInOne) -> dict[str, Any]:
    return {
        field_name: value
        for field_name, value in six_in_one.model_dump().items()
        if field_name in SixInOne.BUSINESS_FIELD_NAMES and value is not None
    }


def _base_command_payload(
    *,
    action: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "task_type": action,
        "params": dict(params or {}),
    }


def normalize_six_in_one_payload(payload_json: dict[str, Any] | None) -> SixInOne:
    """只从 payload.data 解析六合一码，并在 data 内归一现场别名。"""

    data = _payload_data(payload_json)
    normalized = dict(data)
    if _non_empty_str(normalized.get("ProductNo")) and not _non_empty_str(normalized.get("HHPN")):
        normalized["HHPN"] = normalized["ProductNo"]
    if _non_empty_str(normalized.get("PONumber")) and not _non_empty_str(normalized.get("PkgID")):
        normalized["PkgID"] = normalized["PONumber"]
    return SixInOne.model_validate(normalized)


def resolve_rough_sorter_business_key(payload_json: dict[str, Any]) -> str | None:
    """按 payload.data.PkgID 派生粗分机业务主键。"""

    return normalize_six_in_one_payload(payload_json).business_key


def build_pick_and_put_payload(
    *,
    business_key: str,
    source_location: str,
    target_location: str,
    six_in_one: SixInOne | dict[str, Any] | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    """构造入料机械臂抓取放置命令 payload。"""

    params: dict[str, Any] = {
        "business_key": business_key,
        "source_location": source_location,
        "target_location": target_location,
    }
    if isinstance(six_in_one, SixInOne):
        params["six_in_one"] = _six_in_one_dict(six_in_one)
    elif isinstance(six_in_one, dict):
        params["six_in_one"] = dict(six_in_one)
    if trace_id:
        params["trace_id"] = trace_id
    return _base_command_payload(
        action=ACTION_PICK_AND_PUT,
        params=params,
    )


def build_move_forward_payload(
    *,
    business_key: str,
    source_location: str,
    target_location: str,
) -> dict[str, Any]:
    """构造流水线前进命令 payload。"""

    return _base_command_payload(
        action=ACTION_MOVE_FORWARD,
        params={
            "business_key": business_key,
            "source_location": source_location,
            "target_location": target_location,
        },
    )


def build_put_to_bin_payload(
    *,
    business_key: str,
    source_location: str,
    bin_location: str,
) -> dict[str, Any]:
    """构造出料入箱命令 payload。"""

    return _base_command_payload(
        action=ACTION_PUT_TO_BIN,
        params={
            "business_key": business_key,
            "source_location": source_location,
            "target_location": bin_location,
            "bin_location": bin_location,
        },
    )


def build_move_to_ng_payload(
    *,
    business_key: str,
    source_location: str,
    ng_location: str,
    reason_code: str,
) -> dict[str, Any]:
    """构造物理 NG 搬运命令 payload。"""

    return _base_command_payload(
        action=ACTION_MOVE_TO_NG,
        params={
            "business_key": business_key,
            "source_location": source_location,
            "target_location": ng_location,
            "ng_location": ng_location,
            "reason_code": reason_code,
        },
    )


def classify_rough_sorter_result(payload_json: dict[str, Any]) -> str | None:
    """声明粗分机插件的命令结果业务分类。"""

    data = _payload_data(payload_json)
    result = str(payload_json.get("result") or "").upper()
    measurement_result = str(data.get("measurement_result") or data.get("inspection_result") or "").upper()
    size_judgement = str(data.get("size_judgement") or "").upper()
    thickness_judgement = str(data.get("thickness_judgement") or "").upper()

    if result == "SUCCESS" and "NG" in {measurement_result, size_judgement, thickness_judgement}:
        return "business_decision"
    if result in {"FAILED", "ERROR"} and isinstance(payload_json.get("error_detail"), dict):
        return "hardware_failure"
    return None


__all__ = [
    "ACTION_MOVE_FORWARD",
    "ACTION_MOVE_TO_NG",
    "ACTION_PICK_AND_PUT",
    "ACTION_PUT_TO_BIN",
    "ACTION_TARGET_ROLES",
    "EVENT_ROUGH_SORTER_STORAGE_RETRY",
    "EVENT_SCAN_COMPLETED",
    "NG_REASON_BARCODE_INCOMPLETE",
    "NG_REASON_BARCODE_INVALID",
    "NG_REASON_BARCODE_RULE_NG",
    "NG_REASON_MEASUREMENT_NG",
    "NG_REASON_WMS_REJECTED",
    "PHASE_COMPLETED",
    "PHASE_MOVING_FORWARD",
    "PHASE_NG_MOVING",
    "PHASE_PICK_TO_PIPELINE",
    "PHASE_PUTTING_TO_BIN",
    "ROLE_CONVEYOR",
    "ROLE_INPUT_ARM",
    "ROLE_OUTPUT_ARM",
    "ROUGH_SORTER_CONTRACT_VERSION",
    "ROUGH_SORTER_PLUGIN_KEY",
    "ROUGH_SORTER_RACK_WAIT_CONTEXT_STATE",
    "ROUGH_SORTER_SCANNED_CONTEXT_STATE",
    "build_move_forward_payload",
    "build_move_to_ng_payload",
    "build_pick_and_put_payload",
    "build_put_to_bin_payload",
    "classify_rough_sorter_result",
    "normalize_six_in_one_payload",
    "resolve_rough_sorter_business_key",
]
