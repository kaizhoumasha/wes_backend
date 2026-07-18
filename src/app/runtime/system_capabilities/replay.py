"""Recorded replay 解码；重放期间绝不重新调用外部 handler。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.app.runtime.orchestration.repositories.timeline_recorded_replay_repository import (
    TimelineRecordedReplayRepository,
    timeline_recorded_replay_repository,
)
from src.app.runtime.system_capabilities.evidence import QueryEvidence

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class RecordedAttemptAnchor(BaseModel):
    """源 attempt 的权威数值锚点，禁止 replay 根据 intent 反推。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_inbox_id: int = Field(gt=0)
    session_version: int = Field(ge=0)
    session_status: str = Field(min_length=1)
    logical_idempotency_key: str = Field(min_length=1, max_length=300)


class RecordedReplayEnvelope(BaseModel):
    """timeline 中固定的 Definition、binding、index 与决策证据。"""

    model_config = ConfigDict(frozen=True)

    definition_identity: str | None
    binding_identity: str | None
    index_digest: str | None
    attempt_anchor: RecordedAttemptAnchor | None = None
    evidence: tuple[QueryEvidence, ...]
    decision: dict[str, Any]


class RecordedReplayResolution(BaseModel):
    """成功解码或 fail-closed Hold，两者互斥。"""

    model_config = ConfigDict(frozen=True)

    evidence: tuple[QueryEvidence, ...] = ()
    decision: dict[str, Any] | None = None
    binding_identity: str | None = None
    attempt_anchor: RecordedAttemptAnchor | None = None
    hold_reason: str | None = None


def resolve_recorded_replay(
    envelope: RecordedReplayEnvelope,
    *,
    expected_definition_identity: str,
    expected_binding_identity: str,
    expected_index_digest: str,
    expected_source_inbox_id: int,
    expected_evidence_keys: tuple[tuple[str, str, str, str], ...],
) -> RecordedReplayResolution:
    """只解码 recorded 数据；pin 缺失或漂移一律 Hold，不静默升级。"""

    pinned = (envelope.definition_identity, envelope.binding_identity, envelope.index_digest)
    if any(value is None for value in pinned):
        return RecordedReplayResolution(hold_reason="RECORDED_REPLAY_PIN_MISSING")
    expected = (expected_definition_identity, expected_binding_identity, expected_index_digest)
    if pinned != expected:
        return RecordedReplayResolution(hold_reason="RECORDED_REPLAY_PIN_MISMATCH")
    if envelope.attempt_anchor is None or envelope.attempt_anchor.source_inbox_id != expected_source_inbox_id:
        return RecordedReplayResolution(hold_reason="RECORDED_REPLAY_RECORD_INVALID")
    recorded_evidence_keys = tuple(
        (
            item.capability_key,
            item.contract_version,
            item.input_hash,
            item.output_hash,
        )
        for item in envelope.evidence
    )
    if recorded_evidence_keys != expected_evidence_keys:
        return RecordedReplayResolution(hold_reason="RECORDED_REPLAY_EVIDENCE_MISMATCH")
    return RecordedReplayResolution(
        evidence=envelope.evidence,
        decision=envelope.decision,
        binding_identity=envelope.binding_identity,
        attempt_anchor=envelope.attempt_anchor,
    )


class TimelineRecordedReplayService:
    """从 Timeline repository 装载并校验 recorded evidence/decision。"""

    def __init__(self, repository: TimelineRecordedReplayRepository | Any | None = None) -> None:
        self._repository = repository or timeline_recorded_replay_repository

    async def load(
        self,
        db: AsyncSession | Any,
        *,
        source_inbox_id: int,
        expected_definition_identity: str,
        expected_binding_identity: str,
        expected_index_digest: str,
    ) -> RecordedReplayResolution:
        rows = await self._repository.list_recorded_decisions(db, source_inbox_id=source_inbox_id)
        evidence_payloads: list[dict[str, Any]] = []
        decision_payloads: list[dict[str, Any]] = []
        for row in rows:
            payload = getattr(row, "payload_json", None)
            if not isinstance(payload, dict):
                continue
            if payload.get("record_type") == "SYSTEM_CAPABILITY_EVIDENCE" and isinstance(payload.get("evidence"), dict):
                evidence_payloads.append(payload["evidence"])
            elif payload.get("record_type") == "PLUGIN_DECISION":
                decision_payloads.append(payload)
        if len(decision_payloads) != 1:
            return RecordedReplayResolution(hold_reason="RECORDED_REPLAY_RECORD_MISSING")
        decision_record = decision_payloads[0]
        try:
            evidence = tuple(QueryEvidence.model_validate(item) for item in evidence_payloads)
            raw_keys = decision_record.get("evidence_keys")
            if not isinstance(raw_keys, list):
                raise TypeError("evidence_keys missing")
            expected_keys = tuple(_parse_evidence_key(item) for item in raw_keys)
            decision = decision_record.get("decision")
            if not isinstance(decision, dict):
                raise TypeError("decision missing")
            envelope = RecordedReplayEnvelope(
                definition_identity=decision_record.get("definition_identity"),
                binding_identity=decision_record.get("binding_identity"),
                index_digest=decision_record.get("index_digest"),
                attempt_anchor=decision_record.get("attempt_anchor"),
                evidence=evidence,
                decision=decision,
            )
        except (TypeError, ValidationError, ValueError):
            return RecordedReplayResolution(hold_reason="RECORDED_REPLAY_RECORD_INVALID")
        return resolve_recorded_replay(
            envelope,
            expected_definition_identity=expected_definition_identity,
            expected_binding_identity=expected_binding_identity,
            expected_index_digest=expected_index_digest,
            expected_source_inbox_id=source_inbox_id,
            expected_evidence_keys=expected_keys,
        )


def _parse_evidence_key(value: Any) -> tuple[str, str, str, str]:
    if not isinstance(value, list | tuple) or len(value) != 4 or not all(isinstance(item, str) for item in value):
        raise ValueError("invalid evidence key")
    return value[0], value[1], value[2], value[3]


__all__ = [
    "RecordedAttemptAnchor",
    "RecordedReplayEnvelope",
    "RecordedReplayResolution",
    "TimelineRecordedReplayService",
    "resolve_recorded_replay",
]
