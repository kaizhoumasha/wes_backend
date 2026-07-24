"""EXTERNAL_HTTP 唯一 typed transport result 合同。"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum

from src.app.sys.canonical_dispatch import ExternalHttpDispatchRequest

_PROTOCOL_ERROR_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,119}$")


class ExternalHttpTransportOutcome(str, Enum):
    """请求是否确定离开本地边界以及是否得到确定响应。"""

    NOT_SENT = "NOT_SENT"
    ACCEPTED = "ACCEPTED"
    AMBIGUOUS = "AMBIGUOUS"


class ExternalHttpTransportPhase(str, Enum):
    """transport 观测到结果时所处的阶段。"""

    PREPARING = "PREPARING"
    CONNECTING = "CONNECTING"
    SENDING = "SENDING"
    AWAITING_RESPONSE = "AWAITING_RESPONSE"
    RESPONSE_RECEIVED = "RESPONSE_RECEIVED"
    SANDBOX = "SANDBOX"


class ExternalHttpProtocolResult(str, Enum):
    """远端 HTTP/协议层对已送达请求的明确结论。"""

    NOT_AVAILABLE = "NOT_AVAILABLE"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ExternalHttpTransportResult:
    """一次 EXTERNAL_HTTP transport attempt 的冻结结果。"""

    outcome: ExternalHttpTransportOutcome
    phase: ExternalHttpTransportPhase
    protocol_result: ExternalHttpProtocolResult
    safe_to_retry: bool
    http_status_code: int | None = None
    protocol_error_code: str | None = None
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        if self.safe_to_retry and self.outcome is not ExternalHttpTransportOutcome.NOT_SENT:
            raise ValueError("safe_to_retry is only valid for NOT_SENT")
        if self.outcome is ExternalHttpTransportOutcome.NOT_SENT:
            if self.http_status_code is not None:
                raise ValueError("NOT_SENT cannot carry http_status_code")
            if self.protocol_result is not ExternalHttpProtocolResult.NOT_AVAILABLE:
                raise ValueError("NOT_SENT requires NOT_AVAILABLE protocol_result")
            if self.phase is ExternalHttpTransportPhase.RESPONSE_RECEIVED:
                raise ValueError("NOT_SENT cannot occur after RESPONSE_RECEIVED")
        if self.outcome is ExternalHttpTransportOutcome.ACCEPTED:
            if self.phase is ExternalHttpTransportPhase.RESPONSE_RECEIVED:
                if self.protocol_result not in {
                    ExternalHttpProtocolResult.ACCEPTED,
                    ExternalHttpProtocolResult.REJECTED,
                }:
                    raise ValueError("HTTP ACCEPTED requires explicit protocol result")
                if self.http_status_code is None:
                    raise ValueError("HTTP ACCEPTED requires http_status_code")
            elif self.phase is ExternalHttpTransportPhase.SANDBOX:
                if self.protocol_result is not ExternalHttpProtocolResult.NOT_AVAILABLE:
                    raise ValueError("sandbox ACCEPTED requires NOT_AVAILABLE protocol_result")
                if self.http_status_code is not None:
                    raise ValueError("sandbox ACCEPTED cannot carry http_status_code")
            else:
                raise ValueError("ACCEPTED requires RESPONSE_RECEIVED or SANDBOX phase")
        if self.outcome is ExternalHttpTransportOutcome.AMBIGUOUS:
            self._validate_ambiguous_evidence()
        if self.http_status_code is not None and not 100 <= self.http_status_code <= 599:
            raise ValueError("http_status_code must be between 100 and 599")
        if self.protocol_error_code is not None:
            if self.phase is not ExternalHttpTransportPhase.RESPONSE_RECEIVED:
                raise ValueError("protocol_error_code requires RESPONSE_RECEIVED")
            if not _PROTOCOL_ERROR_CODE_RE.fullmatch(self.protocol_error_code):
                raise ValueError("protocol_error_code must be a bounded stable code")

    def _validate_ambiguous_evidence(self) -> None:
        if self.phase is ExternalHttpTransportPhase.SANDBOX:
            raise ValueError("SANDBOX cannot produce AMBIGUOUS transport result")
        if self.phase is ExternalHttpTransportPhase.RESPONSE_RECEIVED:
            if self.protocol_result is not ExternalHttpProtocolResult.UNKNOWN:
                raise ValueError("RESPONSE_RECEIVED AMBIGUOUS requires UNKNOWN protocol_result")
            if self.http_status_code is None:
                raise ValueError("RESPONSE_RECEIVED AMBIGUOUS requires http_status_code")
            return
        if self.protocol_result is not ExternalHttpProtocolResult.NOT_AVAILABLE:
            raise ValueError("pre-response AMBIGUOUS requires NOT_AVAILABLE protocol_result")
        if self.http_status_code is not None:
            raise ValueError("pre-response AMBIGUOUS cannot carry http_status_code")

    @classmethod
    def not_sent(
        cls,
        *,
        phase: ExternalHttpTransportPhase,
        safe_to_retry: bool,
        error_code: str,
        error_message: str | None = None,
    ) -> ExternalHttpTransportResult:
        return cls(
            outcome=ExternalHttpTransportOutcome.NOT_SENT,
            phase=phase,
            protocol_result=ExternalHttpProtocolResult.NOT_AVAILABLE,
            safe_to_retry=safe_to_retry,
            error_code=error_code,
            error_message=error_message,
        )

    @classmethod
    def accepted(
        cls,
        *,
        http_status_code: int,
        protocol_result: ExternalHttpProtocolResult,
        protocol_error_code: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> ExternalHttpTransportResult:
        return cls(
            outcome=ExternalHttpTransportOutcome.ACCEPTED,
            phase=ExternalHttpTransportPhase.RESPONSE_RECEIVED,
            protocol_result=protocol_result,
            safe_to_retry=False,
            http_status_code=http_status_code,
            protocol_error_code=protocol_error_code,
            error_code=error_code,
            error_message=error_message,
        )

    @classmethod
    def ambiguous(
        cls,
        *,
        phase: ExternalHttpTransportPhase,
        protocol_result: ExternalHttpProtocolResult = ExternalHttpProtocolResult.NOT_AVAILABLE,
        http_status_code: int | None = None,
        protocol_error_code: str | None = None,
        error_code: str,
        error_message: str | None = None,
    ) -> ExternalHttpTransportResult:
        return cls(
            outcome=ExternalHttpTransportOutcome.AMBIGUOUS,
            phase=phase,
            protocol_result=protocol_result,
            safe_to_retry=False,
            http_status_code=http_status_code,
            protocol_error_code=protocol_error_code,
            error_code=error_code,
            error_message=error_message,
        )

    @classmethod
    def sandbox_accepted(cls) -> ExternalHttpTransportResult:
        """表示请求由沙箱出口接管，但没有伪造远端 HTTP 响应。"""

        return cls(
            outcome=ExternalHttpTransportOutcome.ACCEPTED,
            phase=ExternalHttpTransportPhase.SANDBOX,
            protocol_result=ExternalHttpProtocolResult.NOT_AVAILABLE,
            safe_to_retry=False,
        )

    def evidence_json(self) -> dict[str, object]:
        """生成可落库的低敏 transport evidence。"""

        return {
            "transport_outcome": self.outcome.value,
            "transport_phase": self.phase.value,
            "protocol_result": self.protocol_result.value,
            "safe_to_retry": self.safe_to_retry,
            "http_status_code": self.http_status_code,
            "protocol_error_code": self.protocol_error_code,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


ExternalHttpSender = Callable[[ExternalHttpDispatchRequest], Awaitable[ExternalHttpTransportResult]]


__all__ = [
    "ExternalHttpProtocolResult",
    "ExternalHttpSender",
    "ExternalHttpTransportOutcome",
    "ExternalHttpTransportPhase",
    "ExternalHttpTransportResult",
]
