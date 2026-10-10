"""单表摘要 SQL、完整详情、目标 schema 与清理真实事务 owner。

显式 RUN_WORKLINE_INTEGRATION=1、INTEGRATION_DATABASE_URL 和 INTEGRATION_REDIS_URL；
由主 Agent 在独占临时库迁移 head 后执行，不安装业务插件。
"""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, event, inspect, select, text

from src.app.sys.models.api_access_log import (
    APIAccessLog,
    APIAccessLogCreate,
    APIAccessLogResponse,
    APIAccessLogSummary,
)
from src.app.sys.repositories.api_access_log_repository import APIAccessLogRepository
from src.app.sys.services.api_access_log_service import APIAccessLogService
from src.core.query_models import QueryOptions
from src.utils.timezone import timezone

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_four_directions_share_table_and_summary_select_omits_details(integration_session_factory):
    prefix = uuid4().hex
    service = APIAccessLogService(session_context=integration_session_factory)
    request_ids = []
    try:
        for index, (system, direction) in enumerate(
            (("wms", "INBOUND"), ("ecs", "INBOUND"), ("wms", "OUTBOUND"), ("ecs", "OUTBOUND"))
        ):
            request_id = f"{prefix}-{index}"
            request_ids.append(request_id)
            await service.record(
                APIAccessLogCreate(
                    system_id=system,
                    direction=direction,
                    method="POST",
                    path="/events",
                    request_id=request_id,
                    status_code=200 if direction == "INBOUND" else None,
                    details={"snapshot": index} if direction == "INBOUND" else None,
                )
            )
        options = QueryOptions.model_validate(
            {
                "filters": {"conditions": [{"field": "request_id", "op": "in", "value": request_ids}]},
                "sort": [{"field": "id", "order": "asc"}],
                "offset": 1,
                "limit": 2,
            }
        )
        statements = []
        async with integration_session_factory() as db:
            engine = db.get_bind()

            def capture(conn, cursor, statement, parameters, context, executemany):
                statements.append(statement)

            event.listen(engine, "before_cursor_execute", capture)
            try:
                total, rows = await service.query_summary(db, options)
            finally:
                event.remove(engine, "before_cursor_execute", capture)
            assert total == 4
            assert [(row.system_id, row.direction) for row in rows] == [("ecs", "INBOUND"), ("wms", "OUTBOUND")]
            assert len(statements) == 2
            assert all("details" not in sql.lower() for sql in statements)
            assert all("details" not in row.model_dump() for row in rows)
            detail = await service.get_by_id(db, None, rows[0].id)
            assert APIAccessLogResponse.model_validate(detail).details == {"snapshot": 1}
            detail = await service.get_by_id(db, None, rows[1].id)
            assert APIAccessLogResponse.model_validate(detail).details is None
            filtered = QueryOptions.model_validate(
                {
                    "filters": {
                        "conditions": [
                            {"field": "request_id", "op": "in", "value": request_ids},
                            {"field": "system_id", "op": "eq", "value": "ecs"},
                            {"field": "direction", "op": "eq", "value": "OUTBOUND"},
                            {"field": "status_code", "op": "is_null"},
                        ]
                    }
                }
            )
            total, rows = await service.query_summary(db, filtered)
            assert total == len(rows) == 1
            assert rows[0].status_code is None
    finally:
        async with integration_session_factory.begin() as db:
            await db.execute(delete(APIAccessLog).where(APIAccessLog.request_id.in_(request_ids)))


async def test_current_schema_has_only_unified_log_shape_and_indexes(integration_session_factory):
    async with integration_session_factory() as db:
        connection = await db.connection()
        catalog = await connection.run_sync(
            lambda sync: (
                inspect(sync).get_columns("api_access_logs", schema="wes_sys"),
                inspect(sync).get_indexes("api_access_logs", schema="wes_sys"),
            )
        )
        assert {column["name"] for column in catalog[0]} == set(APIAccessLogSummary.model_fields) | {
            "details",
            "updated_at",
        }
        assert (
            next(column for column in catalog[0] if column["name"] == "id")["comment"]
            == APIAccessLog.__table__.c.id.comment
        )
        assert {tuple(index["column_names"]) for index in catalog[1]} == {
            ("created_at", "id"),
            ("system_id", "direction", "created_at"),
            ("request_id",),
            ("trace_id",),
        }
        assert await db.scalar(text("SELECT to_regclass('wes_biz.callback_logs')")) is None
        assert await db.scalar(text("SELECT version_num FROM wes_sys.alembic_version")) == "a16c9b2e7d04"


async def test_cleanup_preserves_cutoff_and_recent_rows_and_rolls_back_failed_batch(
    integration_session_factory, monkeypatch
):
    now = timezone.now_for_db()
    monkeypatch.setattr(timezone, "now_for_db", lambda: now)
    cutoff = now - timedelta(days=7)
    prefix = uuid4().hex

    class FailingRepository(APIAccessLogRepository):
        async def delete_expired_batch(self, db, **kwargs):
            await super().delete_expired_batch(db, **kwargs)
            raise RuntimeError("forced batch failure")

    try:
        async with integration_session_factory.begin() as db:
            db.add_all(
                [
                    APIAccessLog(
                        system_id="wms",
                        direction="INBOUND",
                        method="POST",
                        path="/events",
                        request_id=prefix,
                        created_at=cutoff - timedelta(seconds=1),
                    )
                    for _ in range(5002)
                ]
            )
            db.add(
                APIAccessLog(
                    system_id="wms",
                    direction="INBOUND",
                    method="POST",
                    path="/events",
                    request_id=prefix,
                    created_at=cutoff,
                )
            )
            db.add(
                APIAccessLog(
                    system_id="ecs",
                    direction="OUTBOUND",
                    method="POST",
                    path="/command",
                    request_id=prefix,
                    created_at=now,
                )
            )
        with pytest.raises(RuntimeError, match="forced batch failure"):
            await APIAccessLogService(
                repository=FailingRepository(), session_context=integration_session_factory
            ).cleanup()
        async with integration_session_factory() as db:
            assert len(list(await db.scalars(select(APIAccessLog.id).where(APIAccessLog.request_id == prefix)))) == 5004
            protected_before = [
                await db.scalar(text(f"SELECT count(*) FROM {table}"))
                for table in (
                    "wes_sys.audit_logs",
                    "wes_biz.inbound_evidences",
                    "wes_biz.device_commands",
                    "wes_biz.wms_confirmations",
                )
            ]
        service = APIAccessLogService(session_context=integration_session_factory)
        assert await service.cleanup() == 5000
        assert await service.cleanup() == 2
        assert await service.cleanup() == 0
        async with integration_session_factory() as db:
            remaining = list(await db.scalars(select(APIAccessLog).where(APIAccessLog.request_id == prefix)))
            assert {row.created_at for row in remaining} == {cutoff, now}
            protected_after = [
                await db.scalar(text(f"SELECT count(*) FROM {table}"))
                for table in (
                    "wes_sys.audit_logs",
                    "wes_biz.inbound_evidences",
                    "wes_biz.device_commands",
                    "wes_biz.wms_confirmations",
                )
            ]
            assert protected_before == protected_after
    finally:
        async with integration_session_factory.begin() as db:
            await db.execute(delete(APIAccessLog).where(APIAccessLog.request_id == prefix))
