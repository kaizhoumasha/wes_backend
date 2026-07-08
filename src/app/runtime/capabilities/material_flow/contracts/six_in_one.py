"""SixInOne 统一语义模型（方案 A）。"""

from __future__ import annotations

import hashlib
import json
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, computed_field


class SixInOne(BaseModel):
    """六合一码统一语义模型。

    - HHPN
    - MfrPN
    - Qty
    - DateCode
    - LotCode
    - PkgID

    说明：
    - `business_key` 是由 `PkgID` 派生的统一业务主键
    """

    model_config = ConfigDict(populate_by_name=True)
    BUSINESS_FIELD_NAMES: ClassVar[tuple[str, ...]] = (
        "HHPN",
        "MfrPN",
        "Qty",
        "DateCode",
        "LotCode",
        "PkgID",
    )

    HHPN: str | None = Field(default=None, description="厂内料号")
    MfrPN: str | None = Field(default=None, description="供应商料号")
    Qty: str | None = Field(default=None, description="数量")
    DateCode: str | None = Field(default=None, description="日期")
    LotCode: str | None = Field(default=None, description="批次号")
    PkgID: str | None = Field(default=None, description="流水号")

    @staticmethod
    def _is_missing_value(value: str | None) -> bool:
        return value in (None, "")

    def build_business_key(self) -> str | None:
        """根据当前流水号（PkgID字段）生成稳定业务主键。"""

        if not self.PkgID:
            return None
        payload = json.dumps(self.PkgID, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    @computed_field
    @property
    def business_key(self) -> str | None:
        """由 PkgID 派生的稳定业务主键。"""

        return self.build_business_key()

    def iter_business_fields(self) -> list[tuple[str, str | None]]:
        """返回统一业务字段及其当前值。"""

        return [(field_name, getattr(self, field_name, None)) for field_name in self.BUSINESS_FIELD_NAMES]

    @property
    def barcode_values(self) -> list[str]:
        """返回所有非空条码值。"""

        return [value for _, value in self.iter_business_fields() if value]

    @property
    def has_any_value(self) -> bool:
        """是否至少包含一个有效条码字段。"""

        return any(value for _, value in self.iter_business_fields())

    @property
    def is_complete(self) -> bool:
        """是否 6 个统一字段均有值。"""

        return all(not self._is_missing_value(value) for _, value in self.iter_business_fields())

    @property
    def missing_fields(self) -> list[str]:
        """返回缺失字段名。"""

        return [field_name for field_name, value in self.iter_business_fields() if self._is_missing_value(value)]


__all__ = ["SixInOne"]
