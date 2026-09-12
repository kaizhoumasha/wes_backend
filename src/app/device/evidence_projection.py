"""WES 内部设备 Evidence 诊断投影。"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Protocol

from src.app.device.contracts import DeviceEvidenceKind, DeviceEvidenceUpdate
from src.app.execution.models.inbound_evidence import InboundEvidence, InboundEvidenceApplyStatus
from src.app.sys.services.event_stream_service import DEVICE_EVIDENCE_STREAM_CHANNEL
from src.utils.timezone import timezone

logger = logging.getLogger(__name__)


class DeviceEvidenceEventPublisherPort(Protocol):
    async def publish_to(self, channel: str, event_type: str, payload: dict[str, object]) -> bool: ...


def build_device_evidence_update(
    evidence: InboundEvidence,
    *,
    processed_at: datetime | None = None,
    command_code: str | None = None,
) -> DeviceEvidenceUpdate:
    if evidence.id is None or evidence.device_code is None:
        raise RuntimeError("device evidence 缺少 update snapshot 字段")
    kind = DeviceEvidenceKind(getattr(evidence.kind, "value", evidence.kind))
    payload = evidence.normalized_payload if isinstance(evidence.normalized_payload, dict) else {}
    event_type = payload.get("event_type") if kind is DeviceEvidenceKind.DEVICE_EVENT else None
    observation = payload.get("observation") if kind is DeviceEvidenceKind.DEVICE_OBSERVATION else None
    reason_code = payload.get("reason_code") if kind is DeviceEvidenceKind.DEVICE_OBSERVATION else None
    observed_at = payload.get("observed_at") if kind is DeviceEvidenceKind.DEVICE_OBSERVATION else None
    effective_processed_at = processed_at if processed_at is not None else evidence.processed_at
    return DeviceEvidenceUpdate(
        evidence_id=evidence.id,
        kind=kind,
        source_event_id=evidence.source_identity,
        device_code=evidence.device_code,
        command_code=command_code or evidence.command_code,
        event_type=event_type if isinstance(event_type, str) else None,
        observation=observation if observation in {"NOT_ACCEPTED", "RESULT_UNKNOWN"} else None,
        reason_code=reason_code if isinstance(reason_code, str) else None,
        observed_at=_payload_timestamp(observed_at),
        apply_status=InboundEvidenceApplyStatus(evidence.apply_status).value,
        processed_at=(
            timezone.to_utc(effective_processed_at).isoformat() if effective_processed_at is not None else None
        ),
    )


async def publish_device_evidence_update(
    publisher: DeviceEvidenceEventPublisherPort | None,
    update: DeviceEvidenceUpdate | None,
) -> None:
    if publisher is None or update is None:
        return
    try:
        _ = await publisher.publish_to(
            DEVICE_EVIDENCE_STREAM_CHANNEL,
            "device_evidence.updated",
            update.model_dump(mode="json"),
        )
    except Exception:
        logger.exception("device.evidence.update_publish_failed")


def _payload_timestamp(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return timezone.to_utc(parsed).isoformat()


__all__ = [
    "DeviceEvidenceEventPublisherPort",
    "build_device_evidence_update",
    "publish_device_evidence_update",
]
