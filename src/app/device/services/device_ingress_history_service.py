"""诊断日志独立提交；历史回读同时展示当前 Evidence 状态。"""

import base64
import json
from datetime import datetime

from src.app.callback.services import callback_log_service
from src.app.device.contracts import (
    DeviceEvidenceUpdate,
    DeviceIngressAttempt,
    DeviceIngressHistoryItem,
    DeviceIngressHistoryPage,
)
from src.app.device.repositories.ingress_history_repository import (
    DEVICE_INGRESS_CALLBACK_TYPE,
    DeviceIngressHistoryRepository,
)
from src.database.db import get_db_context
from src.utils.timezone import timezone


class DeviceIngressHistoryService:
    def __init__(self, *, session_context=get_db_context, repository=None, log_service=callback_log_service):
        self._sessions = session_context
        self._repository = repository or DeviceIngressHistoryRepository()
        self._logs = log_service

    async def record_attempt(self, attempt: DeviceIngressAttempt) -> None:
        # 使用独立会话，CallbackLogService 的 commit 不得影响 Evidence 接收事务。
        async with self._sessions() as db:
            await self._logs.log_callback(
                db,
                callback_type=DEVICE_INGRESS_CALLBACK_TYPE,
                subject_code="DEVICE_INGRESS",
                request_id=attempt.request_id,
                request_body=attempt.model_dump(mode="json"),
                response_status=attempt.status_code,
                ingress_outcome=attempt.disposition.value,
                error_message=attempt.error_code,
            )

    async def list_history(
        self, *, limit=20, cursor=None, device_code=None, kind=None, command_code=None, apply_status=None
    ) -> DeviceIngressHistoryPage:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        boundary = _decode_cursor(cursor)
        async with self._sessions() as db:
            keys = await self._repository.page_keys(
                db,
                limit=limit + 1,
                cursor=boundary,
                device_code=device_code,
                kind=kind,
                command_code=command_code,
                apply_status=apply_status,
            )
            page_keys = keys[:limit]
            logs = await self._repository.load_logs(db, [key["id"] for key in page_keys if key["source_rank"] == 1])
            attempts = {key: DeviceIngressAttempt.model_validate(log.request_body) for key, log in logs.items()}
            evidence_ids = {key["id"] for key in page_keys if key["source_rank"] == 0}
            evidence_ids.update(attempt.evidence_id for attempt in attempts.values() if attempt.evidence_id is not None)
            evidences = await self._repository.load_evidences(db, evidence_ids)
            items = []
            for key in page_keys:
                attempt = attempts.get(key["id"]) if key["source_rank"] == 1 else None
                if key["source_rank"] == 1 and attempt is None:
                    continue
                evidence_id = attempt.evidence_id if attempt is not None else key["id"]
                evidence = evidences.get(evidence_id)
                items.append(
                    DeviceIngressHistoryItem(
                        row_key=f"attempt:{attempt.request_id}" if attempt is not None else f"evidence:{key['id']}",
                        recorded_at=timezone.to_utc(key["recorded_at"]).isoformat(),
                        attempt=attempt,
                        latest_update=_evidence_snapshot(evidence) if evidence is not None else None,
                    )
                )
        return DeviceIngressHistoryPage(
            items=items, next_cursor=_encode_cursor(page_keys[-1]) if len(keys) > limit else None
        )


def _evidence_snapshot(evidence) -> DeviceEvidenceUpdate:
    raw = evidence.normalized_payload
    return DeviceEvidenceUpdate(
        evidence_id=evidence.id,
        kind=evidence.kind,
        source_event_id=evidence.source_identity,
        device_code=evidence.device_code or raw.get("device_code", ""),
        command_code=evidence.command_code,
        event_type=raw.get("event_type") if evidence.kind == "DEVICE_EVENT" else None,
        apply_status=evidence.apply_status,
        processed_at=timezone.to_utc(evidence.processed_at).isoformat() if evidence.processed_at is not None else None,
    )


def _encode_cursor(key) -> str:
    value = [timezone.to_utc(key["recorded_at"]).isoformat(), key["source_rank"], key["id"]]
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")


def _decode_cursor(value):
    if value is None:
        return None
    try:
        if not isinstance(value, str) or len(value) > 1024:
            raise ValueError
        parsed = json.loads(base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True))
        if (
            not isinstance(parsed, list)
            or len(parsed) != 3
            or type(parsed[1]) is not int
            or parsed[1] not in (0, 1)
            or type(parsed[2]) is not int
            or not 0 < parsed[2] <= 2**63 - 1
        ):
            raise ValueError
        timestamp = datetime.fromisoformat(parsed[0])
        if timestamp.tzinfo is None:
            raise ValueError
        return timezone.to_utc(timestamp).replace(tzinfo=None), parsed[1], parsed[2]
    except (ValueError, TypeError, UnicodeError) as error:
        raise ValueError("invalid device history cursor") from error


device_ingress_history_service = DeviceIngressHistoryService()
