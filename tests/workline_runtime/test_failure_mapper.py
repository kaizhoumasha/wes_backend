from types import SimpleNamespace

from src.workline_runtime.diagnostics import ErrorCode, ErrorDomain, map_failure_to_diagnostic
from src.workline_runtime.enums import FailureDomain as RuntimeFailureDomain


def test_map_failure_to_diagnostic_prefers_known_error_code() -> None:
    error_code, error_domain = map_failure_to_diagnostic(error_code=ErrorCode.PLUGIN_TRANSITION_INVALID.value)

    assert error_code == ErrorCode.PLUGIN_TRANSITION_INVALID
    assert error_domain == ErrorDomain.PLUGIN


def test_map_failure_to_diagnostic_uses_failure_code_and_domain() -> None:
    failure = SimpleNamespace(domain=RuntimeFailureDomain.TIMEOUT.value, code="DEVICE_TIMEOUT")
    error_code, error_domain = map_failure_to_diagnostic(failure=failure)

    assert error_code == ErrorCode.DEVICE_TIMEOUT
    assert error_domain == ErrorDomain.NETWORK


def test_map_failure_to_diagnostic_handles_hardware_failures() -> None:
    failure = SimpleNamespace(domain=RuntimeFailureDomain.HARDWARE.value, code="MOTOR_OFFLINE")
    error_code, error_domain = map_failure_to_diagnostic(failure=failure)

    assert error_code == ErrorCode.DEVICE_UNREACHABLE
    assert error_domain == ErrorDomain.DEVICE


def test_map_failure_to_diagnostic_falls_back_to_unknown() -> None:
    error_code, error_domain = map_failure_to_diagnostic(failure=SimpleNamespace(domain="OTHER", code="X"))

    assert error_code == ErrorCode.UNKNOWN
    assert error_domain == ErrorDomain.SYSTEM
