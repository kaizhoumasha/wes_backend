"""设备 callback attempt 与未记录 attempt 的既有 Evidence 分页投影。"""

from typing import Any, cast

from sqlalchemy import BigInteger, and_, literal, or_, select, tuple_, union_all
from sqlalchemy import cast as sql_cast

from src.app.callback.models import CallbackLog
from src.app.execution.models import InboundEvidence
from src.database.base_repository import BaseRepository

DEVICE_INGRESS_CALLBACK_TYPE = "device_ingress_attempt"


class DeviceIngressHistoryRepository(BaseRepository[CallbackLog]):
    def __init__(self):
        super().__init__(CallbackLog)

    async def page_keys(self, db, *, limit, cursor, device_code, kind, command_code, apply_status):
        logs = cast("Any", CallbackLog).__table__.c
        evidence = cast("Any", InboundEvidence).__table__.c
        link = sql_cast(logs.request_body["evidence_id"].as_string(), BigInteger)
        log_filters = [logs.callback_type == DEVICE_INGRESS_CALLBACK_TYPE]
        evidence_filters = [evidence.kind.in_(("DEVICE_EVENT", "DEVICE_OBSERVATION", "DEVICE_RESULT"))]
        for key, value in (("device_code", device_code), ("kind", kind), ("command_code", command_code)):
            if value is not None:
                log_filters.append(logs.request_body[key].as_string() == value)
                evidence_filters.append(evidence[key] == value)
        if apply_status is not None:
            log_filters.append(
                or_(
                    evidence.apply_status == apply_status,
                    and_(evidence.id.is_(None), logs.request_body["apply_status"].as_string() == apply_status),
                )
            )
            evidence_filters.append(evidence.apply_status == apply_status)
        attempted = (
            select(logs.id.label("id"), logs.created_at.label("recorded_at"), literal(1).label("source_rank"))
            .select_from(CallbackLog)
            .outerjoin(InboundEvidence, evidence.id == link)
            .where(*log_filters)
        )
        already_logged = (
            select(logs.id).where(logs.callback_type == DEVICE_INGRESS_CALLBACK_TYPE, link == evidence.id).exists()
        )
        legacy = select(
            evidence.id.label("id"), evidence.received_at.label("recorded_at"), literal(0).label("source_rank")
        ).where(*evidence_filters, ~already_logged)
        rows = union_all(attempted, legacy).subquery()
        query = select(rows)
        if cursor is not None:
            query = query.where(tuple_(rows.c.recorded_at, rows.c.source_rank, rows.c.id) < tuple_(*cursor))
        result = await db.execute(
            query.order_by(rows.c.recorded_at.desc(), rows.c.source_rank.desc(), rows.c.id.desc()).limit(limit)
        )
        return list(result.mappings())

    async def load_logs(self, db, ids):
        if not ids:
            return {}
        columns = cast("Any", CallbackLog).__table__.c
        rows = await db.scalars(select(CallbackLog).where(columns.id.in_(ids)))
        return {row.id: row for row in rows}

    async def load_evidences(self, db, ids):
        if not ids:
            return {}
        columns = cast("Any", InboundEvidence).__table__.c
        rows = await db.scalars(select(InboundEvidence).where(columns.id.in_(ids)))
        return {row.id: row for row in rows}
