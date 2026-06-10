"""WorklineInbox Repository 层"""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import and_, exists, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.workline.inbox_claim_bucket import build_claim_bucket_key, build_claim_bucket_key_for_update
from src.app.workline.models.inbox import (
    InboxKind,
    InboxStatus,
    WorklineInbox,
)
from src.database.base_repository import BaseRepository
from src.utils.timezone import timezone


@dataclass(frozen=True, slots=True)
class WorklineInboxClaim:
    """Inbox 原子 claim 的轻量结果，不跨 session 传递 ORM 对象。"""

    id: int
    processor_token: str
    received_at: datetime | None
    session_id: int | None
    workline_id: int | None
    device_id: int | None
    kind: InboxKind | str
    payload_json: dict[str, Any]
    claim_bucket_key: str = "serial:unknown"


class WorklineInboxRepository(BaseRepository[WorklineInbox]):
    """作业线收件箱数据访问层"""

    def __init__(self) -> None:
        """初始化收件箱仓库"""
        super().__init__(WorklineInbox)

    @staticmethod
    def _with_claim_bucket_key(data: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(data)
        claim_bucket_key = normalized.get("claim_bucket_key")
        if not isinstance(claim_bucket_key, str) or not claim_bucket_key.strip():
            normalized["claim_bucket_key"] = build_claim_bucket_key(
                session_id=normalized.get("session_id"),
                device_id=normalized.get("device_id"),
                workline_id=normalized.get("workline_id"),
                payload_json=cast("dict[str, Any] | None", normalized.get("payload_json")),
            )
        return normalized

    @staticmethod
    def _with_current_claim_bucket_key(current: WorklineInbox, data: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(data)
        normalized["claim_bucket_key"] = build_claim_bucket_key_for_update(current=current, data=normalized)
        return normalized

    @staticmethod
    def _can_recompute_claim_bucket_key(current: WorklineInbox) -> bool:
        status = getattr(current, "status", None)
        return status in {InboxStatus.NEW, InboxStatus.NEW.value} and not getattr(current, "processor_token", None)

    async def create(self, db: AsyncSession, data: dict[str, Any]) -> WorklineInbox | None:
        """创建 Inbox 时兜底写入物化 claim bucket key。"""

        return await super().create(db, self._with_claim_bucket_key(data))

    async def update(self, db: AsyncSession, id: int, data: dict[str, Any]) -> WorklineInbox | None:
        """更新未 claim 的 Inbox 归属字段时同步刷新物化 claim bucket key。"""

        current = await self.get_by_id(db, id)
        if current is None:
            return await super().update(db, id, data)
        if not self._can_recompute_claim_bucket_key(current):
            return await super().update(db, id, data)
        return await super().update(db, id, self._with_current_claim_bucket_key(current, data))

    async def get_by_idempotency_key(
        self,
        db: AsyncSession,
        idempotency_key: str,
    ) -> WorklineInbox | None:
        """根据幂等键查询（用于幂等检查）"""
        columns = cast("Any", WorklineInbox).__table__.c
        result = await db.execute(
            select(WorklineInbox).where(
                columns.idempotency_key == idempotency_key,
            )
        )
        return result.scalar_one_or_none()

    async def create_idempotent(
        self,
        db: AsyncSession,
        data: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> WorklineInbox:
        """按 idempotency_key 原子创建；冲突时返回已有记录，不回滚当前事务。"""

        data = self._with_claim_bucket_key(data)
        table = cast("Any", WorklineInbox).__table__
        statement = (
            insert(table)
            .values(**data)
            .on_conflict_do_nothing(
                index_elements=["idempotency_key"],
                index_where=table.c.idempotency_key.is_not(None),
            )
            .returning(table.c.id)
        )
        result = await db.execute(statement)
        created_id = result.scalar_one_or_none()
        if isinstance(created_id, int):
            created = await self.get_by_id(db, created_id)
            if created is None:
                raise RuntimeError(f"创建 Inbox 后无法读取: id={created_id}")
            return created

        existing = await self.get_by_idempotency_key(db, idempotency_key)
        if existing is None:
            raise RuntimeError(f"Inbox 幂等创建冲突后无法读取原消息: {idempotency_key}")
        return existing

    @staticmethod
    def _claimable_condition(columns: Any, *, now: datetime, stale_cutoff: datetime) -> Any:
        retry_ready = and_(
            columns.status == InboxStatus.RETRY,
            columns.next_retry_at <= now,
        )
        stale_processing = and_(
            columns.status == InboxStatus.PROCESSING,
            columns.updated_at <= stale_cutoff,
        )
        return or_(
            columns.status == InboxStatus.NEW,
            retry_ready,
            stale_processing,
        )

    async def claim_pending_messages(
        self,
        db: AsyncSession,
        *,
        limit: int = 10,
        processor_token: str,
        stale_after_seconds: int = 300,
    ) -> list[WorklineInboxClaim]:
        """原子 claim 待处理消息。

        合同：
        - NEW / 到期 RETRY / stale PROCESSING 才能进入 PROCESSING。
        - claim 后只返回轻量字段，调用方必须在独立 session 内重新加载 ORM。
        - 使用 SKIP LOCKED 支持多个 worker 扫描同一热队列。
        - 同一 bucket 只 claim 当前队首，避免多个 worker 同时 claim 同一冲突域后续消息。
        """
        if limit <= 0:
            return []

        table = cast("Any", WorklineInbox).__table__
        columns = table.c
        now = timezone.now_for_db()
        stale_cutoff = now - timedelta(seconds=max(stale_after_seconds, 1))
        claim_candidate = table.alias("claim_candidate")
        earlier_inbox = table.alias("earlier_inbox")
        candidate_columns = claim_candidate.c
        earlier_columns = earlier_inbox.c
        candidate_bucket_key = candidate_columns.claim_bucket_key
        earlier_bucket_key = earlier_columns.claim_bucket_key
        candidate_claimable = self._claimable_condition(
            candidate_columns,
            now=now,
            stale_cutoff=stale_cutoff,
        )
        earlier_blocks_bucket = earlier_columns.status.in_(
            [
                InboxStatus.NEW,
                InboxStatus.RETRY,
                InboxStatus.PROCESSING,
            ]
        )
        earlier_message_in_bucket = exists(
            select(1)
            .select_from(earlier_inbox)
            .where(
                earlier_blocks_bucket,
                earlier_bucket_key == candidate_bucket_key,
                tuple_(earlier_columns.received_at, earlier_columns.id)
                < tuple_(candidate_columns.received_at, candidate_columns.id),
            )
        )
        claimable_ids = (
            select(candidate_columns.id)
            .select_from(claim_candidate)
            .where(
                candidate_claimable,
                ~earlier_message_in_bucket,
            )
            .order_by(candidate_columns.received_at, candidate_columns.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )

        statement = (
            update(table)
            .where(columns.id.in_(claimable_ids))
            .values(
                status=InboxStatus.PROCESSING,
                processor_token=processor_token,
                updated_at=now,
            )
            .returning(
                columns.id,
                columns.processor_token,
                columns.received_at,
                columns.session_id,
                columns.workline_id,
                columns.device_id,
                columns.kind,
                columns.payload_json,
                columns.claim_bucket_key,
            )
        )
        result = await db.execute(statement)
        claims = [
            WorklineInboxClaim(
                id=int(row["id"]),
                processor_token=str(row["processor_token"]),
                received_at=cast("datetime | None", row["received_at"]),
                session_id=cast("int | None", row["session_id"]),
                workline_id=cast("int | None", row["workline_id"]),
                device_id=cast("int | None", row["device_id"]),
                kind=cast("InboxKind | str", row["kind"]),
                payload_json=dict(cast("dict[str, Any] | None", row["payload_json"]) or {}),
                claim_bucket_key=str(row.get("claim_bucket_key") or "serial:unknown"),
            )
            for row in result.mappings().all()
        ]
        claims.sort(
            key=lambda item: (item.received_at is None, item.received_at or datetime.min.replace(tzinfo=UTC), item.id)
        )
        return claims

    async def update_processing_message(
        self,
        db: AsyncSession,
        *,
        inbox_id: int,
        processor_token: str,
        data: dict[str, Any],
    ) -> WorklineInbox | None:
        """仅当前 PROCESSING token 持有者可更新消息终态。"""
        table = cast("Any", WorklineInbox).__table__
        columns = table.c
        update_statement = (
            update(WorklineInbox)
            .where(
                columns.id == inbox_id,
                columns.status == InboxStatus.PROCESSING,
                columns.processor_token == processor_token,
            )
            .values(**data, updated_at=timezone.now_for_db())
            .returning(WorklineInbox)
        )
        statement = select(WorklineInbox).from_statement(update_statement).execution_options(populate_existing=True)
        result = await db.execute(statement)
        return cast("WorklineInbox | None", result.scalar_one_or_none())

    async def get_by_kind(
        self,
        db: AsyncSession,
        kind: InboxKind,
        limit: int = 100,
    ) -> list[WorklineInbox]:
        """根据消息类型查询"""
        columns = cast("Any", WorklineInbox).__table__.c
        result = await db.execute(
            select(WorklineInbox).where(columns.kind == kind).order_by(columns.received_at.desc()).limit(limit)
        )
        return list(result.scalars().all())

    def calculate_device_event_idempotency_key(
        self,
        device_code: str,
        event_type: str,
        timestamp: int,
        data: dict[str, Any],
    ) -> str:
        """
        计算设备事件的幂等键

        规则（白皮书 6.3.1）：
        - 优先使用厂商事件 ID
        - 若无，则 device_code + event_type + timestamp + payload_hash
        """
        # 尝试从 data 中获取厂商事件 ID
        vendor_event_id = cast("str | None", data.get("event_id") or data.get("vendor_event_id"))
        if vendor_event_id:
            return f"device_event:{vendor_event_id}"

        # 计算 payload_hash
        payload_items: list[tuple[str, Any]] = sorted(data.items())
        payload_str = str(payload_items)  # 确保字典顺序一致
        payload_hash = hashlib.md5(payload_str.encode(), usedforsecurity=False).hexdigest()[:8]

        # 组合键
        return f"device_event:{device_code}:{event_type}:{timestamp}:{payload_hash}"

    def calculate_command_result_idempotency_key(
        self,
        command_code: str,
        result: str,
        finish_time: int,
        data: dict[str, Any],
    ) -> str:
        """
        计算指令结果的幂等键

        规则（白皮书 6.3.1）：
        - command_code + result + finish_time + payload_hash
        """
        # 计算 payload_hash
        payload_items: list[tuple[str, Any]] = sorted(data.items())
        payload_str = str(payload_items)  # 确保字典顺序一致
        payload_hash = hashlib.md5(payload_str.encode(), usedforsecurity=False).hexdigest()[:8]

        # 组合键
        return f"command_result:{command_code}:{result}:{finish_time}:{payload_hash}"

    def calculate_external_http_idempotency_key(
        self,
        callback_type: str,
        trace_id: str,
        payload: dict[str, Any],
    ) -> str:
        """计算外部 HTTP 回调的幂等键。"""

        source_event_id = payload.get("source_event_id")
        if not isinstance(source_event_id, str) or not source_event_id.strip():
            data = payload.get("data")
            if isinstance(data, dict):
                source_event_id = data.get("source_event_id")
        if isinstance(source_event_id, str) and source_event_id.strip():
            return f"external_http:{callback_type}:{trace_id}:source_event:{source_event_id.strip()}"

        payload_items: list[tuple[str, Any]] = sorted(payload.items())
        payload_str = str(payload_items)
        payload_hash = hashlib.md5(payload_str.encode(), usedforsecurity=False).hexdigest()[:8]
        return f"external_http:{callback_type}:{trace_id}:{payload_hash}"


# 创建单例
inbox_repository = WorklineInboxRepository()
