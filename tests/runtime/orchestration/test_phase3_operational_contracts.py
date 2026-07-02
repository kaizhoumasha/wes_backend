"""Phase 3 observability, toggle and replay operational contracts."""

from __future__ import annotations

from datetime import date


def test_runtime_observability_registry_requires_stable_attributes() -> None:
    from src.app.runtime.orchestration.observability import RuntimeObservabilityRegistry

    registry = RuntimeObservabilityRegistry()

    valid = registry.validate(
        "device_command.ack",
        {
            "trace_id": "trace-1",
            "correlation_id": "corr-1",
            "command_code": "CMD-1",
            "provider_code": "ECS",
        },
    )
    invalid = registry.validate(
        "wms_breaker.transition",
        {
            "trace_id": "trace-1",
            "provider_code": "WMS",
            "breaker_state": "OPEN",
        },
    )

    assert valid.valid is True
    assert invalid.valid is False
    assert invalid.missing_attributes == ("operation_kind",)


def test_runtime_observability_registry_emits_valid_events_to_observers() -> None:
    from src.app.runtime.orchestration.observability import RuntimeObservabilityRegistry

    emitted = []
    registry = RuntimeObservabilityRegistry(observers=(emitted.append,))

    event = registry.emit(
        "wms_breaker.transition",
        {
            "trace_id": "trace-1",
            "provider_code": "WMS",
            "operation_kind": "query_inventory",
            "breaker_state": "OPEN",
        },
    )

    assert emitted == [event]
    assert event.name == "wms_breaker.transition"
    assert event.signal_type == "metric+log"
    assert event.attributes["breaker_state"] == "OPEN"


def test_runtime_toggle_registry_blocks_expired_and_security_bypass_toggles() -> None:
    from src.core.runtime_toggles import RuntimeToggleDefinition, RuntimeToggleKind, RuntimeToggleRegistry

    expired = RuntimeToggleRegistry(
        [
            RuntimeToggleDefinition(
                name="phase3.old-provider-path",
                kind=RuntimeToggleKind.RELEASE,
                owner="runtime",
                expiry=date(2026, 7, 1),
                scope="provider=WMS",
                default=False,
                rollback="disable toggle",
                test_matrix=("provider-contract",),
            )
        ]
    )
    bypass = RuntimeToggleRegistry(
        [
            RuntimeToggleDefinition(
                name="phase3.skip-hmac",
                kind=RuntimeToggleKind.OPS,
                owner="runtime",
                expiry=date(2026, 7, 31),
                scope="callback",
                default=False,
                rollback="disable toggle",
                test_matrix=("security",),
                protected_capabilities=frozenset({"hmac"}),
            )
        ]
    )

    assert expired.validate(today=date(2026, 7, 2)).reason == "TOGGLE_EXPIRED"
    assert bypass.validate(today=date(2026, 7, 2)).reason == "PROTECTED_CAPABILITY_BYPASS"


def test_runtime_toggle_release_gate_blocks_default_on_and_unverified_matrix() -> None:
    """发布门禁必须把 typed toggle 治理结果转成 release blocker。"""

    import pytest

    from src.core.runtime_toggle_release_gate import RuntimeToggleReleaseBlocked, RuntimeToggleReleaseGate
    from src.core.runtime_toggles import RuntimeToggleDefinition, RuntimeToggleKind

    unverified = RuntimeToggleDefinition(
        name="phase3.provider-v2-path",
        kind=RuntimeToggleKind.RELEASE,
        owner="runtime",
        expiry=date(2026, 7, 31),
        scope="provider=WMS",
        default=False,
        rollback="disable toggle",
        test_matrix=("provider-contract", "rollback-drill"),
    )
    default_on = RuntimeToggleDefinition(
        name="phase3.default-on-path",
        kind=RuntimeToggleKind.RELEASE,
        owner="runtime",
        expiry=date(2026, 7, 31),
        scope="provider=WMS",
        default=True,
        rollback="disable toggle",
        test_matrix=("provider-contract",),
    )

    matrix_decision = RuntimeToggleReleaseGate([unverified]).evaluate(
        today=date(2026, 7, 2),
        passed_checks=frozenset({"provider-contract"}),
    )
    default_decision = RuntimeToggleReleaseGate([default_on]).evaluate(
        today=date(2026, 7, 2),
        passed_checks=frozenset({"provider-contract"}),
    )

    assert matrix_decision.ready is False
    assert matrix_decision.reason == "TOGGLE_TEST_MATRIX_NOT_VERIFIED"
    assert matrix_decision.toggle_name == "phase3.provider-v2-path"
    assert matrix_decision.missing_checks == ("rollback-drill",)
    assert default_decision.ready is False
    assert default_decision.reason == "RELEASE_TOGGLE_DEFAULT_ON"

    with pytest.raises(RuntimeToggleReleaseBlocked):
        RuntimeToggleReleaseGate([unverified]).assert_release_ready(
            today=date(2026, 7, 2),
            passed_checks=frozenset({"provider-contract"}),
        )


def test_scenario_recorder_and_replay_are_deterministic_and_sanitized() -> None:
    from src.app.runtime.orchestration.scenario_replay import (
        ScenarioEvent,
        ScenarioRecorder,
        ScenarioReplayRunner,
    )

    recorder = ScenarioRecorder()
    events = [
        ScenarioEvent(
            event_id="evt-2",
            kind="runtime_conflict",
            occurred_at="2026-07-02T10:00:02Z",
            payload={"object_key": "bin:BIN-001", "state": "RECONCILING", "reason": "late_callback"},
        ),
        ScenarioEvent(
            event_id="evt-1",
            kind="device_command",
            occurred_at="2026-07-02T10:00:01Z",
            payload={
                "effect_key": "device-command:CMD-1",
                "object_key": "pkg:PKG-0001",
                "state": "IN_FLIGHT",
                "pkg_code": "PKG-0001",
                "bin_code": "BIN-001",
            },
        ),
    ]

    recording = recorder.record(scenario_id="phase3-p0", events=events)
    first = ScenarioReplayRunner().replay(recording)
    second = ScenarioReplayRunner().replay(recording)

    assert recording.events[0].event_id == "evt-1"
    assert recording.events[0].payload["pkg_code"] == "***0001"
    assert recording.events[0].payload["bin_code"] == "BIN***"
    assert first == second
    assert first.outbox_effect_keys == ("device-command:CMD-1",)
    assert first.reconciliation_reasons == ("late_callback",)
