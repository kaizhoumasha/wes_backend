"""入库确认 operation 的唯一领域合同。"""

from __future__ import annotations

from decimal import Decimal  # noqa: TC003 - Pydantic 运行时需要 Decimal。
from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

OPERATION_IDENTITY = "wms.inventory.confirm_inbound@v1"
StableText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ConfirmInboundOperationRequest(BaseModel):
    """冻结到 intent/outbox 前的入库确认请求。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dispatch_key: StableText = Field(max_length=240)
    inbound_key: StableText = Field(max_length=120)
    material_code: StableText = Field(max_length=120)
    quantity: Decimal = Field(gt=0, allow_inf_nan=False)
    warehouse_code: StableText | None = Field(default=None, max_length=120)
    owner_code: StableText | None = Field(default=None, max_length=120)
    lot_no: StableText | None = Field(default=None, max_length=120)
    workline_id: int | None = Field(default=None, gt=0)
    session_id: int | None = Field(default=None, gt=0)
    trace_id: StableText | None = Field(default=None, max_length=120)


class ConfirmInboundOperationResult(BaseModel):
    """入库确认 callback 归一化后的领域结果。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dispatch_key: StableText = Field(max_length=240)
    inbound_key: StableText = Field(max_length=120)
    accepted: bool
    document_no: StableText | None = Field(default=None, max_length=120)
    reason_code: StableText | None = Field(default=None, max_length=120)
    source_version: StableText | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def require_rejection_reason(self) -> ConfirmInboundOperationResult:
        if not self.accepted and self.reason_code is None:
            raise ValueError("rejected confirm_inbound result requires reason_code")
        return self


class ConfirmInboundOperationPort(Protocol):
    """按单个 operation 暴露的稳定 effect Port。"""

    async def execute(self, request: ConfirmInboundOperationRequest) -> ConfirmInboundOperationResult: ...


__all__ = [
    "OPERATION_IDENTITY",
    "ConfirmInboundOperationPort",
    "ConfirmInboundOperationRequest",
    "ConfirmInboundOperationResult",
]
