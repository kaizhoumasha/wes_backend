from __future__ import annotations

from typing import Any

from src.app.runtime.orchestration.models.inbox import InboxKind, InboxStatus, SourceSystem, WorklineInbox
from src.app.workline.inbox_claim_bucket import build_claim_bucket_key


def build_workline_inbox(**overrides: Any) -> WorklineInbox:
    payload_json = dict(overrides.pop("payload_json", {}) or {})
    session_id = overrides.get("session_id")
    device_id = overrides.get("device_id")
    workline_id = overrides.get("workline_id")
    claim_bucket_key = overrides.pop("claim_bucket_key", None)
    if not isinstance(claim_bucket_key, str) or not claim_bucket_key:
        claim_bucket_key = build_claim_bucket_key(
            session_id=session_id,
            device_id=device_id,
            workline_id=workline_id,
            payload_json=payload_json,
        )
    return WorklineInbox(
        kind=overrides.pop("kind", InboxKind.DEVICE_EVENT),
        source_system=overrides.pop("source_system", SourceSystem.DEVICE),
        source_message_id=overrides.pop("source_message_id", "test-inbox-message"),
        status=overrides.pop("status", InboxStatus.NEW),
        payload_json=payload_json,
        claim_bucket_key=claim_bucket_key,
        **overrides,
    )


__all__ = ["build_workline_inbox"]
