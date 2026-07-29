"""平台 Plugin RuntimeInbox canonical routing 与 dispatcher 接线。"""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.runtime.extension_identity import sha256_digest
from src.app.runtime.orchestration.material_fact_version import (
    material_unit_fact_version as _material_unit_fact_version,
)
from src.app.runtime.orchestration.runtime_intent import BlockScope, RuntimeIntent, RuntimeIntentKind
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge import (
    GeneratedPluginAttemptRunner,
    RuntimeInboxProcessorBridge,
    _build_plugin_dispatch_request,
    _canonical_plugin_input,
    _replay_digest_matches_source,
    _system_capability_intents,
    _write_set_from_recorded_replay,
)
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_writeback_service import (
    _authoritative_snapshot_matches,
)
from src.app.runtime.system_capabilities.evidence import QueryEvidence
from src.app.runtime.system_capabilities.gateway import GatewayQueryResult
from src.app.runtime.system_capabilities.outcomes import ContractViolation, Success
from src.app.runtime.workline_plugins.attempt_coordinator import AttemptSnapshot, PluginAttemptContext
from src.app.runtime.workline_plugins.contracts import PluginDecision
from src.app.runtime.workline_plugins.dispatcher import PluginDispatchRequest
from src.app.runtime.workline_plugins.generated_index import WORKLINE_PLUGIN_INDEX_DIGEST
from src.app.runtime.workline_plugins.rough_sorter.definition import DEFINITION as ROUGH_SORTER_DEFINITION
from src.app.runtime.workline_plugins.rough_sorter.state import RoughSorterState

_Q19_ADMISSION_CONTEXT = {
    "wms_admission_decision": {
        "request_canonical_hash": "a" * 64,
        "decision": "ADMIT",
        "grn_id": "GRN-001",
        "po_number": "PO-001",
        "po_item": "10",
        "material_code": "MAT-001",
        "pkg_id": "PKG-1",
        "measurement_decision": "PASS",
        "standard_reel_diameter_mm": "180",
        "reel_diameter_tolerance_mm": "1",
        "standard_reel_thickness_mm": "16",
        "reel_thickness_tolerance_mm": "0.5",
        "rule_version": "rule-q19",
        "source_version": "source-q19",
        "evidence_reference": "query:q19:routing",
    }
}


@pytest.mark.parametrize(
    ("kind", "event_type", "payload", "expected_route", "expected"),
    [
        (
            "DEVICE_EVENT",
            "SCAN_COMPLETED",
            {"event_type": "PROVIDER_SCAN", "data": {"PkgID": "PKG-1"}},
            "SCAN_COMPLETED",
            {"event_type": "PROVIDER_SCAN", "data": {"PkgID": "PKG-1"}},
        ),
        (
            "COMMAND_RESULT",
            "COMMAND_RESULT",
            {
                "command_code": "CMD-1",
                "command_type": "PICK_AND_PUT",
                "result": "SUCCESS",
                "data": {"diameter_mm": "10"},
            },
            "COMMAND_RESULT",
            {
                "route": "COMMAND_RESULT",
                "command_code": "CMD-1",
                "result": "SUCCESS",
                "data": {"diameter_mm": "10"},
                "error_detail": {},
            },
        ),
        (
            "TIMER_TIMEOUT",
            "TIMER_TIMEOUT",
            {
                "logical_route": "BUSINESS_TIMEOUT",
                "input": {
                    "route": "BUSINESS_TIMEOUT",
                    "command_code": "CMD-1",
                    "wait_type": "COMMAND_RESULT",
                },
            },
            "BUSINESS_TIMEOUT",
            {"route": "BUSINESS_TIMEOUT", "command_code": "CMD-1", "wait_type": "COMMAND_RESULT"},
        ),
        (
            "REPLAY_REQUEST",
            "REPLAY_REQUEST",
            {"idempotency_key": "replay-1", "payload_digest": "a" * 64},
            "REPLAY_REQUEST",
            {"route": "REPLAY_REQUEST", "idempotency_key": "replay-1", "payload_digest": "a" * 64},
        ),
    ],
)
def test_canonical_plugin_input_maps_transport_to_declared_logical_route(
    kind: str,
    event_type: str,
    payload: dict[str, object],
    expected_route: str,
    expected: dict[str, object],
) -> None:
    route, raw_input = _canonical_plugin_input(
        SimpleNamespace(kind=kind, event_type=event_type, payload_json=payload),
    )

    assert route == expected_route
    assert raw_input == expected
    assert route != "SYSTEM_CAPABILITY_RESULT"


def test_canonical_plugin_input_rejects_uncorrelated_command_result() -> None:
    with pytest.raises(ValueError, match="command correlation is required"):
        _canonical_plugin_input(
            SimpleNamespace(
                kind="COMMAND_RESULT",
                event_type="COMMAND_RESULT",
                payload_json={"command_type": "PICK_AND_PUT", "result": "SUCCESS"},
            )
        )


def test_canonical_plugin_input_rejects_missing_logical_route() -> None:
    with pytest.raises(ValueError, match="plugin logical route is required"):
        _canonical_plugin_input(
            SimpleNamespace(
                kind="INTERNAL_EVENT",
                event_type="",
                payload_json={},
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, True),
        ({"idempotency_key": "other-key"}, False),
        ({"workline_id": 99}, False),
        ({"workline_session_id": None}, False),
        ({"execution_session_id": 88}, False),
        ({"correlation_id": "other-correlation"}, False),
    ],
)
async def test_logical_replay_requires_same_identity_digest_and_runtime_anchors(
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, object],
    expected: bool,
) -> None:
    source = SimpleNamespace(
        id=7,
        payload_json={"command_code": "CMD-7", "result": "SUCCESS"},
        payload_hash="a" * 64,
        workline_id=20,
        workline_session_id=10,
        execution_session_id=30,
        correlation_id="correlation-1",
    )
    replay = SimpleNamespace(
        id=8,
        causation_id="inbox:7",
        workline_id=20,
        workline_session_id=10,
        execution_session_id=30,
        correlation_id="correlation-1",
    )
    raw_input: dict[str, object] = {"idempotency_key": "source-key", "payload_digest": "sha256:" + "a" * 64}
    for name, value in overrides.items():
        if name in raw_input:
            raw_input[name] = value
        else:
            setattr(replay, name, value)
    repository = SimpleNamespace(get_by_id=AsyncMock(return_value=source))
    monkeypatch.setattr(
        "src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge.runtime_inbox_repository",
        repository,
    )
    recorded_repository = SimpleNamespace(
        list_recorded_decisions=AsyncMock(
            return_value=[
                SimpleNamespace(
                    payload_json={
                        "record_type": "PLUGIN_DECISION",
                        "attempt_anchor": {"logical_idempotency_key": "source-key"},
                    }
                )
            ]
        )
    )
    monkeypatch.setattr(
        "src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge.timeline_recorded_replay_repository",
        recorded_repository,
        raising=False,
    )

    assert (
        await _replay_digest_matches_source(object(), inbox=replay, route="REPLAY_REQUEST", raw_input=raw_input)
        is expected
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route", "causation_id", "raw_input", "expected"),
    [
        ("SCAN_COMPLETED", "inbox:7", {}, None),
        ("REPLAY_REQUEST", None, {"idempotency_key": "source-key", "payload_digest": "a" * 64}, False),
        ("REPLAY_REQUEST", "event:7", {"idempotency_key": "source-key", "payload_digest": "a" * 64}, False),
        ("REPLAY_REQUEST", "inbox:not-an-int", {"idempotency_key": "source-key", "payload_digest": "a" * 64}, False),
        ("REPLAY_REQUEST", "inbox:0", {"idempotency_key": "source-key", "payload_digest": "a" * 64}, False),
        ("REPLAY_REQUEST", "inbox:8", {"idempotency_key": "source-key", "payload_digest": "a" * 64}, False),
        ("REPLAY_REQUEST", "inbox:7", {"payload_digest": "a" * 64}, False),
        ("REPLAY_REQUEST", "inbox:7", {"idempotency_key": "source-key"}, False),
    ],
)
async def test_logical_replay_invalid_source_identity_fails_closed_before_repository(
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    causation_id: str | None,
    raw_input: dict[str, object],
    expected: bool | None,
) -> None:
    repository = SimpleNamespace(get_by_id=AsyncMock())
    monkeypatch.setattr(
        "src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge.runtime_inbox_repository",
        repository,
    )
    replay = SimpleNamespace(id=8, causation_id=causation_id)

    assert await _replay_digest_matches_source(object(), inbox=replay, route=route, raw_input=raw_input) is expected
    repository.get_by_id.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "recorded_rows",
    [
        [],
        [
            SimpleNamespace(
                payload_json={
                    "record_type": "PLUGIN_DECISION",
                    "attempt_anchor": {"logical_idempotency_key": "source-key"},
                }
            ),
            SimpleNamespace(
                payload_json={
                    "record_type": "PLUGIN_DECISION",
                    "attempt_anchor": {"logical_idempotency_key": "source-key"},
                }
            ),
        ],
        [SimpleNamespace(payload_json={"record_type": "PLUGIN_DECISION", "attempt_anchor": None})],
    ],
)
async def test_logical_replay_requires_one_recorded_decision_with_logical_key(
    monkeypatch: pytest.MonkeyPatch,
    recorded_rows: list[SimpleNamespace],
) -> None:
    source = SimpleNamespace(
        id=7,
        payload_hash="a" * 64,
        workline_id=20,
        workline_session_id=10,
        execution_session_id=30,
        correlation_id="correlation-1",
    )
    replay = SimpleNamespace(
        id=8,
        causation_id="inbox:7",
        workline_id=20,
        workline_session_id=10,
        execution_session_id=30,
        correlation_id="correlation-1",
    )
    monkeypatch.setattr(
        "src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge.runtime_inbox_repository",
        SimpleNamespace(get_by_id=AsyncMock(return_value=source)),
    )
    monkeypatch.setattr(
        "src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge.timeline_recorded_replay_repository",
        SimpleNamespace(list_recorded_decisions=AsyncMock(return_value=recorded_rows)),
    )

    assert (
        await _replay_digest_matches_source(
            object(),
            inbox=replay,
            route="REPLAY_REQUEST",
            raw_input={"idempotency_key": "source-key", "payload_digest": "a" * 64},
        )
        is False
    )


@pytest.mark.asyncio
async def test_generated_runner_dispatches_once_and_records_query_evidence() -> None:
    evidence = QueryEvidence(
        capability_key="wms.lookup",
        contract_version="v1",
        input_hash="a" * 64,
        output_hash="b" * 64,
        authority="WMS",
        source="test",
        evidence_at="2026-07-18T00:00:00Z",
        source_version="v1",
        admission_snapshot={"profile": "test"},
        summary={"outcome": {"type": "Success"}},
    )

    class Gateway:
        async def execute(self, *_args: object) -> GatewayQueryResult:
            return GatewayQueryResult(outcome=Success(payload={"accepted": True}), evidence=evidence)

    state = RoughSorterState()
    decision = PluginDecision[RoughSorterState](intents=(), next_state=state, outcome_code="ROUTE_A")

    async def dispatch(*, request: object, gateway: object) -> object:
        assert request is not None
        _ = await gateway.execute("wms.lookup", "v1", {})
        return decision

    dispatcher = SimpleNamespace(dispatch=AsyncMock(side_effect=dispatch))
    request = SimpleNamespace()
    context = PluginAttemptContext(
        attempt_id="attempt-1",
        inbox_id=1,
        session_id=2,
        workline_id=3,
        event_type="SCAN_COMPLETED",
        payload={},
        plugin_state=state.model_dump(mode="json"),
        snapshot=AttemptSnapshot(processor_token="lease-1", session_version=1, plugin_state_version=0),
        runtime=SimpleNamespace(gateway=Gateway()),
        dispatch_request=request,
    )

    runner = GeneratedPluginAttemptRunner(dispatcher=dispatcher)
    write_set = await runner.run(context)

    dispatcher.dispatch.assert_awaited_once()
    assert write_set.outcome_code == "ROUTE_A"
    assert write_set.next_state == state
    assert write_set.evidence == (evidence,)


@pytest.mark.asyncio
async def test_generated_runner_fails_closed_without_legacy_fallback() -> None:
    dispatcher = SimpleNamespace(
        dispatch=AsyncMock(return_value=ContractViolation(error_code="PLUGIN_ROUTE_UNKNOWN", message="unknown"))
    )
    context = PluginAttemptContext(
        attempt_id="attempt-1",
        inbox_id=1,
        session_id=2,
        workline_id=3,
        event_type="SCAN_COMPLETED",
        payload={},
        plugin_state={"phase": "READY"},
        snapshot=AttemptSnapshot(processor_token="lease-1", session_version=1, plugin_state_version=0),
        runtime=SimpleNamespace(gateway=SimpleNamespace()),
        dispatch_request=SimpleNamespace(),
    )

    write_set = await GeneratedPluginAttemptRunner(dispatcher=dispatcher).run(context)

    assert write_set.outcome_code == "HOLD"
    assert write_set.hold_reason == "PLUGIN_ROUTE_UNKNOWN"
    assert write_set.intents == ()


@pytest.mark.asyncio
async def test_generated_runner_converts_plugin_effects_to_system_capability_intents() -> None:
    state = RoughSorterState(phase="PICK_TO_PIPELINE")
    decision = PluginDecision[RoughSorterState](
        intents=(
            RuntimeIntent.command(
                device_role="input_arm",
                action="PICK_AND_PUT",
                payload={"pkg_code": "PKG-1"},
                result_policy="COMMAND_RESULT",
            ),
        ),
        next_state=state,
        outcome_code="PICK_AND_PUT_PERSISTED",
    )
    dispatcher = SimpleNamespace(dispatch=AsyncMock(return_value=decision))
    context = PluginAttemptContext(
        attempt_id="attempt-1",
        inbox_id=1,
        session_id=2,
        workline_id=3,
        event_type="SCAN_COMPLETED",
        payload={},
        plugin_state={"phase": "READY"},
        snapshot=AttemptSnapshot(
            processor_token="lease-1",
            session_version=7,
            plugin_state_version=0,
            device_fact_versions=(("input_arm", 31, 0),),
            binding_id=17,
            binding_version=4,
        ),
        runtime=SimpleNamespace(gateway=SimpleNamespace()),
        dispatch_request=SimpleNamespace(),
    )

    write_set = await GeneratedPluginAttemptRunner(dispatcher=dispatcher).run(context)

    assert len(write_set.intents) == 1
    intent = write_set.intents[0]
    assert intent.kind is RuntimeIntentKind.SYSTEM_CAPABILITY
    assert (intent.capability_key, intent.contract_version) == ("device.device_command_write", "v1")
    assert intent.payload_json["action"] == "PICK_AND_PUT"
    assert intent.payload_json["result_policy"] == "COMMAND_RESULT"
    assert intent.binding_snapshot == {"binding_id": 17, "binding_version": 4}


def test_generated_command_uses_pinned_target_device_fact_version() -> None:
    context = SimpleNamespace(
        inbox_id=1,
        snapshot=SimpleNamespace(
            binding_id=17,
            binding_version=4,
            binding_identity="binding:17:4",
            device_fact_versions=(("input_arm", 31, 0),),
        ),
    )

    [intent] = _system_capability_intents(
        context,
        (
            RuntimeIntent.command(
                device_role="input_arm",
                action="PICK_AND_PUT",
                payload={"pkg_code": "PKG-1"},
                result_policy="COMMAND_RESULT",
            ),
        ),
    )

    assert intent.payload_json["target_device_id"] == 31
    assert intent.payload_json["device_role"] is None
    assert intent.fact_version == "device:v0"


def test_material_status_update_uses_pinned_material_fact_version() -> None:
    context = SimpleNamespace(
        inbox_id=1,
        snapshot=SimpleNamespace(
            binding_id=17,
            binding_version=4,
            binding_identity="binding:17:4",
            session_version=7,
            material_unit_id=31,
            material_unit_version=11,
        ),
    )

    [intent] = _system_capability_intents(
        context,
        (RuntimeIntent.update_material_unit_status(material_unit_id=31, status="COMPLETED"),),
    )

    assert intent.fact_version == 11


def test_material_status_update_rejects_fact_version_from_different_material() -> None:
    context = SimpleNamespace(
        inbox_id=1,
        snapshot=SimpleNamespace(
            binding_id=17,
            binding_version=4,
            binding_identity="binding:17:4",
            material_unit_id=31,
            material_unit_version=11,
        ),
    )

    with pytest.raises(ValueError, match="pinned material unit"):
        _system_capability_intents(
            context,
            (RuntimeIntent.update_material_unit_status(material_unit_id=99, status="COMPLETED"),),
        )


@pytest.mark.asyncio
async def test_generated_runner_rejects_model_copy_missing_pick_result_policy_without_fire_and_forget_fallback() -> (
    None
):
    valid_pick = RuntimeIntent.command(
        device_role="input_arm",
        action="PICK_AND_PUT",
        payload={"pkg_code": "PKG-1"},
        result_policy="COMMAND_RESULT",
    )
    bypassed_pick = valid_pick.model_copy(update={"result_policy": None})
    decision = PluginDecision[RoughSorterState](
        intents=(valid_pick,),
        next_state=RoughSorterState(phase="PICK_TO_PIPELINE"),
        outcome_code="PICK_AND_PUT_PERSISTED",
    ).model_copy(update={"intents": (bypassed_pick,)})
    context = PluginAttemptContext(
        attempt_id="attempt-model-copy-policy",
        inbox_id=1,
        session_id=2,
        workline_id=3,
        event_type="SCAN_COMPLETED",
        payload={},
        plugin_state={"phase": "READY"},
        snapshot=AttemptSnapshot(
            processor_token="lease-model-copy-policy",
            session_version=7,
            plugin_state_version=0,
            binding_id=17,
            binding_version=4,
        ),
        runtime=SimpleNamespace(gateway=SimpleNamespace()),
        dispatch_request=SimpleNamespace(),
    )

    write_set = await GeneratedPluginAttemptRunner(
        dispatcher=SimpleNamespace(dispatch=AsyncMock(return_value=decision))
    ).run(context)

    assert write_set.outcome_code == "HOLD"
    assert write_set.hold_reason == "PLUGIN_EFFECT_CONVERSION_INVALID"
    assert write_set.intents == ()


@pytest.mark.asyncio
async def test_generated_block_uses_pinned_waiting_status_and_session_version() -> None:
    decision = PluginDecision[RoughSorterState](
        intents=(
            RuntimeIntent.block(
                scope=BlockScope.WORKLINE,
                reason_code="WMS_REJECTED",
                message="WMS rejected inventory admission",
            ),
        ),
        next_state=RoughSorterState(phase="NG_MOVING"),
        outcome_code="HOLD",
    )
    context = PluginAttemptContext(
        attempt_id="attempt-1",
        inbox_id=1,
        session_id=2,
        workline_id=3,
        event_type="COMMAND_RESULT",
        payload={},
        plugin_state={"phase": "WAITING_PICK_RESULT"},
        snapshot=AttemptSnapshot(
            processor_token="lease-1",
            session_version=7,
            plugin_state_version=2,
            session_status="WAITING_DEVICE_RESULT",
            binding_id=17,
            binding_version=4,
        ),
        runtime=SimpleNamespace(gateway=SimpleNamespace()),
        dispatch_request=SimpleNamespace(),
    )

    write_set = await GeneratedPluginAttemptRunner(
        dispatcher=SimpleNamespace(dispatch=AsyncMock(return_value=decision))
    ).run(context)

    [intent] = write_set.intents
    assert intent.capability_key == "runtime.session_hold"
    assert intent.precondition_json == {"expected_status": "WAITING_DEVICE_RESULT"}
    assert intent.fact_version == "session:7"


@pytest.mark.asyncio
async def test_live_block_timeline_recorded_replay_restores_decision_without_reexecuting_effect() -> None:
    from src.app.runtime.orchestration.repositories.plugin_attempt_repository import (
        AuthoritativePluginAttempt,
        PluginAttemptRepository,
    )
    from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_writeback_service import (
        RuntimeInboxWriteBackService,
    )
    from src.app.runtime.system_capabilities.replay import TimelineRecordedReplayService
    from src.app.runtime.workline_plugins.attempt_coordinator import WriteDisposition

    next_state = RoughSorterState(phase="NG_MOVING")
    decision = PluginDecision[RoughSorterState](
        intents=(
            RuntimeIntent.block(
                scope=BlockScope.WORKLINE,
                reason_code="WMS_REJECTED",
                message="WMS rejected inventory admission",
            ),
        ),
        next_state=next_state,
        outcome_code="HOLD",
    )
    snapshot = AttemptSnapshot(
        processor_token="lease-live",
        session_version=7,
        plugin_state_version=2,
        session_status="WAITING_DEVICE_RESULT",
        definition_identity=ROUGH_SORTER_DEFINITION.identity,
        binding_id=17,
        binding_version=4,
        index_digest=WORKLINE_PLUGIN_INDEX_DIGEST,
    )
    source_write_set = await GeneratedPluginAttemptRunner(
        dispatcher=SimpleNamespace(dispatch=AsyncMock(return_value=decision))
    ).run(
        PluginAttemptContext(
            attempt_id="lease-live",
            inbox_id=91,
            session_id=41,
            workline_id=8,
            event_type="COMMAND_RESULT",
            payload={},
            plugin_state={"phase": "WAITING_PICK_RESULT"},
            snapshot=snapshot,
            runtime=SimpleNamespace(gateway=SimpleNamespace()),
            dispatch_request=SimpleNamespace(),
        )
    )

    timeline_rows: list[object] = []

    class TimelineOwner:
        async def allocate_many(self, *_args: object, **_kwargs: object) -> tuple[int, ...]:
            return (73,)

    class TimelineDb:
        def add(self, row: object) -> None:
            timeline_rows.append(row)

    source_session = SimpleNamespace(id=41, plugin_state_json={}, plugin_state_version=2)
    await PluginAttemptRepository(timeline_sequence_repository=TimelineOwner()).persist_locked_attempt(
        TimelineDb(),  # type: ignore[arg-type]
        locked=AuthoritativePluginAttempt(inbox=SimpleNamespace(id=91), session=source_session),
        workline_id=8,
        trace_id="trace-live",
        snapshot=snapshot,
        write_set=source_write_set,
    )

    class RecordedTimelineRepository:
        async def list_recorded_decisions(self, *_args: object, **_kwargs: object) -> list[object]:
            return timeline_rows

    resolution = await TimelineRecordedReplayService(repository=RecordedTimelineRepository()).load(
        object(),  # type: ignore[arg-type]
        source_inbox_id=91,
        expected_definition_identity=ROUGH_SORTER_DEFINITION.identity,
        expected_binding_identity="binding:17:4",
        expected_index_digest=WORKLINE_PLUGIN_INDEX_DIGEST,
    )
    replayed = _write_set_from_recorded_replay(
        resolution,
        fallback_state={"phase": "WAITING_PICK_RESULT"},
    )

    effect_applier = SimpleNamespace(apply=AsyncMock(side_effect=AssertionError("recorded effect must not execute")))

    class ReplayDb:
        async def commit(self) -> None:
            pass

        async def rollback(self) -> None:
            pass

    class ReplayRepository:
        async def lock_authoritative(self, *_args: object, **_kwargs: object) -> object:
            return SimpleNamespace(
                inbox=SimpleNamespace(processor_token="lease-replay"),
                session=SimpleNamespace(version=7, plugin_state_version=3),
            )

        async def persist_locked_attempt(self, *_args: object, **_kwargs: object) -> None:
            pass

    class InboxService:
        async def mark_processed(self, *_args: object, **_kwargs: object) -> bool:
            return True

        async def mark_failed(self, *_args: object, **_kwargs: object) -> bool:
            return True

    replay_snapshot = AttemptSnapshot(processor_token="lease-replay", session_version=7, plugin_state_version=3)
    disposition = await RuntimeInboxWriteBackService(
        plugin_attempt_repository=ReplayRepository(),
        effect_applier=effect_applier,
        inbox_service=InboxService(),  # type: ignore[arg-type]
    ).commit_plugin_attempt(
        ReplayDb(),
        expected_snapshot=replay_snapshot,
        inbox_id=92,
        session_id=41,
        workline_id=8,
        trace_id="trace-replay",
        write_set=replayed,
    )

    assert source_write_set.outcome_code == "HOLD"
    assert source_write_set.hold_reason is None
    assert source_write_set.intents[0].capability_key == "runtime.session_hold"
    assert replayed.outcome_code == "HOLD"
    assert replayed.hold_reason is None
    assert replayed.next_state == {}
    assert replayed.preserve_plugin_state is True
    assert replayed.evidence == source_write_set.evidence
    assert replayed.intents == ()
    assert disposition is WriteDisposition.COMMITTED
    effect_applier.apply.assert_not_awaited()


@pytest.mark.parametrize(
    ("path", "tampered_value"),
    [
        (("capability_key",), "runtime.other"),
        (("contract_version",), "v2"),
        (("operation_key",), "inbox:91:1:block"),
        (("operation_key",), "inbox:92:0:block"),
        (("payload_hash",), "0" * 64),
        (("precondition_json", "expected_status"), ""),
        (("precondition_json", "expected_status"), "RUNNING"),
        (("fact_version",), "session:stale"),
        (("fact_version",), "session:8"),
        (("creator_authority",), "EXTERNAL"),
        (("authorization_policy",), "UNTRUSTED"),
        (("binding_snapshot", "binding_version"), 5),
        (("provider_snapshot", "profile"), "other"),
        (("timeout_seconds",), 30),
        (("forged_extra",), True),
    ],
)
def test_recorded_live_hold_rejects_tampered_system_capability_identity_or_shape(
    path: tuple[str, ...],
    tampered_value: object,
) -> None:
    from src.app.runtime.system_capabilities.replay import RecordedReplayResolution

    intent = RuntimeIntent.system_capability(
        capability_key="runtime.session_hold",
        contract_version="v1",
        operation_key="inbox:91:0:block",
        dispatch_key="system-capability:runtime.session_hold:inbox-91-0-block",
        payload={
            "failure_domain": "WORKLINE",
            "reason_code": "WMS_REJECTED",
            "message": "WMS rejected inventory admission",
        },
        precondition={"expected_status": "WAITING_DEVICE_RESULT"},
        fact_version="session:7",
        timeout_seconds=5,
        creator_authority="WORKLINE_PLUGIN",
        authorization_policy="PLUGIN_DECLARED_CAPABILITY",
        binding_snapshot={"binding_id": 17, "binding_version": 4},
        provider_snapshot={"provider_code": "RUNTIME", "profile": "runtime"},
    ).model_dump(mode="json")
    tampered = deepcopy(intent)
    target = tampered
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = tampered_value

    replayed = _write_set_from_recorded_replay(
        RecordedReplayResolution(
            binding_identity="binding:17:4",
            attempt_anchor={
                "source_inbox_id": 91,
                "session_version": 7,
                "session_status": "WAITING_DEVICE_RESULT",
                "logical_idempotency_key": "workline-plugin:rough_sorter:material:PKG-91:decision",
            },
            decision={
                "outcome_code": "HOLD",
                "hold_reason": None,
                "next_state": {"phase": "NG_MOVING"},
                "intents": [tampered],
            },
        ),
        fallback_state={"phase": "WAITING_PICK_RESULT"},
    )

    assert replayed.outcome_code == "HOLD"
    assert replayed.hold_reason == "RECORDED_REPLAY_RECORD_INVALID"
    assert replayed.intents == ()


def test_recorded_replay_preserves_current_plugin_state_after_source_attempt_advanced() -> None:
    from src.app.runtime.system_capabilities.replay import RecordedReplayResolution

    current_state = {"phase": "WAITING_COMMAND_RESULT", "current_correlation": "CMD-NEW"}
    replayed = _write_set_from_recorded_replay(
        RecordedReplayResolution(
            decision={
                "outcome_code": "ROUTE_A",
                "hold_reason": None,
                "next_state": {"phase": "READY", "current_correlation": "CMD-OLD"},
                "intents": [],
            }
        ),
        fallback_state=current_state,
    )

    assert replayed.hold_reason is None
    assert replayed.next_state == {}
    assert replayed.preserve_plugin_state is True


@pytest.mark.asyncio
async def test_non_create_mark_ng_pins_material_identity_before_device_effect() -> None:
    decision = PluginDecision[RoughSorterState](
        intents=(
            RuntimeIntent.mark_ng(reason_code="MEASUREMENT_NG", message="measurement rejected"),
            RuntimeIntent.command(
                device_role="output_arm", action="MOVE_TO_NG", payload={}, result_policy="COMMAND_RESULT"
            ),
        ),
        next_state=RoughSorterState(phase="NG_MOVING"),
        outcome_code="MEASUREMENT_NG",
    )
    context = PluginAttemptContext(
        attempt_id="attempt-1",
        inbox_id=1,
        session_id=2,
        workline_id=3,
        event_type="COMMAND_RESULT",
        payload={},
        plugin_state={"phase": "WAITING_PICK_RESULT"},
        snapshot=AttemptSnapshot(
            processor_token="lease-1",
            session_version=7,
            plugin_state_version=2,
            session_status="WAITING_DEVICE_RESULT",
            material_unit_id=31,
            material_unit_version=11,
            device_fact_versions=(("output_arm", 32, 5),),
            binding_id=17,
            binding_version=4,
        ),
        runtime=SimpleNamespace(gateway=SimpleNamespace()),
        dispatch_request=SimpleNamespace(),
    )

    write_set = await GeneratedPluginAttemptRunner(
        dispatcher=SimpleNamespace(dispatch=AsyncMock(return_value=decision))
    ).run(context)

    assert [intent.capability_key for intent in write_set.intents] == [
        "material_flow.material_unit_write",
        "device.device_command_write",
    ]
    material_intent = write_set.intents[0]
    assert material_intent.payload_json == {"operation": "MARK_NG", "material_unit_id": 31, "status": "NG"}
    assert material_intent.fact_version == 11


def test_plugin_dispatch_request_type_is_not_a_raw_system_capability_result() -> None:
    assert "SYSTEM_CAPABILITY_RESULT" not in str(PluginDispatchRequest.model_fields["logical_route"].annotation)


def test_material_unit_fact_version_uses_persisted_updated_at_when_model_has_no_version() -> None:
    from datetime import UTC, datetime

    material_unit = SimpleNamespace(version=None, updated_at=datetime(2026, 7, 18, tzinfo=UTC))

    assert _material_unit_fact_version(material_unit) == 1784332800000


@pytest.mark.asyncio
async def test_plugin_dispatch_prefers_persisted_material_identity_over_conflicting_ingress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MaterialUnit 已存在时，callback/session 冲突值不得覆盖持久化业务事实。"""

    from src.app.runtime.orchestration.repositories.material_unit_repository import material_unit_repository
    from src.app.workline.services.plugin_binding_service import workline_plugin_binding_service

    config = {
        "device_roles": {
            "input_arm": "ROUGH_SORTER_INPUT_ARM",
            "conveyor": "ROUGH_SORTER_CONVEYOR",
            "output_arm": "ROUGH_SORTER_OUTPUT_ARM",
        },
        "pipeline_input_location": "PIPELINE-IN",
        "pipeline_output_location": "PIPELINE-OUT",
        "ng_location": "NG-01",
        "warehouse_code": "WH-01",
        "owner_code": "OWNER-01",
        "provider_profile": "runtime",
    }
    monkeypatch.setattr(
        workline_plugin_binding_service,
        "get_pinned",
        AsyncMock(
            return_value=SimpleNamespace(
                id=17,
                binding_version=4,
                plugin_key="rough_sorter",
                contract_version="rough_sorter.v2",
                typed_config_json=config,
                typed_config_hash=sha256_digest(config),
                is_enabled=True,
                is_revoked=False,
                environment="sandbox",
                valid_from=None,
                valid_until=None,
            )
        ),
    )
    monkeypatch.setattr(
        material_unit_repository,
        "get_plugin_fact_payload",
        AsyncMock(
            return_value={
                "pkg_code": "PKG-PERSISTED",
                "material_identity_key": "BUSINESS-PERSISTED",
                "six_in_one": {
                    "PkgID": "PKG-PERSISTED-SIX-IN-ONE-CONFLICT",
                    "HHPN": "HH-PERSISTED",
                    "LotCode": "LOT-PERSISTED",
                },
            }
        ),
    )
    session = SimpleNamespace(
        plugin_state_json={"phase": "PICK_TO_PIPELINE", "current_correlation": "CMD-1"},
        context_json={
            "business_key": "BUSINESS-SESSION",
            "six_in_one": {"HHPN": "HH-SESSION", "LotCode": "LOT-SESSION"},
        },
        current_material_unit_id=31,
        awaiting_device_command_code="CMD-1",
    )
    snapshot = AttemptSnapshot(
        processor_token="lease-1",
        session_version=7,
        plugin_state_version=2,
        binding_id=17,
        binding_version=4,
        plugin_config_hash=sha256_digest(config),
        index_digest=WORKLINE_PLUGIN_INDEX_DIGEST,
    )
    inbox = SimpleNamespace(
        kind="COMMAND_RESULT",
        event_type="COMMAND_RESULT",
        payload_json={
            "command_code": "CMD-1",
            "command_type": "PICK_AND_PUT",
            "result": "SUCCESS",
            "data": {
                "PkgID": "PKG-CALLBACK",
                "HHPN": "HH-CALLBACK",
                "LotCode": "LOT-CALLBACK",
            },
        },
    )

    request = await _build_plugin_dispatch_request(
        object(), inbox=inbox, session=session, workline=SimpleNamespace(id=3), snapshot=snapshot
    )

    from src.app.runtime.workline_plugins.rough_sorter.handlers import build_facts

    facts = build_facts(request.fact_source)
    assert facts.business_key == "BUSINESS-PERSISTED"
    assert facts.hhpn == "HH-PERSISTED"
    assert facts.lot_code == "LOT-PERSISTED"


@pytest.mark.asyncio
async def test_plugin_dispatch_uses_root_pkg_code_before_persisted_six_in_one_pkg_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """缺少 material_identity_key 时，root pkg_code 仍优先于 six_in_one.PkgID。"""

    from src.app.runtime.orchestration.repositories.material_unit_repository import material_unit_repository
    from src.app.workline.services.plugin_binding_service import workline_plugin_binding_service

    config = {
        "device_roles": {
            "input_arm": "ROUGH_SORTER_INPUT_ARM",
            "conveyor": "ROUGH_SORTER_CONVEYOR",
            "output_arm": "ROUGH_SORTER_OUTPUT_ARM",
        },
        "pipeline_input_location": "PIPELINE-IN",
        "pipeline_output_location": "PIPELINE-OUT",
        "ng_location": "NG-01",
        "warehouse_code": "WH-01",
        "owner_code": "OWNER-01",
        "provider_profile": "runtime",
    }
    monkeypatch.setattr(
        workline_plugin_binding_service,
        "get_pinned",
        AsyncMock(
            return_value=SimpleNamespace(
                id=17,
                binding_version=4,
                plugin_key="rough_sorter",
                contract_version="rough_sorter.v2",
                typed_config_json=config,
                typed_config_hash=sha256_digest(config),
                is_enabled=True,
                is_revoked=False,
                environment="sandbox",
                valid_from=None,
                valid_until=None,
            )
        ),
    )
    monkeypatch.setattr(
        material_unit_repository,
        "get_plugin_fact_payload",
        AsyncMock(
            return_value={
                "pkg_code": "PKG-ROOT",
                "six_in_one": {"PkgID": "PKG-NESTED-CONFLICT", "HHPN": "HH-ROOT", "LotCode": "LOT-ROOT"},
            }
        ),
    )
    request = await _build_plugin_dispatch_request(
        object(),
        inbox=SimpleNamespace(
            kind="COMMAND_RESULT",
            event_type="COMMAND_RESULT",
            payload_json={"command_code": "CMD-1", "result": "SUCCESS", "data": {"PkgID": "PKG-CALLBACK"}},
        ),
        session=SimpleNamespace(
            plugin_state_json={},
            context_json={},
            current_material_unit_id=31,
            awaiting_device_command_code="CMD-1",
        ),
        workline=SimpleNamespace(id=3),
        snapshot=AttemptSnapshot(
            processor_token="lease-1",
            session_version=7,
            plugin_state_version=2,
            binding_id=17,
            binding_version=4,
            plugin_config_hash=sha256_digest(config),
            index_digest=WORKLINE_PLUGIN_INDEX_DIGEST,
        ),
    )

    from src.app.runtime.workline_plugins.rough_sorter.handlers import build_facts

    assert build_facts(request.fact_source).business_key == "PKG-ROOT"


@pytest.mark.asyncio
async def test_generated_rough_sorter_scan_route_has_unique_handler_and_system_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = {
        "device_roles": {
            "input_arm": "ROUGH_SORTER_INPUT_ARM",
            "conveyor": "ROUGH_SORTER_CONVEYOR",
            "output_arm": "ROUGH_SORTER_OUTPUT_ARM",
        },
        "pipeline_input_location": "PIPELINE-IN",
        "pipeline_output_location": "PIPELINE-OUT",
        "ng_location": "NG-01",
        "warehouse_code": "WH-01",
        "owner_code": "OWNER-01",
        "provider_profile": "runtime",
    }
    binding = SimpleNamespace(
        id=17,
        binding_version=4,
        plugin_key="rough_sorter",
        contract_version="rough_sorter.v2",
        typed_config_json=config,
        typed_config_hash=sha256_digest(config),
        is_enabled=True,
        is_revoked=False,
        environment="sandbox",
        valid_from=None,
        valid_until=None,
    )
    from src.app.workline.services.plugin_binding_service import workline_plugin_binding_service

    get_pinned = AsyncMock(return_value=binding)
    monkeypatch.setattr(workline_plugin_binding_service, "get_pinned", get_pinned)
    session = SimpleNamespace(
        id=2,
        version=7,
        plugin_state_version=0,
        plugin_state_json={"phase": "READY"},
        context_json=deepcopy(_Q19_ADMISSION_CONTEXT),
        plugin_key="rough_sorter",
        contract_version="rough_sorter.v2",
        plugin_binding_id=17,
        plugin_binding_version=4,
        plugin_index_digest=WORKLINE_PLUGIN_INDEX_DIGEST,
        awaiting_device_command_code=None,
    )
    snapshot = AttemptSnapshot(
        processor_token="lease-1",
        session_version=7,
        plugin_state_version=0,
        device_fact_versions=(("ROUGH_SORTER_INPUT_ARM", 31, 0),),
        binding_id=17,
        binding_version=4,
        plugin_config_hash=sha256_digest(config),
        index_digest=WORKLINE_PLUGIN_INDEX_DIGEST,
    )
    inbox = SimpleNamespace(
        id=1,
        kind="DEVICE_EVENT",
        event_type="SCAN_COMPLETED",
        payload_json={"event_type": "SCAN", "data": {"PkgID": "PKG-1"}},
    )
    request = await _build_plugin_dispatch_request(
        object(), inbox=inbox, session=session, workline=SimpleNamespace(id=3), snapshot=snapshot
    )
    runtime = RuntimeInboxProcessorBridge().create_attempt_runtime("lease-1")
    context = PluginAttemptContext(
        attempt_id="lease-1",
        inbox_id=1,
        session_id=2,
        workline_id=3,
        event_type="SCAN_COMPLETED",
        payload=inbox.payload_json,
        plugin_state=session.plugin_state_json,
        snapshot=snapshot,
        runtime=runtime,
        dispatch_request=request,
    )

    write_set = await GeneratedPluginAttemptRunner().run(context)

    get_pinned.assert_awaited_once()
    assert write_set.outcome_code == "PICK_AND_PUT_PERSISTED"
    assert [intent.kind for intent in write_set.intents] == [
        RuntimeIntentKind.SYSTEM_CAPABILITY,
        RuntimeIntentKind.SYSTEM_CAPABILITY,
    ]
    assert [intent.capability_key for intent in write_set.intents] == [
        "material_flow.material_unit_write",
        "device.device_command_write",
    ]
    assert write_set.intents[1].payload_json["result_policy"] == "COMMAND_RESULT"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command_id", "command", "command_code", "expected"),
    [
        (None, None, "SC-1", "COMMAND_ID_MISSING"),
        (91, None, "SC-1", "COMMAND_NOT_FOUND"),
        (91, SimpleNamespace(task_type=None), "SC-1", "COMMAND_TASK_TYPE_MISSING"),
        (
            91,
            SimpleNamespace(
                task_type="UNSUPPORTED_ACTION",
                command_code="SC-1",
                correlation_id="workline-session:session-2",
                workline_id=3,
                plugin_key="smt_sorting_inbound",
                contract_version="smt_sorting_inbound.v1",
                status="COMPLETED",
                result="SUCCESS",
                result_data={},
                error_detail={},
            ),
            "SC-1",
            "COMMAND_TASK_TYPE_UNSUPPORTED",
        ),
        (
            91,
            SimpleNamespace(
                task_type="SORTING_SOURCE_PICK",
                command_code="SC-OTHER",
                correlation_id="workline-session:session-2",
                workline_id=3,
                plugin_key="smt_sorting_inbound",
                contract_version="smt_sorting_inbound.v1",
                status="COMPLETED",
                result="SUCCESS",
                result_data={},
                error_detail={},
            ),
            "SC-1",
            "COMMAND_RESULT_CORRELATION_MISMATCH",
        ),
        (
            91,
            SimpleNamespace(
                task_type="SORTING_SOURCE_PICK",
                command_code="SC-1",
                correlation_id="workline-session:session-2",
                workline_id=3,
                plugin_key="smt_sorting_inbound",
                contract_version="smt_sorting_inbound.v1",
                status="COMPLETED",
                result="SUCCESS",
                result_data={"authority": "database"},
                error_detail={},
            ),
            "SC-1",
            "SOURCE_PICK_COMPLETED",
        ),
        (
            91,
            SimpleNamespace(
                task_type="SORTING_SOURCE_PICK",
                command_code="SC-1",
                correlation_id="workline-session:session-2",
                workline_id=3,
                plugin_key="smt_sorting_inbound",
                contract_version="smt_sorting_inbound.v1",
                status="FAILED",
                result="FAILED",
                result_data={"authority": "database"},
                error_detail={"error_code": "PERSISTED_COMMAND_FAILED"},
            ),
            "SC-1",
            "HOLD",
        ),
        (
            91,
            SimpleNamespace(
                task_type="SORTING_SOURCE_PICK",
                command_code="SC-1",
                correlation_id="workline-session:session-2",
                workline_id=3,
                plugin_key="smt_sorting_inbound",
                contract_version="smt_sorting_inbound.v1",
                status="SENT",
                result=None,
                result_data=None,
                error_detail=None,
            ),
            "SC-1",
            "COMMAND_RESULT_EVIDENCE_INVALID",
        ),
    ],
)
async def test_command_result_bridge_uses_persisted_command_and_returns_stable_zero_effect_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    command_id: int | None,
    command: object | None,
    command_code: str,
    expected: str,
) -> None:
    """COMMAND_RESULT 必须经 command repository 后再进入 generated dispatcher。"""

    from src.app.device.repositories import device_command_repository
    from src.app.workline.services.plugin_binding_service import workline_plugin_binding_service

    config = {"provider_profile": "runtime", "source_arm_role": "SORTING_SOURCE_ARM"}
    monkeypatch.setattr(
        workline_plugin_binding_service,
        "get_pinned",
        AsyncMock(
            return_value=SimpleNamespace(
                id=17,
                binding_version=4,
                plugin_key="smt_sorting_inbound",
                contract_version="smt_sorting_inbound.v1",
                typed_config_json=config,
                typed_config_hash=sha256_digest(config),
                is_enabled=True,
                is_revoked=False,
                environment="sandbox",
                valid_from=None,
                valid_until=None,
            )
        ),
    )
    get_by_id = AsyncMock(return_value=command)
    monkeypatch.setattr(device_command_repository, "get_by_id", get_by_id)
    snapshot = AttemptSnapshot(
        processor_token="smt-command-result",
        session_version=1,
        plugin_state_version=0,
        binding_id=17,
        binding_version=4,
        plugin_config_hash=sha256_digest(config),
        index_digest=WORKLINE_PLUGIN_INDEX_DIGEST,
    )
    inbox = SimpleNamespace(
        id=91,
        command_id=command_id,
        correlation_id="workline-session:session-2",
        workline_id=3,
        workline_session_id=2,
        kind="COMMAND_RESULT",
        event_type="COMMAND_RESULT",
        payload_json={
            "command_code": command_code,
            "command_type": "FORGED_CALLBACK_TYPE",
            "result": "SUCCESS",
        },
    )
    session = SimpleNamespace(
        id=2,
        session_code="session-2",
        workline_id=3,
        plugin_key="smt_sorting_inbound",
        contract_version="smt_sorting_inbound.v1",
        plugin_binding_id=17,
        plugin_state_json={"phase": "WAITING_SOURCE_PICK", "current_correlation": "SC-1"},
        context_json={},
        current_material_unit_id=None,
        awaiting_device_command_code="SC-1",
    )

    db = object()
    request = await _build_plugin_dispatch_request(
        db, inbox=inbox, session=session, workline=SimpleNamespace(id=3), snapshot=snapshot
    )
    write_set = await GeneratedPluginAttemptRunner().run(
        PluginAttemptContext(
            attempt_id="smt-command-result",
            inbox_id=91,
            session_id=2,
            workline_id=3,
            event_type="COMMAND_RESULT",
            payload=inbox.payload_json,
            plugin_state=session.plugin_state_json,
            snapshot=snapshot,
            runtime=SimpleNamespace(gateway=object()),
            dispatch_request=request,
        )
    )

    assert write_set.outcome_code == expected
    if expected != "SOURCE_PICK_COMPLETED":
        assert write_set.intents == ()
    if getattr(command, "result", None) == "FAILED":
        assert request.raw_input["result"] == "FAILED"
        assert request.raw_input["data"] == {"authority": "database"}
        assert request.raw_input["error_detail"] == {"error_code": "PERSISTED_COMMAND_FAILED"}
    if command_id is None:
        get_by_id.assert_not_awaited()
    else:
        get_by_id.assert_awaited_once_with(db, command_id)


@pytest.mark.asyncio
async def test_command_result_consumes_persisted_q19_without_requery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.app.device.repositories import device_command_repository
    from src.app.workline.services.plugin_binding_service import workline_plugin_binding_service

    config = {
        "device_roles": {
            "input_arm": "ROUGH_SORTER_INPUT_ARM",
            "conveyor": "ROUGH_SORTER_CONVEYOR",
            "output_arm": "ROUGH_SORTER_OUTPUT_ARM",
        },
        "pipeline_input_location": "PIPELINE-IN",
        "pipeline_output_location": "PIPELINE-OUT",
        "ng_location": "NG-01",
        "warehouse_code": "WH-01",
        "owner_code": "OWNER-01",
        "provider_profile": "runtime",
    }
    monkeypatch.setattr(
        workline_plugin_binding_service,
        "get_pinned",
        AsyncMock(
            return_value=SimpleNamespace(
                id=17,
                binding_version=4,
                plugin_key="rough_sorter",
                contract_version="rough_sorter.v2",
                typed_config_json=config,
                typed_config_hash=sha256_digest(config),
                is_enabled=True,
                is_revoked=False,
                environment="sandbox",
                valid_from=None,
                valid_until=None,
            )
        ),
    )
    monkeypatch.setattr(
        device_command_repository,
        "get_by_id",
        AsyncMock(
            return_value=SimpleNamespace(
                task_type="PICK_AND_PUT",
                command_code="CMD-1",
                correlation_id="workline-session:session-2",
                workline_id=3,
                plugin_key="rough_sorter",
                contract_version="rough_sorter.v2",
                status="COMPLETED",
                result="SUCCESS",
                result_data={
                    "PkgID": "PKG-1",
                    "HHPN": "HH-1",
                    "LotCode": "LOT-1",
                    "measurement_result": "OK",
                    "reel_diameter": 180,
                    "reel_thickness": 16,
                },
                error_detail={},
            )
        ),
    )
    session = SimpleNamespace(
        id=2,
        session_code="session-2",
        workline_id=3,
        version=7,
        plugin_state_version=2,
        plugin_state_json={"phase": "PICK_TO_PIPELINE", "current_correlation": "CMD-1"},
        context_json=deepcopy(_Q19_ADMISSION_CONTEXT),
        plugin_key="rough_sorter",
        contract_version="rough_sorter.v2",
        plugin_binding_id=17,
        plugin_binding_version=4,
        plugin_index_digest=WORKLINE_PLUGIN_INDEX_DIGEST,
        awaiting_device_command_code="CMD-1",
    )
    snapshot = AttemptSnapshot(
        processor_token="lease-1",
        session_version=7,
        plugin_state_version=2,
        device_fact_versions=(("ROUGH_SORTER_CONVEYOR", 33, 6),),
        binding_id=17,
        binding_version=4,
        plugin_config_hash=sha256_digest(config),
        index_digest=WORKLINE_PLUGIN_INDEX_DIGEST,
    )
    inbox = SimpleNamespace(
        id=1,
        command_id=91,
        correlation_id="workline-session:session-2",
        workline_id=3,
        workline_session_id=2,
        kind="COMMAND_RESULT",
        event_type="COMMAND_RESULT",
        payload_json={
            "command_code": "CMD-1",
            "command_type": "PICK_AND_PUT",
            "result": "SUCCESS",
            "data": {
                "PkgID": "PKG-1",
                "HHPN": "HH-1",
                "LotCode": "LOT-1",
                "measurement_result": "OK",
                "reel_diameter": 180,
                "reel_thickness": 16,
            },
        },
    )
    request = await _build_plugin_dispatch_request(
        object(), inbox=inbox, session=session, workline=SimpleNamespace(id=3), snapshot=snapshot
    )

    class Gateway:
        calls = 0

        async def execute(self, *_args: object) -> GatewayQueryResult:
            self.calls += 1
            raise AssertionError("粗分机 COMMAND_RESULT 必须消费 SCAN 阶段持久化的 Q19，不得回退调用旧 Q14")

    gateway = Gateway()
    write_set = await GeneratedPluginAttemptRunner().run(
        PluginAttemptContext(
            attempt_id="lease-1",
            inbox_id=1,
            session_id=2,
            workline_id=3,
            event_type="COMMAND_RESULT",
            payload=inbox.payload_json,
            plugin_state=session.plugin_state_json,
            snapshot=snapshot,
            runtime=SimpleNamespace(gateway=gateway),
            dispatch_request=request,
        )
    )

    assert gateway.calls == 0
    assert write_set.evidence == ()
    assert write_set.outcome_code == "MOVE_FORWARD_PERSISTED"
    assert len(write_set.intents) == 1
    assert write_set.intents[0].kind is RuntimeIntentKind.SYSTEM_CAPABILITY
    assert write_set.intents[0].capability_key == "device.device_command_write"
    assert write_set.intents[0].payload_json["result_policy"] == "COMMAND_RESULT"


def test_locked_writeback_revalidates_generated_definition_identity_without_transient_attribute() -> None:
    expected = AttemptSnapshot(
        processor_token="lease-1",
        session_version=7,
        plugin_state_version=2,
        definition_identity=ROUGH_SORTER_DEFINITION.identity,
        binding_id=17,
        binding_version=4,
        plugin_config_hash="c" * 64,
        index_digest=WORKLINE_PLUGIN_INDEX_DIGEST,
    )
    locked = SimpleNamespace(
        inbox=SimpleNamespace(processor_token="lease-1"),
        session=SimpleNamespace(
            version=7,
            plugin_state_version=2,
            plugin_key="rough_sorter",
            contract_version="rough_sorter.v2",
            plugin_binding_id=17,
            plugin_binding_version=4,
            plugin_config_hash="c" * 64,
            plugin_index_digest=WORKLINE_PLUGIN_INDEX_DIGEST,
        ),
    )

    assert _authoritative_snapshot_matches(locked, expected) is True
