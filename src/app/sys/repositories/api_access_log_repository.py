"""统一日志所属的摘要查询与有界清理。"""

from datetime import datetime
from typing import Any, cast

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.sys.models.api_access_log import APIAccessLog, APIAccessLogSummary
from src.core.query_builder import QueryBuilder
from src.core.query_models import QueryOptions
from src.database.base_repository import BaseRepository


class APIAccessLogRepository(BaseRepository[APIAccessLog]):
    def __init__(self) -> None:
        super().__init__(APIAccessLog)

    async def query_summary(self, db: AsyncSession, options: QueryOptions) -> tuple[int, list[Any]]:
        builder = QueryBuilder(APIAccessLog)
        clause = builder.build_filters(options.filters) if options.filters else None
        filters = [clause] if clause is not None else []
        total = await self.count(db, filters)
        columns = cast("Any", APIAccessLog).__table__.c
        query = select(*(columns[name] for name in APIAccessLogSummary.model_fields)).where(*filters)
        order = builder.build_sort(options.sort) if options.sort else [columns.created_at.desc(), columns.id.desc()]
        result = await db.execute(query.order_by(*order).offset(options.offset).limit(options.limit))
        return total, list(result.mappings().all())

    async def delete_expired_batch(self, db: AsyncSession, *, cutoff: datetime, limit: int) -> int:
        columns = cast("Any", APIAccessLog).__table__.c
        expired = (
            select(columns.id).where(columns.created_at < cutoff).order_by(columns.created_at, columns.id).limit(limit)
        )
        result = await db.execute(delete(APIAccessLog).where(columns.id.in_(expired)))
        return result.rowcount


api_access_log_repository = APIAccessLogRepository()

__all__ = ["APIAccessLogRepository", "api_access_log_repository"]
