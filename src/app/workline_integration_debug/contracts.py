"""人工出库联调台的固定状态与现场提示。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TypedDict


class IntegrationDebugScenario(StrEnum):
    MANUAL_OUTBOUND_PICKING_V1 = "manual_outbound_picking@v1"


class IntegrationDebugProfile(StrEnum):
    CONTRACT_SIMULATION = "CONTRACT_SIMULATION"
    DEVICE_INTEGRATION = "DEVICE_INTEGRATION"
    FULL_SITE_INTEGRATION = "FULL_SITE_INTEGRATION"


class IntegrationDebugRunStatus(StrEnum):
    CREATED = "CREATED"
    WAITING_TASK = "WAITING_TASK"
    ACTIVE = "ACTIVE"
    WAITING_EXTERNAL = "WAITING_EXTERNAL"
    COMPLETED = "COMPLETED"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"
    CLOSED_BY_OPERATOR = "CLOSED_BY_OPERATOR"


class IntegrationDebugPhase(StrEnum):
    BIND_TASK = "BIND_TASK"
    TASK_PREPARE = "TASK_PREPARE"
    PLAN_RECEIPT = "PLAN_RECEIPT"
    RACK_TRANSPORT = "RACK_TRANSPORT"
    RACK_ARRIVAL = "RACK_ARRIVAL"
    BIN_INBOUND_BATCH = "BIN_INBOUND_BATCH"
    BIN_TRANSPORT = "BIN_TRANSPORT"
    POINT1_ARRIVAL = "POINT1_ARRIVAL"
    POINT2_SCAN = "POINT2_SCAN"
    WORK_ADMISSION = "WORK_ADMISSION"
    WORK_COMPLETION = "WORK_COMPLETION"
    POINT2_RELEASE = "POINT2_RELEASE"
    COMPLETION_REPORT = "COMPLETION_REPORT"
    POINT3_ROUTE = "POINT3_ROUTE"
    RETURN_BUFFER = "RETURN_BUFFER"
    BIN_RETURN_BATCH = "BIN_RETURN_BATCH"
    BIN_RETURN_TRANSPORT = "BIN_RETURN_TRANSPORT"
    RACK_DEPARTURE = "RACK_DEPARTURE"
    TASK_COMPLETION = "TASK_COMPLETION"
    CLEANUP = "CLEANUP"


class IntegrationTransportActionKind(StrEnum):
    MOVE_RACK = "MOVE_RACK"
    ROTATE_RACK = "ROTATE_RACK"
    MOVE_BINS = "MOVE_BINS"


class ManualOutboundSiteConfiguration(TypedDict):
    outbound_rcs_template: str
    return_rcs_template: str
    bin_rack_positions: list[str]
    outbound_transfer_position: str
    return_zone_code: str
    infeed_position: str
    outfeed_position: str
    ecs_endpoint_base_url: str
    scan_device_codes: list[str]


MANUAL_OUTBOUND_SITE_CONFIGURATION: ManualOutboundSiteConfiguration = {
    "outbound_rcs_template": "CTU01",
    "return_rcs_template": "CTU03",
    "bin_rack_positions": ["KT16", "KT17"],
    "outbound_transfer_position": "OUT65",
    "return_zone_code": "WH05",
    "infeed_position": "CNV0301",
    "outfeed_position": "CNV0302",
    "ecs_endpoint_base_url": "http://10.24.209.26:8080/",
    "scan_device_codes": ["STATION_SCAN9", "STATION_SCAN10", "STATION_SCAN11", "STATION_SCAN12"],
}


@dataclass(frozen=True, slots=True)
class IntegrationTransportAction:
    kind: IntegrationTransportActionKind
    client_request_id: str
    rack_id: str
    source: dict[str, str]
    target: dict[str, str]
    rcs_template_id: str
    target_face: str | None = None
    bin_code: str | None = None


_ALLOWED_TRANSITIONS = {
    IntegrationDebugRunStatus.CREATED: {IntegrationDebugRunStatus.WAITING_TASK},
    IntegrationDebugRunStatus.WAITING_TASK: {
        IntegrationDebugRunStatus.ACTIVE,
        IntegrationDebugRunStatus.NEEDS_ATTENTION,
    },
    IntegrationDebugRunStatus.ACTIVE: {
        IntegrationDebugRunStatus.WAITING_EXTERNAL,
        IntegrationDebugRunStatus.COMPLETED,
        IntegrationDebugRunStatus.NEEDS_ATTENTION,
    },
    IntegrationDebugRunStatus.WAITING_EXTERNAL: {
        IntegrationDebugRunStatus.ACTIVE,
        IntegrationDebugRunStatus.NEEDS_ATTENTION,
    },
    IntegrationDebugRunStatus.COMPLETED: {IntegrationDebugRunStatus.CLOSED_BY_OPERATOR},
    IntegrationDebugRunStatus.NEEDS_ATTENTION: {IntegrationDebugRunStatus.CLOSED_BY_OPERATOR},
    IntegrationDebugRunStatus.CLOSED_BY_OPERATOR: set(),
}


def require_transition(current: IntegrationDebugRunStatus, target: IntegrationDebugRunStatus) -> None:
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"联调 run 状态不能从 {current.value} 变为 {target.value}")


def profile_uses_real_transport(profile: IntegrationDebugProfile) -> bool:
    return profile is IntegrationDebugProfile.FULL_SITE_INTEGRATION


def profile_uses_real_ecs(profile: IntegrationDebugProfile) -> bool:
    return profile in {IntegrationDebugProfile.DEVICE_INTEGRATION, IntegrationDebugProfile.FULL_SITE_INTEGRATION}


def wms_team_guidance(code: str) -> str:
    guidance = {
        "WAIT": (
            "C# 处理：HTTP 200 且 code=DECIDED、data.result=WAIT 后，读取 retry_after_ms；到期重求值时"
            "使用新的 operation_id 重新求值，不重放旧响应身份。"
        ),
        "UNAVAILABLE": (
            "C# 处理：HTTP 503 时保留原 operation_id、timestamp 和完整请求 JSON，按 Retry-After 重试；"
            "禁止生成新 operation_id。"
        ),
        "CONFLICT": (
            "C# 处理：HTTP 409 时停止自动重试，核对同一 operation_id 的完整请求内容与首次请求是否逐字段一致。"
        ),
        "RECEIVED": (
            "C# 处理：HTTP 202 且 code=RECEIVED 只证明 WES 已可靠接收 Evidence；不得据此认定料箱已放行或物理完成。"
        ),
    }
    return guidance.get(
        code,
        f"C# 处理：记录 HTTP 状态、operation_id、code 和完整响应；未识别 code={code} 时停止流程并人工核对。",
    )


__all__ = [
    "IntegrationDebugPhase",
    "IntegrationDebugProfile",
    "IntegrationDebugRunStatus",
    "IntegrationDebugScenario",
    "IntegrationTransportAction",
    "IntegrationTransportActionKind",
    "profile_uses_real_ecs",
    "profile_uses_real_transport",
    "require_transition",
    "wms_team_guidance",
]
