"""WMS 同步调用 typed exception。"""

from __future__ import annotations

from typing import Self


class WmsIntegrationError(Exception):
    """WMS 对接基础异常。"""

    default_retryable = False

    def __init__(
        self,
        message: str,
        *,
        operation_name: str,
        evidence_key: str | None,
        http_status: int | None = None,
        reason_code: str | None = None,
        retryable: bool | None = None,
        target_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.operation_name = operation_name
        self.evidence_key = evidence_key
        self.http_status = http_status
        self.reason_code = reason_code
        self.retryable = self.default_retryable if retryable is None else retryable
        self.target_code = target_code

    def with_evidence_key(self, evidence_key: str) -> Self:
        """返回携带 evidence_key 的同类异常。"""

        return self.__class__(
            self.message,
            operation_name=self.operation_name,
            evidence_key=evidence_key,
            http_status=self.http_status,
            reason_code=self.reason_code,
            retryable=self.retryable,
            target_code=self.target_code,
        )


class WmsBusinessRejectedError(WmsIntegrationError):
    """WMS 4xx 业务拒绝。"""

    default_retryable = False


class WmsUnavailableError(WmsIntegrationError):
    """WMS 依赖不可用。"""

    default_retryable = True


class WmsCircuitOpenError(WmsUnavailableError):
    """WMS 熔断器 OPEN，快速失败。"""


class WmsEvidencePersistenceError(WmsIntegrationError):
    """WMS 已返回成功，但本地 evidence/breaker 成功留痕失败。"""

    default_retryable = False


class WmsTimeoutError(WmsUnavailableError):
    """WMS HTTP 调用超时。"""


__all__ = [
    "WmsBusinessRejectedError",
    "WmsCircuitOpenError",
    "WmsEvidencePersistenceError",
    "WmsIntegrationError",
    "WmsTimeoutError",
    "WmsUnavailableError",
]
