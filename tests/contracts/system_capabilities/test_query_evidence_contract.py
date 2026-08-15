"""QUERY evidence 跨模块合同。"""

from __future__ import annotations

from datetime import UTC, datetime

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
        "admission_snapshot": {
            "profile": "provider-contract",
            "budget": {"timeout_ms": 500, "max_output_bytes": 4096},
        },
        "summary": {"found": True},
    }


def test_query_evidence_rejects_removed_shadow_expected_field() -> None:
    from pydantic import ValidationError

    from src.app.runtime.system_capabilities.evidence import QueryEvidence

    payload = evidence_payload()
    payload["shadow_expected"] = None
    with pytest.raises(ValidationError, match="shadow_expected"):
        QueryEvidence.model_validate(payload)


def test_query_evidence_preserves_actual_provenance_budget_and_canonical_hashes() -> None:
    from src.app.runtime.system_capabilities.evidence import QueryEvidence

    evidence = QueryEvidence.model_validate(evidence_payload())
    payload = evidence.payload()

    assert payload["input_hash"] == "a" * 64
    assert payload["output_hash"] == "b" * 64
    assert payload["authority"] == "WMS"
    assert payload["source"] == "master-data"
    assert payload["source_version"] == "42"
    assert payload["admission_snapshot"]["budget"] == {
        "timeout_ms": 500,
        "max_output_bytes": 4096,
    }
    assert "shadow_expected" not in payload


def test_evidence_maps_to_existing_decision_made_timeline_payload() -> None:
    from src.app.runtime.orchestration.models.timeline import TimelineActionType
    from src.app.runtime.system_capabilities.evidence import QueryEvidence

    evidence = QueryEvidence.model_validate(evidence_payload())
    timeline = evidence.to_timeline_record()
    assert timeline["action_type"] is TimelineActionType.DECISION_MADE
    assert timeline["payload_json"]["capability_key"] == "wms.lookup"
    assert timeline["payload_json"]["evidence_at"].endswith(("Z", "+00:00"))
