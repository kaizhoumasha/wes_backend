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
        },
    )
    invalid = registry.validate(
        "wms_breaker.transition",
        {
            "trace_id": "trace-1",
            "provider_code": "WMS",
        },
    )

    assert valid.valid is True
    assert invalid.valid is False
    assert invalid.missing_attributes == ("operation_kind",)


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
