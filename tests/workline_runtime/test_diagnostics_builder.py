from types import SimpleNamespace

from src.workline_runtime.diagnostics import (
    ErrorCode,
    OwnerRole,
    build_diagnostic_card,
    build_diagnostic_context,
    build_diagnostic_event,
    project_card_for_role,
)


def test_build_diagnostic_card_applies_defaults() -> None:
    context = build_diagnostic_context(
        correlation_id="corr-123",
        session=SimpleNamespace(id=1, correlation_id="corr-123", plugin_key="smt_classifier"),
        inbox=SimpleNamespace(id=2, correlation_id="corr-123"),
        workline=SimpleNamespace(id=3, line_code="WL-01", plugin_key="smt_classifier"),
    )

    event = build_diagnostic_event(
        error_code=ErrorCode.PLUGIN_TRANSITION_INVALID,
        context=context,
        message="transition invalid",
    )
    card = build_diagnostic_card(event)

    assert card.error_code == ErrorCode.PLUGIN_TRANSITION_INVALID
    assert card.owner_role == OwnerRole.PLUGIN_DEVELOPER
    assert card.context.workline_code == "WL-01"
    assert card.next_steps


def test_project_card_for_user_is_simplified() -> None:
    context = build_diagnostic_context(correlation_id="corr-123")
    card = build_diagnostic_card(
        build_diagnostic_event(
            error_code=ErrorCode.DEVICE_TIMEOUT,
            context=context,
            message="device timeout",
            operator_action="检查设备网络",
        )
    )

    projected = project_card_for_role(card, OwnerRole.USER)

    assert projected["error_code"] == ErrorCode.DEVICE_TIMEOUT.value
    assert projected["operator_action"] == "检查设备网络"
    assert "user_message" in projected
