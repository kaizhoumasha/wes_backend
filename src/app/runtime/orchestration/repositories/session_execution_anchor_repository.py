"""WorklineSession 对应执行锚点的归属校验 Repository。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select

from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.execution_work_item import ExecutionWorkItem

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class SessionExecutionAnchorRepository:
    """只在完整执行聚合归属匹配时返回 Session 的 execution 锚点。"""

    async def resolve_owned_anchor(
        self,
        db: AsyncSession,
        *,
        correlation_id: str,
        trace_id: str,
        workline_id: int,
        plugin_key: str,
        contract_version: str,
        plugin_binding_id: int,
        plugin_binding_version: int,
        plugin_config_hash: str,
        plugin_index_digest: str,
        business_key: str,
    ) -> tuple[str, int] | None:
        """按确定性 correlation 校验 ExecutionSession/Correlation/WorkItem 三方归属。"""

        correlation_columns = cast("Any", ExecutionCorrelation).__table__.c
        execution_session_columns = cast("Any", ExecutionSession).__table__.c
        work_item_columns = cast("Any", ExecutionWorkItem).__table__.c
        row = (
            await db.execute(
                select(correlation_columns.correlation_id, correlation_columns.execution_session_id)
                .join(
                    ExecutionSession,
                    execution_session_columns.id == correlation_columns.execution_session_id,
                )
                .join(
                    ExecutionWorkItem,
                    (work_item_columns.correlation_id == correlation_columns.correlation_id)
                    & (work_item_columns.execution_session_id == execution_session_columns.id),
                )
                .where(
                    correlation_columns.correlation_id == correlation_id,
                    correlation_columns.trace_id == trace_id,
                    correlation_columns.business_owner_key == business_key,
                    execution_session_columns.workline_id == workline_id,
                    execution_session_columns.plugin_key == plugin_key,
                    execution_session_columns.manifest_version == contract_version,
                    execution_session_columns.plugin_binding_id == plugin_binding_id,
                    execution_session_columns.plugin_binding_version == plugin_binding_version,
                    execution_session_columns.plugin_config_hash == plugin_config_hash,
                    execution_session_columns.plugin_index_digest == plugin_index_digest,
                    work_item_columns.plugin_key == plugin_key,
                    work_item_columns.plugin_binding_id == plugin_binding_id,
                    work_item_columns.plugin_binding_version == plugin_binding_version,
                    work_item_columns.plugin_config_hash == plugin_config_hash,
                    work_item_columns.plugin_index_digest == plugin_index_digest,
                    work_item_columns.object_type != "",
                    work_item_columns.object_key != "",
                )
                .limit(1)
            )
        ).one_or_none()
        if row is None or row[1] is None:
            return None
        return str(row[0]), int(row[1])


session_execution_anchor_repository = SessionExecutionAnchorRepository()
