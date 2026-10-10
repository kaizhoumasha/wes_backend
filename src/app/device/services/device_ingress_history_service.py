"""诊断日志响应后尽力写入；历史回读同时展示当前 Evidence 状态。"""

import base64
import json
from datetime import datetime

from src.app.device.contracts import (
    DeviceIngressAttempt,
    DeviceIngressHistoryItem,
    DeviceIngressHistoryPage,
)
from src.app.device.evidence_projection import build_device_evidence_update
from src.app.device.repositories.ingress_history_repository import (
    DeviceIngressHistoryRepository,
)
from src.app.sys.models.api_access_log import APIAccessLogCreate
from src.app.sys.services.api_access_log_service import api_access_log_service
from src.database.db import get_db_context
from src.utils.timezone import timezone


class DeviceIngressHistoryService:
    def __init__(self, *, session_context=get_db_context, repository=None, log_service=api_access_log_service):
        self._sessions = session_context
        self._repository = repository or DeviceIngressHistoryRepository()
        self._logs = log_service

    def record_attempt(self, attempt: DeviceIngressAttempt) -> None:
        self._logs.defer_record(
            APIAccessLogCreate(
                system_id="ecs",
                direction="INBOUND",
                method="POST",
                path=attempt.path,
                request_id=attempt.request_id,
                details=attempt.model_dump(mode="json"),
                status_code=attempt.status_code,
                error_code=attempt.error_code,
            )
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
            attempts = {key: DeviceIngressAttempt.model_validate(log.details) for key, log in logs.items()}
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
                        latest_update=build_device_evidence_update(evidence) if evidence is not None else None,
                    )
                )
        return DeviceIngressHistoryPage(
            items=items, next_cursor=_encode_cursor(page_keys[-1]) if len(keys) > limit else None
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
