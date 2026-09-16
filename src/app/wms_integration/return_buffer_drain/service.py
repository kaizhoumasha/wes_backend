"""WorkLine 排空意图沿用 Confirmation 生命周期；结果从匹配 Evidence 读取。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from sqlalchemy import select

from src.app.execution import config
from src.app.execution.models import InboundEvidence, InboundEvidenceKind, WmsConfirmation, WmsConfirmationStatus
from src.app.execution.repositories.wms_confirmation_repository import WmsConfirmationRepository
from src.app.execution.services.wms_confirmation_service import (
    WmsConfirmationIdentityConflictResult,
    WmsConfirmationLifecycleService,
)
from src.app.wms_adapter.return_buffer_drain.typed import decode_intent, decode_outcome, encode_request
from src.app.wms_adapter.return_buffer_drain.wire import RETURN_BUFFER_DRAIN_OPERATION, parse_request, parse_response
from src.utils.canonical_json import canonical_json_digest
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession
    from wes_plugin_sdk import ReturnBufferDrainIntent, ReturnBufferDrainReady, ReturnBufferDrainWait


class ReturnBufferDrainScheduler:
    def __init__(self, confirmations: WmsConfirmationLifecycleService) -> None:
        self._confirmations = confirmations

    async def create_in_session(
        self, db: AsyncSession, intent: ReturnBufferDrainIntent, *, workline_id: int, created_at: datetime
    ) -> None:
        payload = encode_request(intent, timestamp=int(timezone.to_utc(created_at).timestamp() * 1000))
        result = await self._confirmations.create_or_get(
            db,
            operation=RETURN_BUFFER_DRAIN_OPERATION,
            operation_id=intent.operation_id,
            workline_id=workline_id,
            request_payload=payload,
            deadline_at=created_at + config.WMS_CONFIRMATION_DISPATCH_WINDOW,
            created_at=created_at,
        )
        if isinstance(result, WmsConfirmationIdentityConflictResult):
            raise result.to_exception()


@dataclass(frozen=True, slots=True)
class ReturnBufferDrainRecord:
    """宿主验证后的不可变决策记录，不暴露持久化对象或 wire。"""

    workline_id: int
    status: WmsConfirmationStatus
    created_at: datetime
    completed_at: datetime | None
    intent: ReturnBufferDrainIntent
    result: ReturnBufferDrainReady | ReturnBufferDrainWait | None
    evidence_id: int | None


class ReturnBufferDrainResultReader:
    def __init__(self, confirmations: WmsConfirmationRepository | None = None) -> None:
        self._confirmations = confirmations or WmsConfirmationRepository()

    @staticmethod
    def _request(confirmation, workline_id):
        if (
            confirmation is None
            or confirmation.operation != RETURN_BUFFER_DRAIN_OPERATION
            or confirmation.workline_id != workline_id
        ):
            raise ValueError("drain Confirmation identity/owner mismatch")
        request = parse_request(confirmation.request_payload)
        if (
            request.operation_id != confirmation.operation_id
            or canonical_json_digest(confirmation.request_payload) != confirmation.request_digest
        ):
            raise ValueError("drain frozen request drift")
        return request

    @staticmethod
    def _outcome(confirmation, evidence, request, workline_id):
        if (
            confirmation.status != WmsConfirmationStatus.COMPLETED
            or evidence is None
            or evidence.kind != InboundEvidenceKind.WMS_RESULT
            or evidence.operation != RETURN_BUFFER_DRAIN_OPERATION
            or evidence.workline_id != workline_id
            or evidence.operation_id != confirmation.operation_id
            or confirmation.response_evidence_id != evidence.id
        ):
            raise ValueError("drain Evidence does not match completed Confirmation")
        payload = evidence.normalized_payload
        if canonical_json_digest(payload) != evidence.payload_digest:
            raise ValueError("drain frozen response drift")
        response = parse_response(200, payload, request=request)
        if response.data.result != confirmation.response_result:
            raise ValueError("drain completed result differs from Evidence")
        return decode_outcome(response)

    async def read(self, db: AsyncSession, evidence: object, *, workline_id: int):
        operation_id = getattr(evidence, "operation_id", None)
        if (
            getattr(evidence, "operation", None) != RETURN_BUFFER_DRAIN_OPERATION
            or getattr(evidence, "kind", None) != InboundEvidenceKind.WMS_RESULT
            or getattr(evidence, "workline_id", None) != workline_id
            or not isinstance(operation_id, str)
            or not operation_id
        ):
            raise ValueError("drain Evidence identity/owner mismatch")
        # 调用方可能持有 WorkLine 锁；只读取已完成事实，禁止反向获取 Confirmation 锁。
        confirmation = await self._confirmations.get_by_identity(db, RETURN_BUFFER_DRAIN_OPERATION, operation_id)
        request = self._request(confirmation, workline_id)
        outcome = self._outcome(confirmation, evidence, request, workline_id)
        return decode_intent(request), outcome

    async def history(
        self, db: AsyncSession, *, workline_id: int, after_operation_id: str | None = None
    ) -> tuple[ReturnBufferDrainRecord, ...]:
        """一次集合读取最新记录及相邻前驱，额外保留已关闭 checkpoint。

        调用方在 WorkLine 锁内逐次校验直接前驱，生命周期排除旧链重新出现；
        已检查的 WAIT 历史不再逐 wake 重放。checkpoint 缺失仍须 fail closed。
        """
        c = WmsConfirmation
        scope = (c.workline_id == workline_id, c.operation == RETURN_BUFFER_DRAIN_OPERATION)
        # UUIDv7 operation_id 顺序使用既有 (operation, operation_id) 唯一索引；
        # 不为 tail 对整条 WorkLine 历史按 created_at 排序。
        tail = select(c.operation_id).where(*scope)
        if after_operation_id is not None:
            tail = tail.where(c.operation_id > after_operation_id)
        latest = tail.order_by(c.operation_id.desc()).limit(2).correlate(None).subquery()
        identities = select(latest.c.operation_id)
        if after_operation_id is not None:
            identities = identities.union_all(
                select(c.operation_id)
                .where(c.operation == RETURN_BUFFER_DRAIN_OPERATION, c.operation_id == after_operation_id)
                .correlate(None)
            )
        statement = (
            select(c, InboundEvidence)
            .outerjoin(InboundEvidence, InboundEvidence.id == c.response_evidence_id)
            .where(*scope, c.operation_id.in_(identities))
            .order_by(c.operation_id)
        )
        rows = (await db.execute(statement)).all()
        records = []
        for confirmation, evidence in rows:
            request = self._request(confirmation, workline_id)
            result, evidence_id = None, None
            if confirmation.status == WmsConfirmationStatus.COMPLETED:
                if confirmation.completed_at is None:
                    raise ValueError("drain completed time missing")
                outcome = self._outcome(confirmation, evidence, request, workline_id)
                if evidence.published_at is not None:
                    result = cast("ReturnBufferDrainReady | ReturnBufferDrainWait", outcome.result)
                    evidence_id = evidence.id
            records.append(
                ReturnBufferDrainRecord(
                    workline_id=workline_id,
                    status=WmsConfirmationStatus(confirmation.status),
                    created_at=confirmation.created_at,
                    completed_at=confirmation.completed_at,
                    intent=decode_intent(request),
                    result=result,
                    evidence_id=evidence_id,
                )
            )
        if after_operation_id is not None and (
            not records or records[0].intent.operation_id != after_operation_id or records[0].evidence_id is None
        ):
            raise ValueError("drain history checkpoint Evidence missing or unpublished")
        return tuple(records)
