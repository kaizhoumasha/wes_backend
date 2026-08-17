"""只从已完成基础验证且关联完整的 evidence 构建 SDK Fact。"""

from __future__ import annotations

import importlib
from datetime import datetime

import pytest
from wes_plugin_sdk import (
    DeviceResultReadyFact,
    EvidenceReadyFact,
    ReconciliationResultReadyFact,
    WmsResultReadyFact,
)

from src.app.execution.models import (
    InboundEvidence,
    InboundEvidenceApplyStatus,
    InboundEvidenceKind,
    MaterialExecution,
    MaterialExecutionStatus,
)


def _builder():
    module = importlib.import_module("src.app.execution.services.fact_builder")
    return module.FactBuilder()


def _execution() -> MaterialExecution:
    return MaterialExecution(
        id=21,
        execution_code="EXEC-001",
        material_trace_id="TRACE-001",
        workline_id=7,
        line_run_epoch_id=11,
        status=MaterialExecutionStatus.RUNNING,
        last_transition_reason="SCAN_ACCEPTED",
        last_transition_evidence_id=30,
        status_changed_at=datetime(2026, 8, 17),
    )


def _evidence(kind: InboundEvidenceKind, **changes: object) -> InboundEvidence:
    values: dict[str, object] = {
        "id": 31,
        "kind": kind,
        "source_identity": "SOURCE-001",
        "payload_digest": "a" * 64,
        "normalized_payload": {"data": {}},
        "received_at": datetime(2026, 8, 17),
        "line_run_epoch_id": 11,
        "material_execution_id": 21,
        "contract_key": "rough_sorter.measurement_device",
        "contract_version": "1.0",
        "apply_status": InboundEvidenceApplyStatus.APPLIED,
    }
    values.update(changes)
    return InboundEvidence(**values)


def test_device_event_builds_a_stable_evidence_ready_fact() -> None:
    fact = _builder().build(_evidence(InboundEvidenceKind.DEVICE_EVENT), _execution())

    assert fact == EvidenceReadyFact(
        fact_id="evidence:31",
        evidence_id="31",
        fact_version="1.0",
        material_execution_id="EXEC-001",
    )


def test_device_result_uses_persisted_correlations_not_supplier_payload_guessing() -> None:
    fact = _builder().build(
        _evidence(
            InboundEvidenceKind.DEVICE_RESULT,
            device_code="ARM-01",
            command_code="CMD-001",
            normalized_payload={"data": {"vendor_trace": "DO-NOT-USE"}},
        ),
        _execution(),
    )

    assert fact == DeviceResultReadyFact(
        fact_id="evidence:31",
        evidence_id="31",
        fact_version="1.0",
        material_execution_id="EXEC-001",
        command_code="CMD-001",
        device_code="ARM-01",
        material_trace_id="TRACE-001",
    )


def test_wms_result_uses_validated_operation_identity() -> None:
    fact = _builder().build(
        _evidence(
            InboundEvidenceKind.WMS_RESULT,
            operation="inbound.material.admission_decide@v1",
            operation_id="019cd8ce-34b7-7000-8000-000000000001",
        ),
        _execution(),
    )

    assert fact == WmsResultReadyFact(
        fact_id="evidence:31",
        evidence_id="31",
        fact_version="1.0",
        material_execution_id="EXEC-001",
        operation_id="019cd8ce-34b7-7000-8000-000000000001",
    )


def test_batch_reconciliation_builds_one_stable_fact_per_frozen_execution_binding() -> None:
    evidence = _evidence(
        InboundEvidenceKind.WMS_EVENT,
        material_execution_id=None,
        line_run_epoch_id=None,
        operation="inbound.execution.reconciliation_decided@v1",
        operation_id="019cd8ce-34b7-7000-8000-000000000001",
        normalized_payload={"data": {"reconciliation_id": "REC-1"}},
    )

    fact = _builder().build_reconciliation(evidence, _execution())

    assert fact == ReconciliationResultReadyFact(
        fact_id="evidence:31:execution:EXEC-001",
        evidence_id="31",
        fact_version="1.0",
        material_execution_id="EXEC-001",
        reconciliation_id="REC-1",
    )


@pytest.mark.parametrize(
    ("evidence", "execution"),
    [
        (_evidence(InboundEvidenceKind.DEVICE_EVENT, apply_status=InboundEvidenceApplyStatus.PENDING), _execution()),
        (_evidence(InboundEvidenceKind.DEVICE_EVENT, material_execution_id=22), _execution()),
        (_evidence(InboundEvidenceKind.DEVICE_EVENT, line_run_epoch_id=12), _execution()),
    ],
)
def test_unapplied_or_mismatched_evidence_fails_closed(
    evidence: InboundEvidence,
    execution: MaterialExecution,
) -> None:
    with pytest.raises(ValueError):
        _builder().build(evidence, execution)
