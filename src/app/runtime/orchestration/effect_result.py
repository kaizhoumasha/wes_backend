# 旧 runtime 镜像实现:src.workline_runtime.effect_result 的平级副本
# 旧 runtime 入口删除后,本模块承载正式实现。

"""Runtime effect disposition contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.core.task_queue_gateway import OutboxDispatchTarget


class WriteBackDisposition(str, Enum):
    """How the current Inbox should be finalized after runtime effects."""

    PROCESSED = "PROCESSED"
    RESOURCE_RETRY = "RESOURCE_RETRY"


@dataclass(frozen=True)
class RuntimeIntentEffectResult:
    """Small neutral result shared by runtime effects, write-back, and processor."""

    disposition: WriteBackDisposition = WriteBackDisposition.PROCESSED
    business_reject_evidence: dict[str, Any] | None = None
    outbox_dispatch_targets: frozenset[OutboxDispatchTarget] = frozenset()

    @classmethod
    def processed(
        cls,
        *,
        outbox_dispatch_targets: frozenset[OutboxDispatchTarget] = frozenset(),
    ) -> RuntimeIntentEffectResult:
        return cls(
            disposition=WriteBackDisposition.PROCESSED,
            outbox_dispatch_targets=outbox_dispatch_targets,
        )

    @classmethod
    def resource_retry(cls) -> RuntimeIntentEffectResult:
        return cls(disposition=WriteBackDisposition.RESOURCE_RETRY)

    @classmethod
    def business_rejected(cls, evidence: dict[str, Any]) -> RuntimeIntentEffectResult:
        return cls(
            disposition=WriteBackDisposition.PROCESSED,
            business_reject_evidence=dict(evidence),
        )


__all__ = ["RuntimeIntentEffectResult", "WriteBackDisposition"]
