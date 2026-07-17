"""Recorded replay 解码；重放期间绝不重新调用外部 handler。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from src.app.runtime.system_capabilities.evidence import QueryEvidence  # noqa: TC001  # Pydantic schema 运行时依赖


class RecordedReplayEnvelope(BaseModel):
    """timeline 中固定的 Definition、binding、index 与决策证据。"""

    model_config = ConfigDict(frozen=True)

    definition_identity: str | None
    binding_identity: str | None
    index_digest: str | None
    evidence: tuple[QueryEvidence, ...]
    decision: dict[str, Any]


class RecordedReplayResolution(BaseModel):
    """成功解码或 fail-closed Hold，两者互斥。"""

    model_config = ConfigDict(frozen=True)

    evidence: tuple[QueryEvidence, ...] = ()
    decision: dict[str, Any] | None = None
    hold_reason: str | None = None


def resolve_recorded_replay(
    envelope: RecordedReplayEnvelope,
    *,
    expected_definition_identity: str,
    expected_binding_identity: str,
    expected_index_digest: str,
    expected_evidence_keys: tuple[tuple[str, str, str, str], ...],
) -> RecordedReplayResolution:
    """只解码 recorded 数据；pin 缺失或漂移一律 Hold，不静默升级。"""

    pinned = (envelope.definition_identity, envelope.binding_identity, envelope.index_digest)
    if any(value is None for value in pinned):
        return RecordedReplayResolution(hold_reason="RECORDED_REPLAY_PIN_MISSING")
    expected = (expected_definition_identity, expected_binding_identity, expected_index_digest)
    if pinned != expected:
        return RecordedReplayResolution(hold_reason="RECORDED_REPLAY_PIN_MISMATCH")
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
    return RecordedReplayResolution(evidence=envelope.evidence, decision=envelope.decision)


__all__ = ["RecordedReplayEnvelope", "RecordedReplayResolution", "resolve_recorded_replay"]
