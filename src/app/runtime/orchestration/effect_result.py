# 阶段 2 burn-down C5a 镜像:src.workline_runtime.effect_result 的平级副本
# wlr 目录在阶段 3 整体删除时,本镜像改名为正式模块。

"""Runtime effect disposition contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class WriteBackDisposition(str, Enum):
    """How the current Inbox should be finalized after runtime effects."""

    PROCESSED = "PROCESSED"
    RESOURCE_RETRY = "RESOURCE_RETRY"


@dataclass(frozen=True)
class RuntimeIntentEffectResult:
    """Small neutral result shared by runtime effects, write-back, and processor."""

    disposition: WriteBackDisposition = WriteBackDisposition.PROCESSED

    @classmethod
    def processed(cls) -> RuntimeIntentEffectResult:
        return cls(disposition=WriteBackDisposition.PROCESSED)

    @classmethod
    def resource_retry(cls) -> RuntimeIntentEffectResult:
        return cls(disposition=WriteBackDisposition.RESOURCE_RETRY)


__all__ = ["RuntimeIntentEffectResult", "WriteBackDisposition"]
