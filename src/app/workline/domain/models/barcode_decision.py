"""条码业务判定领域模型。"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from src.app.workline.domain.contracts import SixInOne


class BarcodeDecisionType(StrEnum):
    """条码判定结果。"""

    OK = "OK"
    NG = "NG"

    """条码无效。"""
    INVALID = "INVALID"
    """条码不完整。"""
    INCOMPLETE = "INCOMPLETE"


class BarcodeDecision(BaseModel):
    """条码业务判定结果。

    包含六合一码数据及判定结果，6个条码全部有值才算 OK。
    """

    six_in_one: SixInOne = Field(description="六合一码数据")
    decision: BarcodeDecisionType = Field(description="业务判定结果")
    reason_code: str | None = Field(default=None, description="判定原因代码")
    reason_message: str | None = Field(default=None, description="判定原因描述")

    @property
    def business_key(self) -> str:
        """统一业务主键。"""

        return self.six_in_one.business_key or ""

    @property
    def barcodes(self) -> list[str]:
        """所有可用条码值列表。"""

        return self.six_in_one.barcode_values
