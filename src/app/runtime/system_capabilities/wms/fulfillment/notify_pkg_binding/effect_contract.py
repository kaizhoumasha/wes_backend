"""`notify_pkg_binding` OUTBOX_ASYNC admission 与 durable acceptance 合同。"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

StableText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class NotifyPackageBindingEffectPrecondition(BaseModel):
    """创建料盘绑定 EFFECT 前冻结的本地事实。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    package_id: StableText = Field(max_length=120)
    pallet_id: StableText = Field(max_length=120)
    local_physical_fact_recorded: Literal[True]


class NotifyPackageBindingEffectAdmission(BaseModel):
    """System Capability 执行前必须重建的 typed admission。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    precondition: NotifyPackageBindingEffectPrecondition
    fact_version: StableText = Field(max_length=120)


class NotifyPackageBindingDispatchAccepted(BaseModel):
    """只表示双账本 durable accepted，不表示 WMS 已完成绑定。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    accepted: Literal[True] = True
    dispatch_key: StableText = Field(max_length=240)


__all__ = [
    "NotifyPackageBindingDispatchAccepted",
    "NotifyPackageBindingEffectAdmission",
    "NotifyPackageBindingEffectPrecondition",
]
