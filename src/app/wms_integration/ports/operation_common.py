"""WMS operation-specific models 共用的不可变值对象。"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

StableText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
DecimalValue = Annotated[Decimal, Field(allow_inf_nan=False)]
PositiveDecimal = Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]
NonNegativeDecimal = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]


class StrictWmsModel(BaseModel):
    """所有 wire model 的严格、不可变基类。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


class CursorRequest(StrictWmsModel):
    """列表查询的通用 cursor 输入。"""

    cursor: StableText | None = Field(default=None, max_length=500)
    page_size: int = Field(default=100, ge=1, le=500)


class EffectRequest(StrictWmsModel):
    """EFFECT 必须冻结的 WES 派发身份。"""

    dispatch_key: StableText = Field(max_length=240)


class EffectResult(StrictWmsModel):
    """同步或异步终态结果必须回显的关联字段。"""

    dispatch_key: StableText = Field(max_length=240)
    provider_reference: StableText = Field(max_length=160)
    source_version: StableText = Field(max_length=160)


__all__ = [
    "CursorRequest",
    "DecimalValue",
    "EffectRequest",
    "EffectResult",
    "NonNegativeDecimal",
    "PositiveDecimal",
    "StableText",
    "StrictWmsModel",
]
