"""SixInOne 统一语义模型（方案 A）。"""

from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, ConfigDict, model_validator


class SixInOne(BaseModel):
    """六合一码统一语义模型。

    方案 A 统一字段：
    - HHPN
    - MfrPN
    - Qty
    - DateCode
    - LotCode
    - PkgID

    说明：
    - `business_key` 是统一业务主键，不与任一单字段强绑定
    - 外部协议字段到本模型的映射，不通过兼容属性暴露，只通过集中解析函数处理
    """

    model_config = ConfigDict(populate_by_name=True)

    business_key: str | None = None

    HHPN: str | None = None
    MfrPN: str | None = None
    Qty: str | None = None
    DateCode: str | None = None
    LotCode: str | None = None
    PkgID: str | None = None

    @model_validator(mode="after")
    def _ensure_business_key(self) -> SixInOne:
        if not self.business_key and self.has_any_value:
            self.business_key = self.build_business_key()
        return self

    def build_business_key(self) -> str | None:
        """根据当前统一字段生成稳定业务主键。"""

        values = [value for value in self.barcode_values if value]
        if not values:
            return None
        payload = json.dumps(values, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    @property
    def barcode_values(self) -> list[str]:
        """返回所有非空条码值。"""

        return [
            value
            for value in [
                self.HHPN,
                self.MfrPN,
                self.Qty,
                self.DateCode,
                self.LotCode,
                self.PkgID,
            ]
            if value
        ]

    @property
    def has_any_value(self) -> bool:
        """是否至少包含一个有效条码字段。"""

        return any(self.barcode_values)

    @property
    def is_complete(self) -> bool:
        """是否 6 个统一字段均有值。"""

        return all(
            value not in (None, "")
            for value in [
                self.HHPN,
                self.MfrPN,
                self.Qty,
                self.DateCode,
                self.LotCode,
                self.PkgID,
            ]
        )

    @property
    def missing_fields(self) -> list[str]:
        """返回缺失字段名。"""

        missing: list[str] = []
        if self.HHPN in (None, ""):
            missing.append("HHPN")
        if self.MfrPN in (None, ""):
            missing.append("MfrPN")
        if self.Qty in (None, ""):
            missing.append("Qty")
        if self.DateCode in (None, ""):
            missing.append("DateCode")
        if self.LotCode in (None, ""):
            missing.append("LotCode")
        if self.PkgID in (None, ""):
            missing.append("PkgID")
        return missing


__all__ = ["SixInOne"]
