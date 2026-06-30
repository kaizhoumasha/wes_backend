"""Phase 3 typed ExternalReference / EvidenceEnvelope contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError


def test_external_reference_and_evidence_envelope_are_typed_models() -> None:
    from src.app.wms_integration.evidence.envelope import EvidenceEnvelope, ExternalReference

    ref = ExternalReference(
        system="WMS",
        object_type="PKG",
        code="PKG-001",
        schema_version="wms.pkg.v1",
        validated_at="2026-07-01T02:00:00Z",
        source_version="wms-42",
    )
    envelope = EvidenceEnvelope(
        schema_version="evidence.v1",
        source_system="WMS",
        source_event_id="EVT-001",
        source_version="wms-42",
        evidence_type="PKG_BOUND",
        occurred_at="2026-07-01T02:00:00Z",
        external_refs=[ref],
        request_hash="b" * 64,
        payload_hash="a" * 64,
        payload={"pkg_code": "PKG-001"},
    )

    assert envelope.external_refs[0].code == "PKG-001"
    assert envelope.request_hash == "b" * 64
    assert envelope.payload_hash == "a" * 64


def test_evidence_envelope_rejects_raw_unversioned_external_reference() -> None:
    from src.app.wms_integration.evidence.envelope import EvidenceEnvelope

    with pytest.raises(ValidationError):
        EvidenceEnvelope(
            schema_version="evidence.v1",
            source_system="WMS",
            source_event_id="EVT-001",
            source_version="wms-42",
            evidence_type="PKG_BOUND",
            occurred_at="2026-07-01T02:00:00Z",
            external_refs=[{"system": "WMS", "object_type": "PKG", "code": "PKG-001"}],
            request_hash="b" * 64,
            payload_hash="a" * 64,
            payload={},
        )
