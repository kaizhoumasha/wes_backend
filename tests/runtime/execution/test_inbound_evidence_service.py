"""InboundEvidence 统一设备和 WMS 入站证据的稳定身份。"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.app.execution.models.inbound_evidence import (
    InboundEvidence,
    InboundEvidenceConflict,
    InboundEvidenceKind,
)
from src.app.execution.services.inbound_evidence_service import (
    InboundEvidenceConflictResult,
    InboundEvidenceIdentityConflictError,
    InboundEvidenceService,
)


class FakeInboundEvidenceRepository:
    def __init__(self) -> None:
        self.evidences: dict[str, InboundEvidence] = {}
        self.conflicts: list[InboundEvidenceConflict] = []

    async def lock_source_identity(self, _db: object, source_identity: str) -> None:
        return None

    async def get_by_source_identity_for_update(
        self,
        _db: object,
        source_identity: str,
    ) -> InboundEvidence | None:
        return self.evidences.get(source_identity)

    async def add(self, _db: object, evidence: InboundEvidence) -> InboundEvidence:
        evidence.id = len(self.evidences) + 1
        self.evidences[evidence.source_identity] = evidence
        return evidence

    async def add_conflict(
        self,
        _db: object,
        conflict: InboundEvidenceConflict,
    ) -> InboundEvidenceConflict:
        self.conflicts.append(conflict)
        return conflict


@pytest.mark.asyncio
async def test_same_source_identity_and_normalized_payload_is_idempotent() -> None:
    repository = FakeInboundEvidenceRepository()
    service = InboundEvidenceService(repository=repository)

    first = await service.accept(
        object(),
        kind=InboundEvidenceKind.WMS_RESULT,
        source_identity="inbound.material.admission_decide@v1:OP-001",
        normalized_payload={"result": "WAIT", "data": {"reason_code": "BUSY"}},
        received_at=datetime(2026, 8, 16),
        operation="inbound.material.admission_decide@v1",
        operation_id="OP-001",
        material_execution_id=21,
    )
    duplicate = await service.accept(
        object(),
        kind=InboundEvidenceKind.WMS_RESULT,
        source_identity="inbound.material.admission_decide@v1:OP-001",
        normalized_payload={"data": {"reason_code": "BUSY"}, "result": "WAIT"},
        received_at=datetime(2026, 8, 16, 0, 1),
        operation="inbound.material.admission_decide@v1",
        operation_id="OP-001",
        material_execution_id=21,
    )

    assert first.duplicate is False
    assert duplicate.duplicate is True
    assert duplicate.evidence is first.evidence
    assert first.evidence.kind == InboundEvidenceKind.WMS_RESULT
    assert first.evidence.normalized_payload == {"result": "WAIT", "data": {"reason_code": "BUSY"}}


@pytest.mark.asyncio
async def test_same_source_identity_with_different_digest_records_conflict() -> None:
    repository = FakeInboundEvidenceRepository()
    service = InboundEvidenceService(repository=repository)
    await service.accept(
        object(),
        kind=InboundEvidenceKind.DEVICE_EVENT,
        source_identity="SCAN-001",
        normalized_payload={"source_event_id": "SCAN-001", "data": {"shape_result": "PASS"}},
        received_at=datetime(2026, 8, 16),
        device_code="MEASURE-01",
        line_run_epoch_id=11,
    )

    conflict_result = await service.accept(
        object(),
        kind=InboundEvidenceKind.DEVICE_EVENT,
        source_identity="SCAN-001",
        normalized_payload={"source_event_id": "SCAN-001", "data": {"shape_result": "FAIL"}},
        received_at=datetime(2026, 8, 16, 0, 1),
        device_code="MEASURE-01",
        line_run_epoch_id=11,
    )

    assert isinstance(conflict_result, InboundEvidenceConflictResult)
    assert isinstance(conflict_result.to_exception(), InboundEvidenceIdentityConflictError)
    assert len(repository.evidences) == 1
    assert repository.conflicts[0].source_identity == "SCAN-001"
    assert repository.conflicts[0].conflicting_digest != repository.evidences["SCAN-001"].payload_digest


@pytest.mark.asyncio
async def test_same_payload_cannot_rebind_source_identity_to_another_execution() -> None:
    repository = FakeInboundEvidenceRepository()
    service = InboundEvidenceService(repository=repository)
    payload = {"result": "WAIT", "data": {"reason_code": "BUSY"}}
    await service.accept(
        object(),
        kind=InboundEvidenceKind.WMS_RESULT,
        source_identity="inbound.material.admission_decide@v1:OP-001",
        normalized_payload=payload,
        received_at=datetime(2026, 8, 16),
        operation="inbound.material.admission_decide@v1",
        operation_id="OP-001",
        material_execution_id=21,
    )

    conflict_result = await service.accept(
        object(),
        kind=InboundEvidenceKind.WMS_RESULT,
        source_identity="inbound.material.admission_decide@v1:OP-001",
        normalized_payload=payload,
        received_at=datetime(2026, 8, 16, 0, 1),
        operation="inbound.material.admission_decide@v1",
        operation_id="OP-001",
        material_execution_id=22,
    )

    assert isinstance(conflict_result, InboundEvidenceConflictResult)
    assert repository.conflicts[0].reason_code == "SOURCE_IDENTITY_CORRELATION_CONFLICT"


@pytest.mark.asyncio
async def test_wms_source_identity_must_equal_operation_plus_operation_id() -> None:
    service = InboundEvidenceService(repository=FakeInboundEvidenceRepository())

    with pytest.raises(ValueError, match=r"operation.*operation_id"):
        await service.accept(
            object(),
            kind=InboundEvidenceKind.WMS_EVENT,
            source_identity="OP-001",
            normalized_payload={"decision": "ABORT"},
            received_at=datetime(2026, 8, 16),
            operation="inbound.execution.reconciliation_decided@v1",
            operation_id="OP-001",
        )


def test_kind_is_closed_and_raw_supplier_payload_is_not_an_owner() -> None:
    assert {kind.value for kind in InboundEvidenceKind} == {
        "DEVICE_EVENT",
        "DEVICE_RESULT",
        "WMS_EVENT",
        "WMS_RESULT",
    }
    assert "raw_payload" not in InboundEvidence.model_fields
    assert "normalized_payload" in InboundEvidence.model_fields


def test_evidence_has_a_separate_durable_decision_application_lease() -> None:
    assert {
        "decision_digest",
        "decision_attempt_count",
        "decision_next_attempt_at",
        "decision_claim_token",
        "decision_claim_expires_at",
    } <= InboundEvidence.model_fields.keys()
