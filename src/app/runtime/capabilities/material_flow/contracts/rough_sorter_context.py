"""粗分机插件 Session context 与 Q19 首次准入事实。"""

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RoughSorterQ19AdmissionDecision(BaseModel):
    """设备命令前持久化、crash/replay 不可改写的 Q19 typed decision。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_canonical_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    decision: str = Field(pattern=r"^(ADMIT|REJECT)$")
    reason_code: str | None = Field(default=None, max_length=120)
    grn_id: str | None = Field(default=None, max_length=120)
    po_number: str | None = Field(default=None, max_length=120)
    po_item: str | None = Field(default=None, max_length=120)
    material_code: str | None = Field(default=None, max_length=120)
    pkg_id: str | None = Field(default=None, max_length=160)
    measurement_decision: str = Field(pattern=r"^(PASS|REJECT)$")
    standard_reel_diameter_mm: Decimal = Field(gt=0, allow_inf_nan=False)
    reel_diameter_tolerance_mm: Decimal = Field(ge=0, allow_inf_nan=False)
    standard_reel_thickness_mm: Decimal = Field(gt=0, allow_inf_nan=False)
    reel_thickness_tolerance_mm: Decimal = Field(ge=0, allow_inf_nan=False)
    rule_version: str = Field(min_length=1, max_length=160)
    source_version: str = Field(min_length=1, max_length=160)
    evidence_reference: str = Field(min_length=1, max_length=240)


class RoughSorterContext(BaseModel):
    """粗分机业务上下文，仅保存可 JSON 序列化快照。"""

    six_in_one: dict[str, Any] = Field(default_factory=dict)
    business_key: str | None = None
    measurement: dict[str, Any] = Field(default_factory=dict)
    wms_admission_decision: RoughSorterQ19AdmissionDecision | None = None
    active_bin_rack: dict[str, Any] | None = None
    target_bin_location: dict[str, Any] | str | None = None
    rack_operation: dict[str, Any] = Field(default_factory=dict)
    ng_reason: dict[str, Any] = Field(default_factory=dict)
    phase: str | None = None


__all__ = ["RoughSorterContext", "RoughSorterQ19AdmissionDecision"]
