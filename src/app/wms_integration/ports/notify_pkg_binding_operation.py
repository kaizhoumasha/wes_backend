"""料盘绑定通知 operation 的唯一领域合同。"""

from __future__ import annotations

from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

OPERATION_IDENTITY = "wms.fulfillment.notify_pkg_binding@v1"
StableText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class NotifyPackageBindingOperationRequest(BaseModel):
    """冻结到 intent/outbox 前的料盘绑定请求。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dispatch_key: StableText = Field(max_length=240)
    package_id: StableText = Field(max_length=120)
    pallet_id: StableText = Field(max_length=120)
    station_code: StableText = Field(max_length=120)
    workline_id: int | None = Field(default=None, gt=0)
    session_id: int | None = Field(default=None, gt=0)
    trace_id: StableText | None = Field(default=None, max_length=120)


class NotifyPackageBindingOperationResult(BaseModel):
    """料盘绑定 callback 归一化后的领域结果。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dispatch_key: StableText = Field(max_length=240)
    package_id: StableText = Field(max_length=120)
    pallet_id: StableText = Field(max_length=120)
    accepted: bool
    bound_at: StableText | None = Field(default=None, max_length=80)
    reason_code: StableText | None = Field(default=None, max_length=120)
    source_version: StableText | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def require_rejection_reason(self) -> NotifyPackageBindingOperationResult:
        if not self.accepted and self.reason_code is None:
            raise ValueError("rejected notify_pkg_binding result requires reason_code")
        return self


class NotifyPackageBindingOperationPort(Protocol):
    """按单个 operation 暴露的稳定 effect Port。"""

    async def execute(self, request: NotifyPackageBindingOperationRequest) -> NotifyPackageBindingOperationResult: ...


__all__ = [
    "OPERATION_IDENTITY",
    "NotifyPackageBindingOperationPort",
    "NotifyPackageBindingOperationRequest",
    "NotifyPackageBindingOperationResult",
]
