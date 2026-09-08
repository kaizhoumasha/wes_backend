"""单 Redis Stream 的有界近期记录，不承担可靠业务义务。"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from src.app.wms_diagnostics.contracts import (
    ExchangeDetail,
    ExchangeFacts,
    ExchangeFilters,
    ExchangePage,
    ExchangeQuery,
    ExchangeSummary,
)
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from src.app.wms_diagnostics.config import DiagnosticsConfig

STREAM_KEY = "wms:diagnostics:exchanges"
_MAX_READ_BYTES = 2 * 1024 * 1024
_RECORD_READ_BOUND = 65 * 1024


def matches(record: ExchangeFacts, query: ExchangeFilters) -> bool:
    for name in ("direction", "operation", "operation_id", "business_reference"):
        expected = getattr(query, name)
        if expected is not None and getattr(record, name) != expected:
            return False
    return not query.only_errors or record.error_code is not None or record.contract_status == "ERROR"


class DiagnosticsRepository:
    def __init__(self, redis: Any, config: DiagnosticsConfig) -> None:
        self._redis = redis
        self._config = config

    def _cutoff(self) -> int:
        return int(timezone.now_utc().timestamp() * 1000) - self._config.retention_hours * 3600000

    async def append(self, encoded: str) -> str:
        if len(encoded.encode("utf-8")) > self._config.max_record_bytes:
            raise ValueError("diagnostic record exceeds byte budget")
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.xadd(STREAM_KEY, {"record": encoded}, maxlen=self._config.max_records, approximate=False)
            pipe.xtrim(STREAM_KEY, minid=f"{self._cutoff()}-0", approximate=False)
            pipe.expire(STREAM_KEY, self._config.retention_hours * 3600)
            result = await pipe.execute()
        identity = result[0]
        return identity.decode() if isinstance(identity, bytes) else identity

    @staticmethod
    def _decode(row: tuple) -> ExchangeDetail:
        identity, fields = row
        identity = identity.decode() if isinstance(identity, bytes) else identity
        raw = fields.get("record", fields.get(b"record"))
        from src.app.wms_diagnostics.contracts import ExchangeObservation

        observation = ExchangeObservation.model_validate_json(raw)
        return ExchangeDetail(**(observation.model_dump() | {"exchange_id": identity}))

    async def get(self, exchange_id: str) -> ExchangeDetail | None:
        if int(exchange_id.split("-", maxsplit=1)[0]) < self._cutoff():
            return None
        rows = await self._redis.xrange(STREAM_KEY, min=exchange_id, max=exchange_id, count=1)
        return self._decode(rows[0]) if rows else None

    async def list(self, query: ExchangeQuery) -> ExchangePage:
        minimum = f"{max(self._cutoff(), query.from_ms or 0)}-0"
        maximum = (
            f"({query.cursor}"
            if query.cursor
            else f"{query.to_ms}-18446744073709551615"
            if query.to_ms is not None
            else "+"
        )
        read_budget = _MAX_READ_BYTES
        output_budget = 256 * 1024 - 4096
        scanned = 0
        items: list[ExchangeSummary] = []
        cursor = query.cursor
        more = True
        deadline = time.monotonic() + self._config.budget_ms / 1000
        while scanned < 200 and read_budget >= _RECORD_READ_BOUND and time.monotonic() < deadline:
            count = min(200 - scanned, read_budget // _RECORD_READ_BOUND)
            rows = await self._redis.xrevrange(STREAM_KEY, min=minimum, max=maximum, count=count)
            # 读前按旧记录硬上限预留；调低配置不得扩大预取批次。
            read_budget -= len(rows) * _RECORD_READ_BOUND
            if not rows:
                more = False
                break
            for row in rows:
                if time.monotonic() >= deadline:
                    break
                detail = self._decode(row)
                summary = ExchangeSummary(**{name: getattr(detail, name) for name in ExchangeSummary.model_fields})
                matched = matches(summary, query) and (
                    query.to_ms is None or int(detail.exchange_id.split("-")[0]) <= query.to_ms
                )
                size = len(summary.model_dump_json().encode())
                if size > 2048:
                    raise ValueError("diagnostic summary exceeds byte budget")
                if matched and (len(items) >= query.page_size or size > output_budget):
                    return ExchangePage(
                        items=items,
                        next_cursor=cursor,
                        scan_incomplete=True,
                        retention_hours=self._config.retention_hours,
                    )
                cursor = detail.exchange_id
                scanned += 1
                if matched:
                    items.append(summary)
                    output_budget -= size
                maximum = f"({cursor}"
            else:
                if len(rows) < count:
                    more = False
                    break
                continue
            break
        return ExchangePage(
            items=items,
            next_cursor=cursor if more else None,
            scan_incomplete=more,
            retention_hours=self._config.retention_hours,
        )
