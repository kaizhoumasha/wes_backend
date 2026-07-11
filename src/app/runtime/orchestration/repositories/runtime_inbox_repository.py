"""RuntimeInbox 跨域只读 Repository 实现。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import func, select

from src.app.contracts.runtime_inbox_query import (
    RUNTIME_INBOX_UNFINISHED_STATUSES,
    RuntimeInboxEvidence,
    RuntimeInboxWorkloadSample,
)
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.core.conf import settings
from src.database.base_repository import BaseRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class RuntimeInboxPayloadTooLarge(Exception):
    """canonical payload 的 UTF-8 JSON bytes 超过 RuntimeInbox 持久化上限。"""

    status_code = 413

    def __init__(self, *, actual_bytes: int, max_bytes: int) -> None:
        super().__init__(f"runtime inbox payload too large: actual_bytes={actual_bytes}, max_bytes={max_bytes}")
        self.actual_bytes = actual_bytes
        self.max_bytes = max_bytes


def validate_canonical_payload_size(payload_json: object) -> None:
    """在 db.add/flush/ACK 前校验 canonical JSON 的 UTF-8 bytes。"""

    if not isinstance(payload_json, dict):
        raise TypeError("runtime inbox payload_json must be a dict")
    encoded = json.dumps(
        payload_json,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    max_bytes = settings.runtime_inbox_payload_max_bytes
    if len(encoded) > max_bytes:
        raise RuntimeInboxPayloadTooLarge(actual_bytes=len(encoded), max_bytes=max_bytes)


class RuntimeInboxRepository(BaseRepository[RuntimeInbox]):
    """集中持有 RuntimeInbox ORM，并向业务 repository 暴露 typed query DTO。"""

    def __init__(self) -> None:
        super().__init__(RuntimeInbox)

    async def get_by_source_event_identity(
        self,
        db: AsyncSession,
        *,
        provider_code: str,
        event_type: str,
        source_event_id: str,
    ) -> RuntimeInbox | None:
        """按入站事件幂等身份读取 RuntimeInbox。"""

        columns = cast("Any", RuntimeInbox).__table__.c
        result = await db.execute(
            select(RuntimeInbox).where(
                columns.provider_code == provider_code,
                columns.event_type == event_type,
                columns.source_event_id == source_event_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_id_for_update(self, db: AsyncSession, inbox_id: int) -> RuntimeInbox | None:
        """锁定读取单条 RuntimeInbox，用于人工重放。"""

        columns = cast("Any", RuntimeInbox).__table__.c
        result = await db.execute(select(RuntimeInbox).where(columns.id == inbox_id).with_for_update())
        return result.scalar_one_or_none()

    async def add_received(self, db: AsyncSession, data: dict[str, Any]) -> RuntimeInbox:
        """新建 RECEIVED RuntimeInbox 并 flush，调用方控制事务提交。"""

        validate_canonical_payload_size(data.get("payload_json"))
        record = RuntimeInbox(**data)
        db.add(record)
        await db.flush()
        await db.refresh(record)
        return record

    async def get_evidence_by_id(self, db: AsyncSession, inbox_id: int) -> RuntimeInboxEvidence | None:
        """按显式主键返回跨域只读 evidence DTO。"""

        record = await self.get_by_id(db, inbox_id)
        if record is None or record.id is None:
            return None
        return RuntimeInboxEvidence(
            id=record.id,
            status=record.status,
            event_id=record.event_id,
            attempt_count=record.attempt_count,
            max_retries=record.max_retries,
            next_retry_at=record.next_retry_at,
            processed_at=record.processed_at,
            failed_at=record.failed_at,
            last_error_code=record.last_error_code,
            last_error_message=record.last_error_message,
        )

    async def count_unfinished_by_workline(self, db: AsyncSession, workline_id: int) -> int:
        """按显式 workline_id 统计非终态 RuntimeInbox。"""

        columns = cast("Any", RuntimeInbox).__table__.c
        result = await db.execute(
            select(func.count())
            .select_from(RuntimeInbox)
            .where(
                columns.workline_id == workline_id,
                columns.status.in_(RUNTIME_INBOX_UNFINISHED_STATUSES),
            )
        )
        return int(result.scalar_one() or 0)

    async def first_unfinished_by_workline(
        self,
        db: AsyncSession,
        workline_id: int,
    ) -> RuntimeInboxWorkloadSample | None:
        """按主键稳定顺序返回首条非终态 RuntimeInbox。"""

        columns = cast("Any", RuntimeInbox).__table__.c
        result = await db.execute(
            select(columns.id, columns.status)
            .where(
                columns.workline_id == workline_id,
                columns.status.in_(RUNTIME_INBOX_UNFINISHED_STATUSES),
            )
            .order_by(columns.id.asc())
            .limit(1)
        )
        row = result.first()
        if row is None:
            return None
        return RuntimeInboxWorkloadSample(id=int(row[0]), status=str(row[1]))


runtime_inbox_repository = RuntimeInboxRepository()


__all__ = [
    "RuntimeInboxPayloadTooLarge",
    "RuntimeInboxRepository",
    "runtime_inbox_repository",
    "validate_canonical_payload_size",
]
