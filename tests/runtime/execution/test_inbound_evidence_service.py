"""InboundEvidence 统一设备和 WMS 入站证据的稳定身份。"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest

from src.app.execution.models.inbound_evidence import (
    InboundEvidence,
    InboundEvidenceApplyStatus,
    InboundEvidenceConflict,
    InboundEvidenceKind,
)
from src.app.execution.repositories.inbound_evidence_repository import InboundEvidenceRepository
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
async def test_exact_device_registration_leaves_unrelated_and_rejected_history_quiet(db_session):
    repository = InboundEvidenceRepository()
    suffix = uuid4().hex
    records = []
    for name, overrides in [
        ("match", {}),
        ("other-command", {"command_code": "other-command"}),
        ("other-device", {"device_code": "other-device"}),
        ("other-contract", {"contract_version": "unrecognized"}),
        ("processed", {"processed_at": datetime(2026, 9, 1)}),
    ]:
        values = {
            "kind": InboundEvidenceKind.DEVICE_RESULT,
            "source_identity": f"RESULT:{suffix}:{name}",
            "device_code": "device-match",
            "command_code": "command-match",
            "contract_key": "third_party_integration",
            "contract_version": "1.1",
            "apply_status": InboundEvidenceApplyStatus.IGNORED,
            "received_at": datetime(2026, 9, 1),
            "normalized_payload": {"message": name},
            "payload_digest": "a" * 64,
        }
        values.update(overrides)
        records.append(await repository.add(db_session, InboundEvidence(**values)))
    assert (
        await repository.requeue_unassociated_device_results(
            db_session,
            command_code="command-match",
            device_code="device-match",
            workline_id=None,
            material_execution_id=None,
            contract_key="arm.pick",
            contract_version="2.0",
            source_contract_key="third_party_integration",
            source_contract_version="1.1",
        )
        == 1
    )
    assert records[0].apply_status == InboundEvidenceApplyStatus.PENDING
    assert records[0].contract_key == "arm.pick"
    assert records[0].normalized_payload == {"message": "match"}
    assert records[0].payload_digest == "a" * 64
    assert all(record.apply_status == InboundEvidenceApplyStatus.IGNORED for record in records[1:])
    claimed = await repository.claim_next_pending(db_session, kinds=(InboundEvidenceKind.DEVICE_RESULT,))
    assert claimed.id == records[0].id
    await repository.mark_reconciling(db_session, claimed, processed_at=datetime(2026, 9, 2))
    assert await repository.claim_next_pending(db_session, kinds=(InboundEvidenceKind.DEVICE_RESULT,)) is None


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
        workline_id=11,
    )

    conflict_result = await service.accept(
        object(),
        kind=InboundEvidenceKind.DEVICE_EVENT,
        source_identity="SCAN-001",
        normalized_payload={"source_event_id": "SCAN-001", "data": {"shape_result": "FAIL"}},
        received_at=datetime(2026, 8, 16, 0, 1),
        device_code="MEASURE-01",
        workline_id=11,
    )

    assert isinstance(conflict_result, InboundEvidenceConflictResult)
    assert isinstance(conflict_result.to_exception(), InboundEvidenceIdentityConflictError)
    assert len(repository.evidences) == 1
    assert repository.conflicts[0].source_identity == "SCAN-001"
    assert repository.conflicts[0].conflicting_digest != repository.evidences["SCAN-001"].payload_digest


@pytest.mark.asyncio
async def test_device_observation_reuses_stable_identity_without_faking_result() -> None:
    repository = FakeInboundEvidenceRepository()
    service = InboundEvidenceService(repository=repository)
    values = {
        "command_code": "CMD-001",
        "device_code": "ARM-01",
        "observation": "RESULT_UNKNOWN",
        "reason_code": "DELIVERY_UNKNOWN",
        "observed_at": datetime(2026, 8, 16),
        "received_at": datetime(2026, 8, 16, 0, 0, 1),
        "workline_id": 11,
        "material_execution_id": 21,
        "contract_key": "rough-sorter-device",
        "contract_version": "1.1",
    }

    first = await service.record_device_observation(object(), **values)  # type: ignore[arg-type]
    duplicate = await service.record_device_observation(object(), **values)  # type: ignore[arg-type]

    assert first.duplicate is False
    assert duplicate.duplicate is True
    assert duplicate.evidence is first.evidence
    assert first.evidence.source_identity == "device:CMD-001:observation:RESULT_UNKNOWN"
    assert first.evidence.kind == InboundEvidenceKind.DEVICE_OBSERVATION
    assert first.evidence.normalized_payload == {
        "command_code": "CMD-001",
        "device_code": "ARM-01",
        "observation": "RESULT_UNKNOWN",
        "observed_at": "2026-08-16T00:00:00",
        "reason_code": "DELIVERY_UNKNOWN",
    }
    assert "result" not in first.evidence.normalized_payload


@pytest.mark.asyncio
async def test_device_observation_rejects_same_identity_payload_drift() -> None:
    repository = FakeInboundEvidenceRepository()
    service = InboundEvidenceService(repository=repository)
    values = {
        "command_code": "CMD-001",
        "device_code": "ARM-01",
        "observation": "RESULT_UNKNOWN",
        "observed_at": datetime(2026, 8, 16),
        "received_at": datetime(2026, 8, 16, 0, 0, 1),
        "workline_id": 11,
        "material_execution_id": 21,
        "contract_key": "rough-sorter-device",
        "contract_version": "1.1",
    }
    await service.record_device_observation(object(), reason_code="DELIVERY_UNKNOWN", **values)  # type: ignore[arg-type]

    with pytest.raises(InboundEvidenceIdentityConflictError):
        await service.record_device_observation(  # type: ignore[arg-type]
            object(),
            reason_code="ACK_DEADLINE_EXPIRED",
            **values,
        )

    assert len(repository.evidences) == 1
    assert len(repository.conflicts) == 1


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
            operation="inbound.execution.recovery_decided@v1",
            operation_id="OP-001",
        )


@pytest.mark.asyncio
async def test_transport_evidence_requires_frozen_task_and_version_identity() -> None:
    service = InboundEvidenceService(repository=FakeInboundEvidenceRepository())
    accepted = await service.accept(
        object(),
        kind=InboundEvidenceKind.TRANSPORT_RESULT,
        source_identity="transport:TRANSPORT-1:outcome:2",
        normalized_payload={"transport_task_id": "TRANSPORT-1", "outcome_version": 2, "status": "SUCCEEDED"},
        received_at=datetime(2026, 8, 17),
        workline_id=11,
        material_execution_id=21,
        transport_task_id="TRANSPORT-1",
        contract_key="transport.outcome",
        contract_version="1.0",
        apply_status=InboundEvidenceApplyStatus.APPLIED,
    )

    assert accepted.evidence.transport_task_id == "TRANSPORT-1"
    with pytest.raises(ValueError, match="Transport source_identity"):
        await service.accept(
            object(),
            kind=InboundEvidenceKind.TRANSPORT_RESULT,
            source_identity="transport:OTHER:outcome:2",
            normalized_payload={"outcome_version": 2, "status": "SUCCEEDED"},
            received_at=datetime(2026, 8, 17),
            transport_task_id="TRANSPORT-1",
        )


def test_kind_is_closed_and_raw_supplier_payload_is_not_an_owner() -> None:
    assert {kind.value for kind in InboundEvidenceKind} == {
        "DEVICE_EVENT",
        "DEVICE_OBSERVATION",
        "DEVICE_RESULT",
        "TRANSPORT_RESULT",
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
