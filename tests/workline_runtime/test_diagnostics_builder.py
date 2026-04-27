from types import SimpleNamespace

from src.workline_runtime.diagnostics import (
    ErrorCode,
    ErrorDomain,
    ProblemClass,
    build_diagnostic_card,
    build_diagnostic_context,
    build_diagnostic_event,
)


def test_build_diagnostic_card_applies_defaults() -> None:
    context = build_diagnostic_context(
        trace_id="trace-123",
        session=SimpleNamespace(id=1, trace_id="trace-123", plugin_key="smt_classifier"),
        inbox=SimpleNamespace(id=2, trace_id="trace-123"),
        workline=SimpleNamespace(id=3, line_code="WL-01", plugin_key="smt_classifier"),
    )

    event = build_diagnostic_event(
        error_code=ErrorCode.PLUGIN_TRANSITION_INVALID,
        context=context,
        message="transition invalid",
    )
    card = build_diagnostic_card(event)

    assert card.error_code == ErrorCode.PLUGIN_TRANSITION_INVALID
    assert card.problem_class == ProblemClass.SOFTWARE
    assert card.context.workline_code == "WL-01"
    assert card.next_steps


def test_build_diagnostic_card_marks_device_timeout_as_hardware() -> None:
    context = build_diagnostic_context(trace_id="trace-123")
    card = build_diagnostic_card(
        build_diagnostic_event(
            error_code=ErrorCode.DEVICE_TIMEOUT,
            context=context,
            message="device timeout",
            operator_action="检查设备网络",
        )
    )

    assert card.error_code == ErrorCode.DEVICE_TIMEOUT
    assert card.problem_class == ProblemClass.HARDWARE
    assert card.operator_action == "检查设备网络"
    assert "user_message" in card.model_dump()


def test_build_diagnostic_card_preserves_explicit_error_domain_override() -> None:
    context = build_diagnostic_context(trace_id="trace-unknown-device")
    card = build_diagnostic_card(
        build_diagnostic_event(
            error_code=ErrorCode.UNKNOWN,
            error_domain=ErrorDomain.DEVICE,
            problem_class=ProblemClass.HARDWARE,
            context=context,
            message="estop",
        )
    )

    assert card.error_code == ErrorCode.UNKNOWN
    assert card.error_domain == ErrorDomain.DEVICE
    assert card.problem_class == ProblemClass.HARDWARE
