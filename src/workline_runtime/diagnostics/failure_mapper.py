"""运行时失败对象到诊断错误码的轻量映射。"""

from __future__ import annotations

from typing import Any

from src.workline_runtime.enums import FailureDomain

from .codes import ErrorCode, ErrorDomain, error_domain_for


def _map_failure_to_error_code(*, failure: Any | None = None) -> ErrorCode:
    failure_domain = getattr(failure, "domain", None)
    failure_code = getattr(failure, "code", None)

    if failure_code == "CONTRACT_MISMATCH":
        return ErrorCode.CONTRACT_MISMATCH
    if failure_code == "DEVICE_TIMEOUT":
        return ErrorCode.DEVICE_TIMEOUT
    if failure_code == "DEVICE_NOT_FOUND":
        return ErrorCode.DEVICE_UNREACHABLE
    if failure_code == "STATE_MISMATCH":
        return ErrorCode.PLUGIN_TRANSITION_INVALID
    if failure_domain == FailureDomain.CONFIG.value:
        return ErrorCode.CONFIG_INVALID
    if failure_domain == FailureDomain.TIMEOUT.value:
        return ErrorCode.DEVICE_TIMEOUT
    if failure_domain in {FailureDomain.SOFTWARE.value, FailureDomain.ORCHESTRATION.value}:
        return ErrorCode.PLUGIN_EXECUTION_FAILED
    return ErrorCode.UNKNOWN


def _domain_from_failure(failure: Any | None) -> ErrorDomain:
    failure_domain = getattr(failure, "domain", None)
    if failure_domain == FailureDomain.HARDWARE.value:
        return ErrorDomain.DEVICE
    if failure_domain == FailureDomain.TIMEOUT.value:
        return ErrorDomain.NETWORK
    if failure_domain in {FailureDomain.SOFTWARE.value, FailureDomain.ORCHESTRATION.value}:
        return ErrorDomain.PLUGIN
    if failure_domain == FailureDomain.CONFIG.value:
        return ErrorDomain.CONFIG
    if failure_domain == FailureDomain.DATA.value:
        return ErrorDomain.DATA_QUALITY
    return ErrorDomain.SYSTEM


def map_failure_to_diagnostic(
    *, failure: Any | None = None, error_code: str | None = None
) -> tuple[ErrorCode, ErrorDomain]:
    """将运行时 failure 或 orchestrator error_code 映射到统一诊断维度。"""

    if error_code and error_code in ErrorCode._value2member_map_:
        mapped = ErrorCode(error_code)
        return mapped, error_domain_for(mapped)

    mapped = _map_failure_to_error_code(failure=failure)
    if mapped == ErrorCode.UNKNOWN:
        return mapped, _domain_from_failure(failure)
    return mapped, error_domain_for(mapped)


__all__ = ["map_failure_to_diagnostic"]
