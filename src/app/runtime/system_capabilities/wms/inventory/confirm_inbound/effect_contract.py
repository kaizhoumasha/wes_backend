"""`confirm_inbound` OUTBOX_ASYNC admission 与 durable acceptance 合同。"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

StableText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ConfirmInboundEffectPrecondition(BaseModel):
    """创建 WMS 入库 EFFECT 前冻结的本地事实。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    inbound_key: StableText = Field(max_length=120)
    local_physical_fact_recorded: Literal[True]


class ConfirmInboundEffectAdmission(BaseModel):
    """System Capability 执行前必须重建的 typed admission。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    precondition: ConfirmInboundEffectPrecondition
    fact_version: StableText = Field(max_length=120)


class ConfirmInboundDispatchAccepted(BaseModel):
    """只表示双账本 durable accepted，不表示 WMS 已完成入库。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    accepted: Literal[True] = True
    dispatch_key: StableText = Field(max_length=240)


__all__ = [
    "ConfirmInboundDispatchAccepted",
    "ConfirmInboundEffectAdmission",
    "ConfirmInboundEffectPrecondition",
]
