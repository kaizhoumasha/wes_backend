"""Workline 插件 binding 数据访问层。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import func, select

from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.execution_work_item import ExecutionWorkItem
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog
from src.app.workline.models.plugin_binding import WorklinePluginBinding
from src.database.base_repository import BaseRepository
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class WorklinePluginBindingRepository(BaseRepository[WorklinePluginBinding]):
    def __init__(self) -> None:
        super().__init__(WorklinePluginBinding)

    async def next_binding_version(
        self, db: AsyncSession, workline_id: int, plugin_key: str, contract_version: str
    ) -> int:
        columns = cast("Any", WorklinePluginBinding).__table__.c
        result = await db.execute(
            select(func.max(columns.binding_version)).where(
                columns.workline_id == workline_id,
                columns.plugin_key == plugin_key,
                columns.contract_version == contract_version,
            )
        )
        return int(result.scalar_one_or_none() or 0) + 1

    async def create_immutable(self, db: AsyncSession, data: dict[str, Any]) -> WorklinePluginBinding:
        binding = await self.create(db, data)
        if binding is None:
            raise RuntimeError("创建 WorklinePluginBinding 失败")
        return binding

    async def get_pinned(self, db: AsyncSession, binding_id: int) -> WorklinePluginBinding | None:
        return await self.get_by_id(db, binding_id)

    @staticmethod
    async def create_pinned_runtime_aggregate(
        db: AsyncSession,
        *,
        workline_session: Any,
        execution_session: ExecutionSession,
        work_item: ExecutionWorkItem,
    ) -> tuple[ExecutionSession, ExecutionWorkItem]:
        """在 caller 事务中创建同 pin 的 target-state Execution 聚合。"""

        now = timezone.now_for_db()
        execution_session.created_at = now
        execution_session.updated_at = now
        db.add(execution_session)
        await db.flush()
        if execution_session.id is None:
            raise RuntimeError("ExecutionSession 创建后缺少 ID")
        correlation_id = f"workline-session:{workline_session.session_code}"
        correlation = ExecutionCorrelation(
            correlation_id=correlation_id,
            execution_session_id=execution_session.id,
            trace_id=workline_session.trace_id or correlation_id,
            source_event_id=workline_session.last_request_id,
            business_owner_key=workline_session.business_key,
            created_at=now,
            updated_at=now,
        )
        db.add(correlation)
        await db.flush()
        work_item.execution_session_id = execution_session.id
        db.add(work_item)
        await db.flush()
        return execution_session, work_item

    @staticmethod
    async def list_runtime_extension_references(db: AsyncSession, workline_id: int) -> list[dict[str, Any]]:
        """列出 WorkItem/Intent 通过 ExecutionSession 固定的插件与索引引用。"""

        session_columns = cast("Any", ExecutionSession).__table__.c
        work_item_columns = cast("Any", ExecutionWorkItem).__table__.c
        intent_columns = cast("Any", RuntimeIntentLog).__table__.c
        work_items = await db.execute(
            select(
                work_item_columns.id,
                work_item_columns.plugin_key,
                work_item_columns.plugin_binding_id,
                work_item_columns.plugin_binding_version,
                work_item_columns.plugin_config_hash,
                work_item_columns.plugin_index_digest,
            )
            .select_from(ExecutionWorkItem)
            .join(ExecutionSession, session_columns.id == work_item_columns.execution_session_id)
            .where(session_columns.workline_id == workline_id)
            .order_by(work_item_columns.id)
        )
        intents = await db.execute(
            select(
                intent_columns.id,
                session_columns.plugin_key,
                session_columns.plugin_binding_id,
                session_columns.plugin_binding_version,
                session_columns.plugin_config_hash,
                session_columns.plugin_index_digest,
            )
            .select_from(RuntimeIntentLog)
            .join(ExecutionSession, session_columns.id == intent_columns.execution_session_id)
            .where(session_columns.workline_id == workline_id)
            .order_by(intent_columns.id)
        )
        references = [
            {
                "type": "WORK_ITEM",
                "reference": f"work-item:{row.id}",
                "plugin_key": row.plugin_key,
                "plugin_binding_id": row.plugin_binding_id,
                "plugin_binding_version": row.plugin_binding_version,
                "plugin_config_hash": row.plugin_config_hash,
                "plugin_index_digest": row.plugin_index_digest,
            }
            for row in work_items
        ]
        references.extend(
            {
                "type": "INTENT",
                "reference": f"intent:{row.id}",
                "plugin_key": row.plugin_key,
                "plugin_binding_id": row.plugin_binding_id,
                "plugin_binding_version": row.plugin_binding_version,
                "plugin_config_hash": row.plugin_config_hash,
                "plugin_index_digest": row.plugin_index_digest,
            }
            for row in intents
        )
        return references


workline_plugin_binding_repository = WorklinePluginBindingRepository()

__all__ = ["WorklinePluginBindingRepository", "workline_plugin_binding_repository"]
