"""粗分机 WMS 库存准入 typed input/output。"""

from __future__ import annotations

from decimal import Decimal  # noqa: TC003 - Pydantic runtime validation 需要具体类型。

from pydantic import BaseModel, ConfigDict, Field

PROFILE_IDENTITY = "wms.2026-07-06.material-flow.sandbox"


class RoughSorterBindingSnapshot(BaseModel):
    """QUERY 输入固定的不可变插件 binding 摘要。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    binding_id: int = Field(gt=0)
    binding_version: int = Field(gt=0)
    profile_identity: str = Field(min_length=1)
    plugin_config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_index_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class RoughSorterInventoryAdmissionInput(BaseModel):
    """包含业务键、物料批次、测量值和 binding 快照的准入输入。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    business_key: str = Field(min_length=1, max_length=160)
    hhpn: str = Field(min_length=1, max_length=120)
    lot_code: str = Field(min_length=1, max_length=120)
    warehouse_code: str = Field(min_length=1, max_length=120)
    owner_code: str = Field(min_length=1, max_length=120)
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
    "PROFILE_IDENTITY",
    "RoughSorterBindingSnapshot",
    "RoughSorterInventoryAdmissionInput",
    "RoughSorterInventoryAdmissionOutput",
]
