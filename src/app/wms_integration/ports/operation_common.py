"""WMS operation-specific models 共用的不可变值对象。"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, TypeVar, get_origin

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints, model_validator

_RFC3339_UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?\+00:00$")


def _parse_json_decimal(value: Any) -> Decimal:
    """精确数值 wire 只接收 JSON string；内部已解析 Decimal 可原样复用。"""

    if isinstance(value, Decimal):
        return value
    if not isinstance(value, str):
        # Pydantic 不会把 validator 抛出的 TypeError 包装成 ValidationError，必须保留 ValueError。
        raise ValueError("decimal wire value must be a JSON string")  # noqa: TRY004
    return Decimal(value)


def validate_rfc3339_utc_timestamp(value: Any) -> str:
    """只接受规范且语义有效的 RFC 3339 UTC 时间文本。"""

    if not isinstance(value, str) or not _RFC3339_UTC_TIMESTAMP_RE.fullmatch(value):
        raise ValueError("timestamp must be an RFC 3339 UTC timestamp with +00:00 offset")
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("timestamp must be a valid RFC 3339 UTC timestamp") from exc
    return value


StableText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Rfc3339UtcTimestamp = Annotated[str, BeforeValidator(validate_rfc3339_utc_timestamp)]
DecimalValue = Annotated[Decimal, BeforeValidator(_parse_json_decimal), Field(allow_inf_nan=False)]
PositiveDecimal = Annotated[Decimal, BeforeValidator(_parse_json_decimal), Field(gt=0, allow_inf_nan=False)]
NonNegativeDecimal = Annotated[Decimal, BeforeValidator(_parse_json_decimal), Field(ge=0, allow_inf_nan=False)]
StrictModelT = TypeVar("StrictModelT", bound="StrictWmsModel")


class StrictWmsModel(BaseModel):
    """所有 wire model 的严格、不可变基类。"""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @model_validator(mode="before")
    @classmethod
    def normalize_json_arrays(cls, value: Any) -> Any:
        """JSON array 对 tuple wire 字段是唯一结构表示；只做容器归一化，不转换标量。"""

        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        for field_name, field in cls.model_fields.items():
            if get_origin(field.annotation) is tuple and isinstance(normalized.get(field_name), list):
                normalized[field_name] = tuple(normalized[field_name])
        return normalized


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


def validate_json_payload(model: type[StrictModelT], payload: Any) -> StrictModelT:
    """按 JSON wire 语义校验已解码 payload，保持 strict scalar 且不接受 Python 隐式转换。"""

    import json

    try:
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError("payload must contain finite JSON values") from exc
    return model.model_validate_json(encoded)


__all__ = [
    "CursorRequest",
    "DecimalValue",
    "EffectRequest",
    "EffectResult",
    "NonNegativeDecimal",
    "PositiveDecimal",
    "Rfc3339UtcTimestamp",
    "StableText",
    "StrictWmsModel",
    "validate_json_payload",
    "validate_rfc3339_utc_timestamp",
]
