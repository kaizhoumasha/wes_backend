"""Runtime Hold API schemas."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - Pydantic response models need runtime type access
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class NgReasonInput(BaseModel):
    """Operator-selected NG reason."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(description="NG reason source")
    code: str = Field(min_length=1, max_length=100, description="Canonical NG reason code")
    label: str = Field(min_length=1, max_length=200, description="Human-readable NG reason label")


class PhysicalHandoffEvidenceInput(BaseModel):
    """Client-submitted physical handoff evidence.

    Server-owned facts such as confirmed_by, confirmed_at and material_identity
    are intentionally not part of this schema.
    """

    model_config = ConfigDict(extra="forbid")

    ng_location_code: str = Field(min_length=1, max_length=100, description="NG 暂存位置编码")
    ng_location_scan: str = Field(min_length=1, max_length=500, description="NG 位置扫码原文")
    material_scan_payload: dict[str, Any] | str = Field(description="现场重新扫描到的物料原文")
    line_clear_checked: bool = Field(description="已确认工位/设备无残留同一物料")
    late_callback_reviewed: bool = Field(description="已复核迟到 callback evidence")
    handoff_witness_id: str | None = Field(default=None, max_length=100, description="可选见证人")


class ResolveRuntimeHoldRequest(BaseModel):
    """Resolve Runtime Hold request."""

    model_config = ConfigDict(extra="forbid")

    resolution: Literal["COMPLETED", "FAILED", "CANCELLED"] = Field(description="Session 结论")
    checks: dict[str, bool] = Field(description="服务端要求的 release checklist")
    operator_note: str = Field(min_length=1, max_length=1000, description="现场确认说明")
    material_disposition: Literal["CONTINUE", "RETURN_TO_NG"] = Field(description="物料处置")
    ng_reason: NgReasonInput | None = Field(default=None, description="RETURN_TO_NG 时必填")
    physical_handoff_evidence: PhysicalHandoffEvidenceInput | None = Field(
        default=None,
        description="RETURN_TO_NG 时必填；只包含客户端可提交证据",
    )
    result_payload: dict[str, Any] | None = Field(default=None, description="CONTINUE/COMPLETED 可补充业务结果")
    hold_version: int = Field(ge=0, description="RuntimeHold 乐观锁版本")
    latest_evidence_hash: str = Field(min_length=1, max_length=200, description="页面看到的最新证据 hash")


class RuntimeHoldSummary(BaseModel):
    """Runtime Hold summary."""

    id: int
    hold_type: str
    status: str
    blocking: bool
    workline_id: int
    session_id: int | None = None
    trace_id: str | None = None
    plugin_key: str | None = None
    contract_version: str | None = None
    source_reason: str
    material_disposition: str | None = None
    ng_reason_code: str | None = None
    ng_reason_label: str | None = None
    version: int
    created_at: datetime | None = None
    resolved_at: datetime | None = None
    resolved_by: int | None = None


class RuntimeHoldSource(BaseModel):
    """Runtime Hold source refs."""

    source_kind: str
    source_reason: str
    source_inbox_id: int | None = None
    source_outbox_id: int | None = None
    source_command_id: int | None = None
    source_device_id: int | None = None
    source_idempotency_key: str


class FailedCommandEvidence(BaseModel):
    """Failed command evidence for operator review."""

    command_id: int | None = None
    command_code: str | None = None
    status: str | None = None
    failure_code: str | None = None
    reconciliation_reason: str | None = None
    result_evidence_id: int | None = None


class RuntimeHoldReleaseEligibility(BaseModel):
    """Current release decision model."""

    can_resolve: bool
    required_checks: list[str] = Field(default_factory=list)
    allowed_resolutions: list[str] = Field(default_factory=list)
    allowed_material_dispositions: list[str] = Field(default_factory=list)
    latest_evidence_hash: str
    reason: str | None = None


class RuntimeHoldBlocker(BaseModel):
    """Another active hold blocking the same WorkLine."""

    id: int
    hold_type: str
    status: str
    source_reason: str
    session_id: int | None = None
    source_device_id: int | None = None


class RuntimeHoldDetailResponse(BaseModel):
    """Runtime Hold detail response."""

    summary: RuntimeHoldSummary
    source: RuntimeHoldSource
    evidence_snapshot_json: dict[str, Any]
    release_evidence_json: dict[str, Any]
    failed_command_evidence: FailedCommandEvidence | None = None
    release_eligibility: RuntimeHoldReleaseEligibility
    blockers: list[RuntimeHoldBlocker] = Field(default_factory=list)


class ResolveRuntimeHoldResponse(BaseModel):
    """Resolve Runtime Hold response."""

    hold_id: int
    status: str
    workline_id: int
    workline_runtime_status: str
    remaining_active_blocking_holds: int
    released_outbox_count: int
    ng_return_item_id: int | None = None
    created_inbox_id: int | None = None


class NgReasonOption(BaseModel):
    """NG reason option."""

    source: str
    code: str
    label: str
    plugin_key: str | None = None
    contract_version: str | None = None
    maps_from: list[str] = Field(default_factory=list)


class NgReturnItemResponse(BaseModel):
    """NG return item response."""

    id: int
    source_workline_id: int
    source_session_id: int
    source_command_id: int | None = None
    source_event_id: str | None = None
    material_identity_key: str
    material_identity_json: dict[str, Any]
    physical_handoff_evidence_json: dict[str, Any]
    disposition: str
    ng_reason_source: str
    ng_reason_code: str
    ng_reason_label: str
    operator_note: str | None = None
    created_from_runtime_hold_id: int | None = None
    status: str
    confirmed_by: int | None = None
    confirmed_at: datetime | None = None
    created_at: datetime | None = None


__all__ = [
    "FailedCommandEvidence",
    "NgReasonInput",
    "NgReasonOption",
    "NgReturnItemResponse",
    "PhysicalHandoffEvidenceInput",
    "ResolveRuntimeHoldRequest",
    "ResolveRuntimeHoldResponse",
    "RuntimeHoldBlocker",
    "RuntimeHoldDetailResponse",
    "RuntimeHoldReleaseEligibility",
    "RuntimeHoldSource",
    "RuntimeHoldSummary",
]
