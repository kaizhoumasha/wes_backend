"""Phase 3 WMS fulfillment state machine contract tests."""

from __future__ import annotations

from src.utils.timezone import timezone


def test_fulfillment_state_set_matches_phase3_contract() -> None:
    """Phase 3 外部履约必须是文档约定的 11 态, 不引入额外终态。"""

    from src.app.wms_integration.state_machine import FulfillmentState

    assert {state.value for state in FulfillmentState} == {
        "REQUESTED",
        "SENT",
        "ACCEPTED",
        "RUNNING",
        "SUCCEEDED",
        "REJECTED",
        "FAILED",
        "TIMEOUT",
        "CANCELLED",
        "BLOCKED_BY_CB",
        "RECONCILING",
    }


def test_provider_rejected_is_terminal_business_reject() -> None:
    """WMS/RCS 业务拒绝必须进入 REJECTED, 不与 FAILED/TIMEOUT 混淆。"""

    from src.app.wms_integration.state_machine import (
        FulfillmentEvent,
        FulfillmentState,
        WmsFulfillmentStateMachine,
    )

    machine = WmsFulfillmentStateMachine()

    result = machine.transition(
        current=FulfillmentState.SENT,
        event=FulfillmentEvent.PROVIDER_REJECTED,
        now=timezone.now_for_db(),
    )

    assert result.state == FulfillmentState.REJECTED
    assert result.reason == "PROVIDER_REJECTED"


def test_open_circuit_breaker_blocks_outbound_request_without_consuming_sent_quota() -> None:
    """CB open 期间新履约请求进入 BLOCKED_BY_CB, 不进入 SENT。"""

    from src.app.wms_integration.state_machine import (
        FulfillmentEvent,
        FulfillmentState,
        WmsFulfillmentStateMachine,
    )

    machine = WmsFulfillmentStateMachine()

    result = machine.transition(
        current=FulfillmentState.REQUESTED,
        event=FulfillmentEvent.CIRCUIT_BREAKER_OPEN,
        now=timezone.now_for_db(),
    )

    assert result.state == FulfillmentState.BLOCKED_BY_CB
    assert result.counts_as_sent is False
    assert result.should_dispatch_effect is False


def test_circuit_breaker_open_does_not_overwrite_in_flight_fulfillment() -> None:
    """CB open 只阻断新出站 effect, 不覆盖已经 SENT/RUNNING 的履约。"""

    from src.app.wms_integration.state_machine import (
        FulfillmentEvent,
        FulfillmentState,
        WmsFulfillmentStateMachine,
    )

    machine = WmsFulfillmentStateMachine()

    for current in (FulfillmentState.SENT, FulfillmentState.RUNNING):
        result = machine.transition(
            current=current,
            event=FulfillmentEvent.CIRCUIT_BREAKER_OPEN,
            now=timezone.now_for_db(),
        )

        assert result.state == current
        assert result.reason == "CIRCUIT_BREAKER_OPEN_OUTBOUND_ONLY"


def test_four_timeout_paths_enter_timeout_with_distinct_reasons() -> None:
    """Phase 3 四类 timeout 必须可观测, 不能混成同一个黑盒超时。"""

    from src.app.wms_integration.state_machine import (
        FulfillmentEvent,
        FulfillmentState,
        WmsFulfillmentStateMachine,
    )

    machine = WmsFulfillmentStateMachine()
    scenarios = [
        (FulfillmentState.REQUESTED, FulfillmentEvent.REQUEST_DISPATCH_TIMEOUT),
        (FulfillmentState.SENT, FulfillmentEvent.SENT_ACK_TIMEOUT),
        (FulfillmentState.ACCEPTED, FulfillmentEvent.ACCEPTED_RUNNING_TIMEOUT),
        (FulfillmentState.RUNNING, FulfillmentEvent.RUNNING_RESULT_TIMEOUT),
    ]

    for current, event in scenarios:
        result = machine.transition(current=current, event=event, now=timezone.now_for_db())

        assert result.state == FulfillmentState.TIMEOUT
        assert result.reason == event.value


def test_late_callback_during_blocked_by_cb_is_inboxed_not_marked_blocked() -> None:
    """CB open/half-open 只阻断出站 effect, late callback 仍进入 RuntimeInbox。"""

    from src.app.wms_integration.state_machine import (
        FulfillmentEvent,
        FulfillmentState,
        WmsFulfillmentStateMachine,
    )

    machine = WmsFulfillmentStateMachine()

    result = machine.transition(
        current=FulfillmentState.BLOCKED_BY_CB,
        event=FulfillmentEvent.CALLBACK_SUCCEEDED,
        now=timezone.now_for_db(),
    )

    assert result.state == FulfillmentState.RECONCILING
    assert result.runtime_inbox_required is True
    assert result.reason == "LATE_CALLBACK_WHILE_CB_BLOCKED"


def test_terminal_fulfillment_state_cannot_be_overwritten_by_late_events() -> None:
    """终态履约不能被 provider 重试或乱序 callback 覆盖。"""

    from src.app.wms_integration.state_machine import (
        FulfillmentEvent,
        FulfillmentState,
        WmsFulfillmentStateMachine,
    )

    machine = WmsFulfillmentStateMachine()
    now = timezone.now_for_db()

    scenarios = [
        (FulfillmentState.SUCCEEDED, FulfillmentEvent.CALLBACK_FAILED, True),
        (FulfillmentState.SUCCEEDED, FulfillmentEvent.PROVIDER_REJECTED, False),
        (FulfillmentState.REJECTED, FulfillmentEvent.CALLBACK_SUCCEEDED, True),
        (FulfillmentState.FAILED, FulfillmentEvent.PROVIDER_RUNNING, False),
        (FulfillmentState.TIMEOUT, FulfillmentEvent.CALLBACK_SUCCEEDED, True),
        (FulfillmentState.CANCELLED, FulfillmentEvent.DISPATCH_SENT, False),
    ]
    for current, event, expected_runtime_inbox_required in scenarios:
        result = machine.transition(current=current, event=event, now=now)

        assert result.state == current
        assert result.reason == "TERMINAL_STATE_IGNORED"
        assert result.runtime_inbox_required is expected_runtime_inbox_required
