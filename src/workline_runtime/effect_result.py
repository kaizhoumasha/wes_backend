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
