"""粗分机 WMS 库存准入 typed input/output。"""

from __future__ import annotations

from decimal import Decimal  # noqa: TC003 - Pydantic runtime validation 需要具体类型。
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from src.app.runtime.capabilities.material_flow.contracts.rough_sorter_inventory_admission import (  # noqa: TC001 - Pydantic 运行时需要具体类型。
    RoughSorterBindingSnapshot,
)

PROFILE_FAMILY = "wms.2026-07-06.material-flow"
PROFILE_IDENTITY = f"{PROFILE_FAMILY}.sandbox"
SUPPORTED_PROFILE_IDENTITIES = frozenset(
    f"{PROFILE_FAMILY}.{environment}" for environment in ("sandbox", "staging", "production")
)
StableString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class RoughSorterInventoryAdmissionInput(BaseModel):
    """包含业务键、物料批次、测量值和 binding 快照的准入输入。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    business_key: StableString = Field(max_length=160)
    hhpn: StableString = Field(max_length=120)
    lot_code: StableString = Field(max_length=120)
    warehouse_code: StableString = Field(max_length=120)
    owner_code: StableString = Field(max_length=120)
    diameter_mm: Decimal = Field(gt=0, allow_inf_nan=False)
    thickness_mm: Decimal = Field(gt=0, allow_inf_nan=False)
    binding_snapshot: RoughSorterBindingSnapshot


class RoughSorterInventoryAdmissionOutput(BaseModel):
    """命中库存的最小、可审计成功摘要。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    accepted: bool
    material_code: str
    batch_no: str
    warehouse_code: str
    matched_item_count: int = Field(gt=0)
    available_quantity: Decimal = Field(ge=0, allow_inf_nan=False)
    source_version: str = Field(min_length=1)


__all__ = [
    "PROFILE_FAMILY",
    "PROFILE_IDENTITY",
    "SUPPORTED_PROFILE_IDENTITIES",
    "RoughSorterInventoryAdmissionInput",
    "RoughSorterInventoryAdmissionOutput",
]
