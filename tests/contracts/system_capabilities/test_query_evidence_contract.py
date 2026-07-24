"""QUERY evidence 与 recorded replay 的跨模块合同。"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest


def evidence_payload() -> dict[str, object]:
    return {
        "capability_key": "wms.lookup",
        "contract_version": "v1",
        "input_hash": "a" * 64,
        "output_hash": "b" * 64,
        "authority": "WMS",
        "source": "master-data",
        "evidence_at": datetime(2026, 7, 17, tzinfo=UTC).isoformat(),
        "source_version": "42",
        "admission_snapshot": {"profile": "provider-contract"},
        "summary": {"found": True},
        "shadow_expected": None,
    }


def test_query_evidence_requires_explicit_shadow_contract_and_serializes_null() -> None:
    from pydantic import ValidationError

    from src.app.runtime.system_capabilities.evidence import QueryEvidence

    payload = evidence_payload()
    payload.pop("shadow_expected")
    with pytest.raises(ValidationError, match="shadow_expected"):
        QueryEvidence.model_validate(payload)

    payload["shadow_expected"] = None
    evidence = QueryEvidence.model_validate(payload)

    assert evidence.payload()["shadow_expected"] is None


def test_evidence_maps_to_existing_decision_made_timeline_payload() -> None:
    from src.app.runtime.orchestration.models.timeline import TimelineActionType
    from src.app.runtime.system_capabilities.evidence import QueryEvidence

    evidence = QueryEvidence.model_validate(evidence_payload())
    timeline = evidence.to_timeline_record()
    assert timeline["action_type"] is TimelineActionType.DECISION_MADE
    assert timeline["payload_json"]["capability_key"] == "wms.lookup"
    assert timeline["payload_json"]["evidence_at"].endswith(("Z", "+00:00"))


def test_recorded_replay_decodes_evidence_and_decision_without_handler() -> None:
    from src.app.runtime.system_capabilities.evidence import QueryEvidence
    from src.app.runtime.system_capabilities.replay import RecordedReplayEnvelope, resolve_recorded_replay

    envelope = RecordedReplayEnvelope(
        definition_identity="plugin.rough-sorter@v1:" + "c" * 64,
        binding_identity="binding:17:3",
        index_digest="d" * 64,
        attempt_anchor={
            "source_inbox_id": 71,
            "session_version": 7,
            "session_status": "RUNNING",
            "logical_idempotency_key": "workline-plugin:test:material:PKG-71:decision",
        },
        evidence=(QueryEvidence.model_validate(evidence_payload()),),
        decision={"outcome_code": "ROUTE_A", "intents": []},
    )
    resolution = resolve_recorded_replay(
        envelope,
        expected_definition_identity=envelope.definition_identity,
        expected_binding_identity=envelope.binding_identity,
        expected_index_digest=envelope.index_digest,
        expected_source_inbox_id=71,
        expected_evidence_keys=(("wms.lookup", "v1", "a" * 64, "b" * 64),),
    )
    assert resolution.hold_reason is None
    assert resolution.decision == {"outcome_code": "ROUTE_A", "intents": []}
    assert len(resolution.evidence) == 1


def test_recorded_replay_rejects_query_key_or_hash_drift_without_calling_handler() -> None:
    from src.app.runtime.system_capabilities.evidence import QueryEvidence
    from src.app.runtime.system_capabilities.replay import RecordedReplayEnvelope, resolve_recorded_replay

    handler_calls = 0

    def forbidden_handler() -> None:
        nonlocal handler_calls
        handler_calls += 1

    envelope = RecordedReplayEnvelope(
        definition_identity="plugin.rough-sorter@v1:" + "c" * 64,
        binding_identity="binding:17:3",
        index_digest="d" * 64,
        attempt_anchor={
            "source_inbox_id": 71,
            "session_version": 7,
            "session_status": "RUNNING",
            "logical_idempotency_key": "workline-plugin:test:material:PKG-71:decision",
        },
        evidence=(QueryEvidence.model_validate(evidence_payload()),),
        decision={"outcome_code": "ROUTE_A", "intents": []},
    )
    resolution = resolve_recorded_replay(
        envelope,
        expected_definition_identity=envelope.definition_identity,
        expected_binding_identity=envelope.binding_identity,
        expected_index_digest=envelope.index_digest,
        expected_source_inbox_id=71,
        expected_evidence_keys=(("wms.lookup", "v1", "f" * 64, "b" * 64),),
    )

    assert resolution.hold_reason == "RECORDED_REPLAY_EVIDENCE_MISMATCH"
    assert handler_calls == 0
    assert forbidden_handler is not None


def test_recorded_replay_missing_or_mismatched_pin_fails_closed_to_hold() -> None:
    from src.app.runtime.system_capabilities.replay import RecordedReplayEnvelope, resolve_recorded_replay

    envelope = RecordedReplayEnvelope(
        definition_identity=None,
        binding_identity=None,
        index_digest=None,
        evidence=(),
        decision={"outcome_code": "ROUTE_A"},
    )
    resolution = resolve_recorded_replay(
        envelope,
        expected_definition_identity="plugin.rough-sorter@v1:" + "c" * 64,
        expected_binding_identity="binding:17:3",
        expected_index_digest="d" * 64,
        expected_source_inbox_id=71,
        expected_evidence_keys=(),
    )
    assert resolution.decision is None
    assert resolution.hold_reason == "RECORDED_REPLAY_PIN_MISSING"


@pytest.mark.asyncio
async def test_recorded_replay_service_loads_only_timeline_decision_records() -> None:
    from src.app.runtime.system_capabilities.replay import TimelineRecordedReplayService

    evidence = evidence_payload()
    rows = [
        SimpleNamespace(
            payload_json={
                "record_type": "SYSTEM_CAPABILITY_EVIDENCE",
                "evidence": evidence,
            }
        ),
        SimpleNamespace(
            payload_json={
                "record_type": "PLUGIN_DECISION",
                "definition_identity": "plugin.rough-sorter@v1:" + "c" * 64,
                "binding_identity": "binding:17:3",
                "index_digest": "d" * 64,
                "attempt_anchor": {
                    "source_inbox_id": 71,
                    "session_version": 7,
                    "session_status": "RUNNING",
                    "logical_idempotency_key": "workline-plugin:test:material:PKG-71:decision",
                },
                "evidence_keys": [["wms.lookup", "v1", "a" * 64, "b" * 64]],
                "decision": {"outcome_code": "ROUTE_A", "intents": []},
            }
        ),
    ]

    class Repository:
        async def list_recorded_decisions(self, _db: object, *, source_inbox_id: int) -> list[object]:
            assert source_inbox_id == 71
            return rows

    resolution = await TimelineRecordedReplayService(Repository()).load(
        object(),
        source_inbox_id=71,
        expected_definition_identity="plugin.rough-sorter@v1:" + "c" * 64,
        expected_binding_identity="binding:17:3",
        expected_index_digest="d" * 64,
    )

    assert resolution.hold_reason is None
    assert resolution.decision == {"outcome_code": "ROUTE_A", "intents": []}


@pytest.mark.asyncio
async def test_recorded_replay_service_fails_closed_when_legacy_record_has_no_attempt_anchor() -> None:
    from src.app.runtime.system_capabilities.replay import TimelineRecordedReplayService

    class Repository:
        async def list_recorded_decisions(self, _db: object, *, source_inbox_id: int) -> list[object]:
            return [
                SimpleNamespace(
                    payload_json={
                        "record_type": "PLUGIN_DECISION",
                        "definition_identity": "plugin.rough-sorter@v1:" + "c" * 64,
                        "binding_identity": "binding:17:3",
                        "index_digest": "d" * 64,
                        "evidence_keys": [],
                        "decision": {
                            "outcome_code": "HOLD",
                            "hold_reason": None,
                            "intents": [],
                            "next_state": {},
                        },
                    }
                )
            ]

    resolution = await TimelineRecordedReplayService(Repository()).load(
        object(),
        source_inbox_id=71,
        expected_definition_identity="plugin.rough-sorter@v1:" + "c" * 64,
        expected_binding_identity="binding:17:3",
        expected_index_digest="d" * 64,
    )

    assert resolution.decision is None
    assert resolution.hold_reason == "RECORDED_REPLAY_RECORD_INVALID"


@pytest.mark.asyncio
async def test_recorded_replay_service_missing_timeline_record_fails_closed() -> None:
    from src.app.runtime.system_capabilities.replay import TimelineRecordedReplayService

    class Repository:
        async def list_recorded_decisions(self, _db: object, *, source_inbox_id: int) -> list[object]:
            _ = source_inbox_id
            return []

    resolution = await TimelineRecordedReplayService(Repository()).load(
        object(),
        source_inbox_id=71,
        expected_definition_identity="plugin.rough-sorter@v1:" + "c" * 64,
        expected_binding_identity="binding:17:3",
        expected_index_digest="d" * 64,
    )

    assert resolution.decision is None
    assert resolution.hold_reason == "RECORDED_REPLAY_RECORD_MISSING"
