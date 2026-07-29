"""粗分机库存准入纯 policy 的 typed input、decision 与 provenance。"""

from __future__ import annotations

from decimal import Decimal  # noqa: TC003 - Pydantic runtime validation 需要具体类型。
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from src.app.wms_integration.ports.inventory_operations import (  # noqa: TC001 - Pydantic 运行时需要具体类型。
    InventorySnapshotQueryResult,
)

POLICY_VERSION = "rough-sorter-inventory-admission.v1"
StableString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
StableHash = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"),
]


class RoughSorterBindingSnapshot(BaseModel):
    """纯 policy 可重放所需的不可变插件 binding 摘要。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    binding_id: int = Field(gt=0)
    binding_version: int = Field(gt=0)
    profile_identity: StableString
    plugin_config_hash: StableHash
    generated_index_digest: StableHash


class RoughSorterInventoryQueryOutcomeKind(str, Enum):
    """进入 policy 前的封闭 QUERY outcome 分类。"""

    SUCCESS = "SUCCESS"
    BUSINESS_REJECT = "BUSINESS_REJECT"
    TECHNICAL_FAILURE = "TECHNICAL_FAILURE"
    CONTRACT_FAILURE = "CONTRACT_FAILURE"
    INVALID = "INVALID"


class RoughSorterInventoryQuerySnapshot(BaseModel):
    """Handler 从一次 typed QUERY outcome 归一化出的纯输入。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome_kind: RoughSorterInventoryQueryOutcomeKind
    result: InventorySnapshotQueryResult | None = None
    evidence_key: StableString | None = None
    reason_code: StableString | None = None
    message: StableString | None = None
    retryable: bool | None = None
    retry_after_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)


class RoughSorterInventoryAdmissionPolicyInput(BaseModel):
    """完整、自包含、可重放的库存准入 policy 输入。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    material_code: StableString = Field(max_length=120)
    lot_no: StableString = Field(max_length=120)
    warehouse_code: StableString = Field(max_length=120)
    owner_code: StableString = Field(max_length=120)
    binding_snapshot: RoughSorterBindingSnapshot
    supported_profile_identities: tuple[StableString, ...] = Field(min_length=1)
    source_operation: StableString
    query_snapshot: RoughSorterInventoryQuerySnapshot | None

    @field_validator("supported_profile_identities")
    @classmethod
    def canonicalize_supported_profiles(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """版本集合必须稳定排序且去重，避免 replay 输入受 hash 顺序影响。"""

        return tuple(sorted(set(value)))


class RoughSorterInventoryAdmissionEvidence(BaseModel):
    """决定 ADMIT/REJECT 的请求与物料/批次匹配摘要。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    material_code: StableString
    lot_no: StableString
    warehouse_code: StableString
    matched_item_count: int = Field(ge=0)
    available_quantity: Decimal = Field(ge=0, allow_inf_nan=False)


class RoughSorterInventorySourceProvenance(BaseModel):
    """WMS authority snapshot 的来源、evidence 与版本依据。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_identity: StableString
    outcome_kind: Literal["MISSING"] | RoughSorterInventoryQueryOutcomeKind
    query_owner_code: StableString
    evidence_key: StableString | None = None
    source_version: StableString | None = None
    reason_code: StableString | None = None
    message: StableString | None = None
    retryable: bool | None = None
    retry_after_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)


class RoughSorterInventoryDecisionProvenance(BaseModel):
    """重放 policy 决策所需的 policy、source、binding 与支持版本集合。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: Literal["rough-sorter-inventory-admission.v1"] = POLICY_VERSION
    source: RoughSorterInventorySourceProvenance
    binding: RoughSorterBindingSnapshot
    supported_profile_identities: tuple[StableString, ...]


class RoughSorterInventoryAdmissionDecision(BaseModel):
    """粗分机库存准入的封闭、可解释 decision。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: Literal["ADMIT", "REJECT", "HOLD"]
    reason_code: StableString
    evidence: RoughSorterInventoryAdmissionEvidence
    provenance: RoughSorterInventoryDecisionProvenance


__all__ = [
    "POLICY_VERSION",
    "RoughSorterBindingSnapshot",
    "RoughSorterInventoryAdmissionDecision",
    "RoughSorterInventoryAdmissionEvidence",
    "RoughSorterInventoryAdmissionPolicyInput",
    "RoughSorterInventoryDecisionProvenance",
    "RoughSorterInventoryQueryOutcomeKind",
    "RoughSorterInventoryQuerySnapshot",
    "RoughSorterInventorySourceProvenance",
]
