"""条码业务判定领域模型。"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from src.workline_runtime.payloads import SixInOne


class BarcodeDecisionType(StrEnum):
    """条码判定结果。"""

    OK = "OK"
    NG = "NG"
    INVALID = "INVALID"
    INCOMPLETE = "INCOMPLETE"


class BarcodeDecision(BaseModel):
    """条码业务判定结果。

    包含六合一码数据及判定结果，6个条码全部有值才算 OK。
    """

    # 六合一码数据（输入）
    six_in_one: SixInOne = Field(description="六合一码数据")

    # 判定结果（输出）
    decision: BarcodeDecisionType = Field(description="业务判定结果")
    reason_code: str | None = Field(default=None, description="判定原因代码")
    reason_message: str | None = Field(default=None, description="判定原因描述")

    @property
    def pkg_id(self) -> str:
        """追溯主键：包装/箱号ID"""
        return self.six_in_one.PkgID or ""

    @property
    def barcodes(self) -> list[str]:
        """所有可用条码值列表"""
        return [
            value
            for value in [
                self.six_in_one.HHPN,
                self.six_in_one.MfrPN,
                self.six_in_one.Qty,
                self.six_in_one.DateCode,
                self.six_in_one.LotCode,
                self.six_in_one.PkgID,
            ]
            if value
        ]
