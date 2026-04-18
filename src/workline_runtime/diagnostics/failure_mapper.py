"""FailureIntent 到诊断错误码的轻量映射。"""

from __future__ import annotations

from typing import Any

from src.workline_runtime.enums import FailureDomain

from .codes import ErrorCode, ErrorDomain


def _map_failure_to_error_code(*, failure: Any | None = None) -> ErrorCode:
    failure_domain = getattr(failure, "domain", None)
    failure_code = getattr(failure, "code", None)

    if failure_code == "CONTRACT_MISMATCH":
        return ErrorCode.CONTRACT_MISMATCH
    if failure_code == "DEVICE_TIMEOUT":
        return ErrorCode.DEVICE_TIMEOUT
    if failure_code == "DEVICE_NOT_FOUND":
        return ErrorCode.DEVICE_UNREACHABLE
    if failure_domain == FailureDomain.CONFIG.value:
        return ErrorCode.CONFIG_INVALID
    if failure_domain == FailureDomain.TIMEOUT.value:
        return ErrorCode.DEVICE_TIMEOUT
    if failure_domain == FailureDomain.HARDWARE.value:
        return ErrorCode.DEVICE_UNREACHABLE
    if failure_domain in {FailureDomain.SOFTWARE.value, FailureDomain.ORCHESTRATION.value}:
        return ErrorCode.PLUGIN_EXECUTION_FAILED
    return ErrorCode.UNKNOWN


def map_failure_to_diagnostic(
    *, failure: Any | None = None, error_code: str | None = None
) -> tuple[ErrorCode, ErrorDomain]:
    """将运行时 failure 或 orchestrator error_code 映射到统一诊断维度。"""

    if error_code and error_code in ErrorCode._value2member_map_:
        mapped = ErrorCode(error_code)
        return mapped, _domain_from_error_code(mapped)

    mapped = _map_failure_to_error_code(failure=failure)
    return mapped, _domain_from_error_code(mapped)


def _domain_from_error_code(error_code: ErrorCode) -> ErrorDomain:
    if error_code in {ErrorCode.CONTRACT_MISMATCH, ErrorCode.CONFIG_INVALID}:
        return ErrorDomain.CONFIG
    if error_code in {ErrorCode.PLUGIN_EXECUTION_FAILED, ErrorCode.PLUGIN_TRANSITION_INVALID}:
        return ErrorDomain.PLUGIN
    if error_code == ErrorCode.DEVICE_UNREACHABLE:
        return ErrorDomain.DEVICE
    if error_code == ErrorCode.DEVICE_TIMEOUT:
        return ErrorDomain.NETWORK
    if error_code == ErrorCode.CALLBACK_SCHEMA_INVALID:
        return ErrorDomain.DATA_QUALITY
    if error_code in {ErrorCode.SESSION_CONTEXT_MISSING, ErrorCode.SESSION_RESOLVE_FAILED}:
        return ErrorDomain.WORKFLOW
    if error_code == ErrorCode.OUTBOX_DISPATCH_FAILED:
        return ErrorDomain.INTEGRATION
    return ErrorDomain.SYSTEM


__all__ = ["map_failure_to_diagnostic"]
