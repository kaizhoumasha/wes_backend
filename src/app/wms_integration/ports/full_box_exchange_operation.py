"""满箱交换 operation 的唯一领域合同。"""

from __future__ import annotations

from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

OPERATION_IDENTITY = "wms.fulfillment.full_box_exchange@v1"
StableText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class FullBoxExchangeOperationRequest(BaseModel):
    """冻结到 intent/outbox 前的满箱交换请求。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dispatch_key: StableText = Field(max_length=240)
    provider_code: StableText = Field(max_length=60)
    rack_id: StableText = Field(max_length=120)
    empty_box_id: StableText = Field(max_length=120)
    full_box_id: StableText = Field(max_length=120)
    workline_id: int | None = Field(default=None, gt=0)
    session_id: int | None = Field(default=None, gt=0)
    trace_id: StableText | None = Field(default=None, max_length=120)


class FullBoxExchangeOperationResult(BaseModel):
    """满箱交换 callback 归一化后的领域结果。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dispatch_key: StableText = Field(max_length=240)
    rack_id: StableText = Field(max_length=120)
    empty_box_id: StableText = Field(max_length=120)
    full_box_id: StableText = Field(max_length=120)
    accepted: bool
    exchange_request_code: StableText | None = Field(default=None, max_length=120)
    reason_code: StableText | None = Field(default=None, max_length=120)
    source_version: StableText | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def require_rejection_reason(self) -> FullBoxExchangeOperationResult:
        if not self.accepted and self.reason_code is None:
            raise ValueError("rejected full_box_exchange result requires reason_code")
        return self


class FullBoxExchangeOperationPort(Protocol):
    """按单个 operation 暴露的稳定 effect Port。"""

    async def execute(self, request: FullBoxExchangeOperationRequest) -> FullBoxExchangeOperationResult: ...


__all__ = [
    "OPERATION_IDENTITY",
    "FullBoxExchangeOperationPort",
    "FullBoxExchangeOperationRequest",
    "FullBoxExchangeOperationResult",
]
