"""SYSTEM_CAPABILITY EFFECT 专用 claim 合同。"""

from __future__ import annotations

from enum import Enum


class SystemCapabilityClaimResult(str, Enum):
    NEW = "NEW"
    MATCH = "MATCH"


class SystemCapabilityIdempotencyConflict(Exception):
    """同一 runtime-owned effect identity 出现不同 payload。"""

    def __init__(
        self,
        *,
        provider_code: str,
        operation_kind: str,
        idempotency_key: str,
        existing_request_hash: str,
        incoming_request_hash: str,
        correlation_id: str | None = None,
    ) -> None:
        super().__init__("system capability idempotency conflict")
        self.provider_code = provider_code
        self.operation_kind = operation_kind
        self.idempotency_key = idempotency_key
        self.existing_request_hash = existing_request_hash
        self.incoming_request_hash = incoming_request_hash
        self.correlation_id = correlation_id


__all__ = ["SystemCapabilityClaimResult", "SystemCapabilityIdempotencyConflict"]
