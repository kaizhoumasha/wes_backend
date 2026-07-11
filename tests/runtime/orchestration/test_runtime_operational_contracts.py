"""Runtime observability, toggle and replay operational contracts."""

from __future__ import annotations

from datetime import date


def test_runtime_inbox_sli_signals_require_stable_attributes() -> None:
    from src.app.runtime.orchestration.observability import RuntimeObservabilityRegistry

    registry = RuntimeObservabilityRegistry()
    signals = {
        "runtime_inbox.claim_batch": {"claimed_count": 2, "duration_ms": 1.2},
        "runtime_inbox.processing": {"inbox_id": 1, "duration_ms": 3.4, "outcome": "success"},
        "runtime_inbox.lease_reclaim": {"reclaimed_count": 1},
        "runtime_inbox.fencing_reject": {"inbox_id": 1, "target_state": "PROCESSED"},
        "runtime_inbox.resource_wait": {"inbox_id": 1},
        "runtime_inbox.dead_letter": {"inbox_id": 1},
    }

    for name, attributes in signals.items():
        assert registry.validate(name, attributes).valid is True

    invalid = registry.validate("runtime_inbox.processing", {"inbox_id": 1, "duration_ms": 3.4})
    assert invalid.valid is False
    assert invalid.missing_attributes == ("outcome",)


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
            "ack_age_ms": 123,
        },
    )
    invalid_ack = registry.validate(
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
    dispatch_policy = registry.validate(
        "device_command.dispatch_policy",
        {
            "trace_id": "trace-1",
            "correlation_id": "corr-1",
            "command_code": "CMD-1",
            "device_code": "DEV-1",
            "provider_code": "ECS",
            "policy_decision": "WAIT_FOR_IDLE",
            "reason": "DEVICE_BUSY",
            "dispatch_allowed": False,
            "runtime_hold_required": False,
        },
    )

    assert valid.valid is True
    assert dispatch_policy.valid is True
    assert invalid_ack.valid is False
    assert invalid_ack.missing_attributes == ("ack_age_ms",)
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

    evidence_failure = registry.validate(
        "wms_evidence.persistence_failure",
        {
            "trace_id": "trace-1",
            "provider_code": "WMS",
            "operation_kind": "reserve_inventory",
            "evidence_key": "ev:reserve_inventory:REQ-1",
            "reason_code": "WMS_EVIDENCE_PERSISTENCE_FAILED",
        },
    )

    assert evidence_failure.valid is True


def test_runtime_observability_open_telemetry_bridge_exports_signal_kinds() -> None:
    from src.app.runtime.orchestration.observability import (
        RuntimeObservabilityRegistry,
        RuntimeOpenTelemetryBridge,
    )

    class RecordingExporter:
        def __init__(self) -> None:
            self.calls = []

        def emit_span(self, name, attributes) -> None:
            self.calls.append(("span", name, dict(attributes)))

        def emit_metric(self, name, attributes) -> None:
            self.calls.append(("metric", name, dict(attributes)))

        def emit_log_event(self, name, attributes) -> None:
            self.calls.append(("log", name, dict(attributes)))

    exporter = RecordingExporter()
    bridge = RuntimeOpenTelemetryBridge(exporter)
    registry = RuntimeObservabilityRegistry(observers=(bridge,))

    registry.emit(
        "callback.normalize",
        {
            "trace_id": "trace-1",
            "correlation_id": "corr-1",
            "provider_code": "WMS",
            "source_event_id": "evt-1",
        },
    )

    assert [call[0] for call in exporter.calls] == ["span", "metric", "log"]
    assert {call[1] for call in exporter.calls} == {"callback.normalize"}
    assert all(call[2]["trace_id"] == "trace-1" for call in exporter.calls)


def test_runtime_open_telemetry_http_exporter_posts_stable_backend_payloads() -> None:
    from src.app.runtime.orchestration.observability import RuntimeOpenTelemetryHttpExporter

    posts = []

    def post_json(endpoint, payload, headers, timeout_seconds) -> None:
        posts.append((endpoint, dict(payload), dict(headers), timeout_seconds))

    exporter = RuntimeOpenTelemetryHttpExporter(
        endpoint="https://otel-collector.example/runtime",
        service_name="wes-backend",
        environment="prod",
        headers={"x-tenant": "wes"},
        timeout_seconds=0.25,
        post_json=post_json,
    )

    exporter.emit_span("callback.normalize", {"trace_id": "trace-1", "provider_code": "ECS"})

    assert posts == [
        (
            "https://otel-collector.example/runtime",
            {
                "service_name": "wes-backend",
                "environment": "prod",
                "signal_kind": "span",
                "name": "callback.normalize",
                "attributes": {"trace_id": "trace-1", "provider_code": "ECS"},
            },
            {"content-type": "application/json", "x-tenant": "wes"},
            0.25,
        )
    ]


def test_configure_runtime_open_telemetry_backend_registers_named_observer_once() -> None:
    from src.app.runtime.orchestration.observability import (
        RuntimeObservabilityRegistry,
        configure_runtime_open_telemetry_backend,
    )

    posts = []

    def post_json(endpoint, payload, headers, timeout_seconds) -> None:
        posts.append((endpoint, payload["signal_kind"], payload["name"]))

    registry = RuntimeObservabilityRegistry()

    configured = configure_runtime_open_telemetry_backend(
        registry=registry,
        enabled=True,
        endpoint="https://otel-collector.example/runtime",
        service_name="wes-backend",
        environment="prod",
        post_json=post_json,
    )
    configured_again = configure_runtime_open_telemetry_backend(
        registry=registry,
        enabled=True,
        endpoint="https://otel-collector.example/runtime",
        service_name="wes-backend",
        environment="prod",
        post_json=post_json,
    )

    registry.emit(
        "callback.normalize",
        {
            "trace_id": "trace-1",
            "correlation_id": "corr-1",
            "provider_code": "ECS",
            "source_event_id": "evt-1",
        },
    )

    assert configured is True
    assert configured_again is True
    assert posts == [
        ("https://otel-collector.example/runtime", "span", "callback.normalize"),
        ("https://otel-collector.example/runtime", "metric", "callback.normalize"),
        ("https://otel-collector.example/runtime", "log", "callback.normalize"),
    ]


def test_runtime_toggle_registry_blocks_expired_and_security_bypass_toggles() -> None:
    from src.core.runtime_toggles import RuntimeToggleDefinition, RuntimeToggleKind, RuntimeToggleRegistry

    expired = RuntimeToggleRegistry(
        [
            RuntimeToggleDefinition(
                name="runtime.old-provider-path",
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
                name="runtime.skip-hmac",
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
        name="runtime.provider-v2-path",
        kind=RuntimeToggleKind.RELEASE,
        owner="runtime",
        expiry=date(2026, 7, 31),
        scope="provider=WMS",
        default=False,
        rollback="disable toggle",
        test_matrix=("provider-contract", "rollback-drill"),
    )
    default_on = RuntimeToggleDefinition(
        name="runtime.default-on-path",
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
    assert matrix_decision.toggle_name == "runtime.provider-v2-path"
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

    recording = recorder.record(scenario_id="runtime-production", events=events)
    first = ScenarioReplayRunner().replay(recording)
    second = ScenarioReplayRunner().replay(recording)

    assert recording.events[0].event_id == "evt-1"
    assert recording.events[0].payload["pkg_code"] == "***0001"
    assert recording.events[0].payload["bin_code"] == "BIN***"
    assert first == second
    assert first.outbox_effect_keys == ("device-command:CMD-1",)
    assert first.reconciliation_reasons == ("late_callback",)
