"""自动联调流程对中性设备 Evidence 的窄解析边界。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import ValidationError

from src.app.device.contracts import EcsDeviceEvent
from src.app.execution.models import InboundEvidence, InboundEvidenceApplyStatus, InboundEvidenceKind

_SCAN12_DIRECTION_SUFFIXES = frozenset({"-A", "-B", "-C", "-D"})
_SCAN12_DEVICE_CODES = frozenset({"SCAN12", "STATION_SCAN12"})


class Scan12EvidenceDisposition(StrEnum):
    MATCH = "MATCH"
    WAIT = "WAIT"
    IGNORE = "IGNORE"
    ATTENTION = "ATTENTION"


@dataclass(frozen=True, slots=True)
class Scan12EvidenceEvaluation:
    disposition: Scan12EvidenceDisposition
    evidence_id: int | None = None
    source_event_id: str | None = None
    bin_id: str | None = None
    reason_code: str | None = None


def evaluate_scan12_evidence(  # noqa: PLR0911 - each closed Evidence disposition exits immediately
    evidence: InboundEvidence,
    *,
    not_before_ms: int,
    selected_bins: frozenset[str],
) -> Scan12EvidenceEvaluation:
    boundary_result = _evaluate_evidence_boundary(evidence)
    if boundary_result is not None:
        return boundary_result

    evidence_id = evidence.id
    if evidence_id is None:
        return _attention(None, "EVIDENCE_ID_MISSING")
    event, parse_result = _parse_event(evidence, evidence_id)
    if parse_result is not None:
        return parse_result
    if event is None:
        return _attention(evidence_id, "INVALID_NORMALIZED_PAYLOAD")

    if event.device_code not in _SCAN12_DEVICE_CODES:
        return _ignore(evidence_id, "OTHER_DEVICE", source_event_id=event.source_event_id)
    if event.event_type != "SCAN_COMPLETED":
        return _ignore(evidence_id, "OTHER_EVENT", source_event_id=event.source_event_id)
    if event.timestamp < not_before_ms:
        return _ignore(evidence_id, "BEFORE_NOT_BEFORE", source_event_id=event.source_event_id)

    barcode = event.data.get("barcode")
    if not isinstance(barcode, str) or not barcode:
        return _attention(evidence_id, "INVALID_BARCODE", source_event_id=event.source_event_id)
    bin_id = _match_selected_bin_id(barcode, selected_bins)
    if bin_id is None:
        return _ignore(
            evidence_id,
            "UNSELECTED_BIN",
            source_event_id=event.source_event_id,
            bin_id=barcode,
        )
    apply_result = _evaluate_apply_status(evidence, evidence_id, event.source_event_id)
    if apply_result is not None:
        return apply_result
    return Scan12EvidenceEvaluation(
        disposition=Scan12EvidenceDisposition.MATCH,
        evidence_id=evidence_id,
        source_event_id=event.source_event_id,
        bin_id=bin_id,
    )


def _match_selected_bin_id(barcode: str, selected_bins: frozenset[str]) -> str | None:
    if barcode in selected_bins:
        return barcode
    if len(barcode) > 2 and barcode[-2:] in _SCAN12_DIRECTION_SUFFIXES:
        candidate = barcode[:-2]
        if candidate in selected_bins:
            return candidate
    return None


def _evaluate_evidence_boundary(
    evidence: InboundEvidence,
) -> Scan12EvidenceEvaluation | None:
    evidence_id = evidence.id
    if evidence_id is None:
        return _attention(None, "EVIDENCE_ID_MISSING")
    try:
        kind = InboundEvidenceKind(evidence.kind)
    except ValueError:
        return _attention(evidence_id, "INVALID_EVIDENCE_KIND")
    if kind is not InboundEvidenceKind.DEVICE_EVENT:
        return _ignore(evidence_id, "NOT_DEVICE_EVENT")
    return None


def _evaluate_apply_status(
    evidence: InboundEvidence,
    evidence_id: int,
    source_event_id: str,
) -> Scan12EvidenceEvaluation | None:
    try:
        apply_status = InboundEvidenceApplyStatus(evidence.apply_status)
    except ValueError:
        return _attention(evidence_id, "INVALID_APPLY_STATUS", source_event_id=source_event_id)
    if apply_status is InboundEvidenceApplyStatus.RECONCILING:
        return _attention(evidence_id, "EVIDENCE_RECONCILING", source_event_id=source_event_id)
    if apply_status is InboundEvidenceApplyStatus.PENDING:
        return Scan12EvidenceEvaluation(
            disposition=Scan12EvidenceDisposition.WAIT,
            evidence_id=evidence_id,
            source_event_id=source_event_id,
            reason_code="EVIDENCE_NOT_PROCESSED",
        )
    return None


def _parse_event(
    evidence: InboundEvidence,
    evidence_id: int,
) -> tuple[EcsDeviceEvent | None, Scan12EvidenceEvaluation | None]:
    try:
        event = EcsDeviceEvent.model_validate(evidence.normalized_payload)
    except ValidationError:
        return None, _attention(evidence_id, "INVALID_NORMALIZED_PAYLOAD")
    if evidence.source_identity != event.source_event_id:
        return None, _attention(evidence_id, "SOURCE_IDENTITY_CONFLICT", source_event_id=event.source_event_id)
    if evidence.device_code != event.device_code:
        return None, _attention(evidence_id, "DEVICE_IDENTITY_CONFLICT", source_event_id=event.source_event_id)
    if evidence.contract_key != event.contract_key:
        return None, _attention(evidence_id, "CONTRACT_KEY_CONFLICT", source_event_id=event.source_event_id)
    if evidence.contract_version != event.contract_version:
        return None, _attention(evidence_id, "CONTRACT_VERSION_CONFLICT", source_event_id=event.source_event_id)
    return event, None


def _ignore(
    evidence_id: int,
    reason_code: str,
    *,
    source_event_id: str | None = None,
    bin_id: str | None = None,
) -> Scan12EvidenceEvaluation:
    return Scan12EvidenceEvaluation(
        disposition=Scan12EvidenceDisposition.IGNORE,
        evidence_id=evidence_id,
        source_event_id=source_event_id,
        bin_id=bin_id,
        reason_code=reason_code,
    )


def _attention(
    evidence_id: int | None,
    reason_code: str,
    *,
    source_event_id: str | None = None,
) -> Scan12EvidenceEvaluation:
    return Scan12EvidenceEvaluation(
        disposition=Scan12EvidenceDisposition.ATTENTION,
        evidence_id=evidence_id,
        source_event_id=source_event_id,
        reason_code=reason_code,
    )


__all__ = [
    "Scan12EvidenceDisposition",
    "Scan12EvidenceEvaluation",
    "evaluate_scan12_evidence",
]
