from __future__ import annotations

from datetime import datetime

import pytest

from src.app.execution.models import InboundEvidence, InboundEvidenceApplyStatus, InboundEvidenceKind
from src.app.transport.debug_run_evidence import (
    Scan12EvidenceDisposition,
    evaluate_scan12_evidence,
)

NOT_BEFORE_MS = 1_725_000_000_000
SELECTED_BINS = frozenset({"A000001922", "A000002653"})


def _evidence(**changes: object) -> InboundEvidence:
    values: dict[str, object] = {
        "id": 101,
        "kind": InboundEvidenceKind.DEVICE_EVENT,
        "source_identity": "SCAN12-EVENT-101",
        "payload_digest": "a" * 64,
        "normalized_payload": {
            "device_code": "SCAN12",
            "contract_key": "device.event",
            "contract_version": "1.0",
            "event_type": "SCAN_COMPLETED",
            "timestamp": NOT_BEFORE_MS,
            "source_event_id": "SCAN12-EVENT-101",
            "is_debug": True,
            "data": {"barcode": "A000001922"},
        },
        "received_at": datetime(2026, 9, 2),
        "device_code": "SCAN12",
        "contract_key": "device.event",
        "contract_version": "1.0",
        "apply_status": InboundEvidenceApplyStatus.APPLIED,
    }
    values.update(changes)
    return InboundEvidence(**values)


def _evaluate(evidence: InboundEvidence):
    return evaluate_scan12_evidence(
        evidence,
        not_before_ms=NOT_BEFORE_MS,
        selected_bins=SELECTED_BINS,
    )


@pytest.mark.parametrize(
    "apply_status",
    [InboundEvidenceApplyStatus.APPLIED, InboundEvidenceApplyStatus.IGNORED],
)
def test_scan12_accepts_processed_device_evidence(apply_status: InboundEvidenceApplyStatus) -> None:
    evaluation = _evaluate(_evidence(apply_status=apply_status))

    assert evaluation.disposition is Scan12EvidenceDisposition.MATCH
    assert evaluation.bin_id == "A000001922"
    assert evaluation.evidence_id == 101
    assert evaluation.source_event_id == "SCAN12-EVENT-101"
    assert evaluation.reason_code is None


@pytest.mark.parametrize(
    ("evidence", "reason_code"),
    [
        (
            _evidence(
                normalized_payload={
                    **_evidence().normalized_payload,
                    "timestamp": NOT_BEFORE_MS - 1,
                }
            ),
            "BEFORE_NOT_BEFORE",
        ),
        (_evidence(kind=InboundEvidenceKind.DEVICE_RESULT), "NOT_DEVICE_EVENT"),
        (
            _evidence(
                device_code="SCAN13",
                source_identity="SCAN13-EVENT-101",
                normalized_payload={
                    **_evidence().normalized_payload,
                    "device_code": "SCAN13",
                    "source_event_id": "SCAN13-EVENT-101",
                },
            ),
            "OTHER_DEVICE",
        ),
        (
            _evidence(normalized_payload={**_evidence().normalized_payload, "event_type": "HEARTBEAT"}),
            "OTHER_EVENT",
        ),
        (
            _evidence(normalized_payload={**_evidence().normalized_payload, "data": {"barcode": "OTHER-BIN"}}),
            "UNSELECTED_BIN",
        ),
    ],
)
def test_scan12_ignores_evidence_that_cannot_advance_the_current_face(
    evidence: InboundEvidence,
    reason_code: str,
) -> None:
    evaluation = _evaluate(evidence)

    assert evaluation.disposition is Scan12EvidenceDisposition.IGNORE
    assert evaluation.reason_code == reason_code


def test_scan12_accepts_a_late_commit_even_when_its_id_is_below_the_recorded_boundary() -> None:
    evaluation = _evaluate(_evidence(id=100))

    assert evaluation.disposition is Scan12EvidenceDisposition.MATCH


@pytest.mark.parametrize(
    ("evidence", "reason_code"),
    [
        (_evidence(apply_status=InboundEvidenceApplyStatus.RECONCILING), "EVIDENCE_RECONCILING"),
        (
            _evidence(
                normalized_payload={
                    key: value for key, value in _evidence().normalized_payload.items() if key != "timestamp"
                }
            ),
            "INVALID_NORMALIZED_PAYLOAD",
        ),
        (
            _evidence(normalized_payload={**_evidence().normalized_payload, "data": {"barcode": 123}}),
            "INVALID_BARCODE",
        ),
        (
            _evidence(source_identity="DIFFERENT-EVENT"),
            "SOURCE_IDENTITY_CONFLICT",
        ),
        (
            _evidence(device_code="SCAN13"),
            "DEVICE_IDENTITY_CONFLICT",
        ),
        (
            _evidence(contract_key="other.contract"),
            "CONTRACT_KEY_CONFLICT",
        ),
        (
            _evidence(contract_version="2.0"),
            "CONTRACT_VERSION_CONFLICT",
        ),
    ],
)
def test_scan12_fails_closed_for_ambiguous_or_unprocessed_evidence(
    evidence: InboundEvidence,
    reason_code: str,
) -> None:
    evaluation = _evaluate(evidence)

    assert evaluation.disposition is Scan12EvidenceDisposition.ATTENTION
    assert evaluation.reason_code == reason_code


def test_scan12_waits_for_pending_selected_evidence_without_consuming_it() -> None:
    evaluation = _evaluate(_evidence(apply_status=InboundEvidenceApplyStatus.PENDING))

    assert evaluation.disposition is Scan12EvidenceDisposition.WAIT
    assert evaluation.reason_code == "EVIDENCE_NOT_PROCESSED"
    assert evaluation.evidence_id == 101


@pytest.mark.parametrize(
    "evidence",
    [
        _evidence(
            apply_status=InboundEvidenceApplyStatus.PENDING,
            device_code="SCAN13",
            source_identity="SCAN13-EVENT-101",
            normalized_payload={
                **_evidence().normalized_payload,
                "device_code": "SCAN13",
                "source_event_id": "SCAN13-EVENT-101",
            },
        ),
        _evidence(
            apply_status=InboundEvidenceApplyStatus.PENDING,
            normalized_payload={**_evidence().normalized_payload, "event_type": "HEARTBEAT"},
        ),
    ],
)
def test_scan12_ignores_unprocessed_evidence_that_is_definitely_unrelated(
    evidence: InboundEvidence,
) -> None:
    evaluation = _evaluate(evidence)

    assert evaluation.disposition is Scan12EvidenceDisposition.IGNORE
