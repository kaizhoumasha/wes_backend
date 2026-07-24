"""WorkLine 目标态行为契约 guard（测试专用）。

表达目标态行为约束的 contract 壳, 供 tests/contracts/ 引用。
这些 contract 已升级到生产 runtime/orchestration 实现。

对应 SPEC P0-003 的 BC-01 ~ BC-10。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AdmissionDecision:
    """BC-01 start admission 目标态决策结果。"""

    accepted: bool
    reason_code: str | None = None
    session_created: bool = False
    intent_created: bool = False


def evaluate_start_admission(
    *,
    manifest_valid: bool,
    device_roles_satisfied: bool,
    external_ports_available: bool,
    projection_blocked: bool,
) -> AdmissionDecision:
    """BC-01: admission 必须校验 manifest/设备/port/projection 四前置。

    任一不满足即拒绝, 且不创建 session/intent。
    """
    if not manifest_valid:
        return AdmissionDecision(accepted=False, reason_code="INVALID_MANIFEST")
    if not device_roles_satisfied:
        return AdmissionDecision(accepted=False, reason_code="DEVICE_ROLE_UNSATISFIED")
    if not external_ports_available:
        return AdmissionDecision(accepted=False, reason_code="EXTERNAL_PORT_UNAVAILABLE")
    if projection_blocked:
        return AdmissionDecision(accepted=False, reason_code="PROJECTION_BLOCKED")
    return AdmissionDecision(accepted=True, session_created=True, intent_created=True)


@dataclass
class HandoffEvidence:
    """BC-03 handoff evidence。"""

    has_callback: bool = False
    has_intent_evidence: bool = False


def handoff_can_advance(evidence: HandoffEvidence) -> tuple[bool, str | None]:
    """BC-03: 交接只能由 callback 或 RuntimeIntentLog evidence 推进。

    无 evidence 时 HOLD/拒绝, 禁止 API 层直接改投影。
    """
    if evidence.has_callback or evidence.has_intent_evidence:
        return True, None
    return False, "NO_EVIDENCE"


@dataclass
class ActiveOwnership:
    """BC-04 active 归属。"""

    object_key: str
    workline_id: str
    is_active: bool = True
    transient_until: float | None = None  # transient owner TTL 占位; 主计划 §6.6 N=30s


def assert_single_active_ownership(
    existing: ActiveOwnership | None,
    new_claim: ActiveOwnership,
    *,
    now: float,
) -> tuple[bool, str | None]:
    """BC-04: 同一 object 在同一 WorkLine 内只能有一个可解释 active 归属。

    瞬态窗口(transient_until)内合法重复; 超时进入 RECONCILING。
    """
    if existing is None or not existing.is_active:
        return True, None
    if existing.object_key != new_claim.object_key or existing.workline_id != new_claim.workline_id:
        return True, None  # 不同对象或不同 workline, 不冲突
    # 同 object 同 workline 已有 active
    if existing.transient_until is not None and now <= existing.transient_until:
        return True, "TRANSIENT_WINDOW"  # 瞬态合法
    return False, "DUPLICATE_ACTIVE_OWNER"


# BC-08: 缺 event_id 的离散事件可以 ACK, 但不得推进 session 归属
def event_can_advance_correlation(event: dict[str, Any]) -> tuple[bool, str | None]:
    """缺 event_id 只 ACK, 不创建/推进 ExecutionCorrelation。"""
    data = event.get("data", event)
    event_id = data.get("event_id")
    if not event_id:
        return False, "MISSING_EVENT_ID"  # ACK 但不推进
    return True, None


# BC-10: Event_Push HTTP 响应只 ACK, 拦截 command-like 字段
COMMAND_LIKE_FIELDS = {
    "action",
    "command",
    "next_action",
    "next_command",
    "instruction",
    "task_type",
    "params",
    "target_loc",
    "source_loc",
}


def validate_event_push_response(response: dict[str, Any]) -> tuple[bool, str | None]:
    """Event_Push 响应必须固定 ACK; command-like 字段必须被拒绝。"""
    for field_name in COMMAND_LIKE_FIELDS:
        if field_name in response:
            return False, f"COMMAND_LIKE_FIELD_{field_name}"
    if response.get("status") != "ACK":
        return False, "NON_ACK_STATUS"
    return True, None


# BC-09: WMS 查询响应必须含 scope/authority/source/evidence_at
@dataclass
class AuthorityMetadata:
    """BC-09 / AUTHORITY_METADATA_BOUNDARY authority metadata。"""

    scope: str
    authority: str
    source: str
    evidence_at: str


def validate_authority_metadata(meta: AuthorityMetadata | dict | None) -> tuple[bool, str | None]:
    """查询响应缺 scope/authority/source/evidence_at 任一字段即失败。"""
    if meta is None:
        return False, "MISSING_AUTHORITY_METADATA"
    fields = ("scope", "authority", "source", "evidence_at")
    values = (
        (meta.scope, meta.authority, meta.source, meta.evidence_at)
        if isinstance(meta, AuthorityMetadata)
        else (meta.get(f) for f in fields)  # type: ignore[union-attr]
    )
    for name, value in zip(fields, values, strict=False):
        if not value:
            return False, f"MISSING_{name.upper()}"
    return True, None


__all__ = [
    "COMMAND_LIKE_FIELDS",
    "ActiveOwnership",
    "AdmissionDecision",
    "AuthorityMetadata",
    "HandoffEvidence",
    "assert_single_active_ownership",
    "evaluate_start_admission",
    "event_can_advance_correlation",
    "handoff_can_advance",
    "validate_authority_metadata",
    "validate_event_push_response",
]
