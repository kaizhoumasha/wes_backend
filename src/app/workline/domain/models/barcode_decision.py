"""条码业务判定领域模型。"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class BarcodeDecisionType(StrEnum):
    """条码判定结果。"""

    OK = "OK"
    NG = "NG"
    INVALID = "INVALID"


class BarcodeDecision(BaseModel):
    """条码业务判定结果。"""

    barcode: str = Field(default="", description="主条码")
    barcodes: list[str] = Field(default_factory=list, description="所有可用条码")
    decision: BarcodeDecisionType = Field(description="业务判定结果")
    reason_code: str | None = Field(default=None, description="判定原因代码")
    reason_message: str | None = Field(default=None, description="判定原因描述")
