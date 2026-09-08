"""近期记录的分页不能跳过未消费项，也不能把故障伪装成空结果。"""

import json
from unittest.mock import AsyncMock

import pytest

from src.app.wms_diagnostics.config import DiagnosticsConfig
from src.app.wms_diagnostics.contracts import ExchangeObservation, ExchangeQuery
from src.app.wms_diagnostics.repository import DiagnosticsRepository


def observation(attempt_id: str = "attempt-1", operation: str = "sample@v1") -> ExchangeObservation:
    return ExchangeObservation(
        attempt_id=attempt_id, observed_at="2026-09-07T12:00:00Z", direction="WES_TO_WMS", operation=operation
    )


async def test_sparse_page_uses_last_consumed_cursor_and_caps_prefetch_bytes() -> None:
    rows = [
        (f"2000000000000-{index}", {"record": observation(str(index)).model_dump_json()}) for index in range(250, 0, -1)
    ]
    redis = AsyncMock()

    async def reverse_range(key, *, max, min, count):
        assert count * (65 * 1024) <= 2 * 1024 * 1024
        remaining = (
            rows
            if max == "+"
            else [row for row in rows if int(row[0].split("-")[1]) < int(max.lstrip("(").split("-")[1])]
        )
        return remaining[:count]

    redis.xrevrange.side_effect = reverse_range
    repo = DiagnosticsRepository(redis, DiagnosticsConfig())
    page = await repo.list(ExchangeQuery(operation="different@v1"))
    assert page.items == []
    assert page.next_cursor is not None
    assert page.scan_incomplete is True


async def test_expired_detail_is_not_visible_even_before_background_cleanup() -> None:
    redis = AsyncMock()
    redis.xrange.return_value = [("1-0", {"record": observation().model_dump_json()})]
    repo = DiagnosticsRepository(redis, DiagnosticsConfig())
    assert await repo.get("1-0") is None


async def test_read_failure_is_not_an_empty_page() -> None:
    redis = AsyncMock()
    redis.xrevrange.side_effect = ConnectionError("offline")
    with pytest.raises(ConnectionError):
        await DiagnosticsRepository(redis, DiagnosticsConfig()).list(ExchangeQuery())


async def test_summary_does_not_include_wire_body() -> None:
    redis = AsyncMock()
    data = observation().model_dump(mode="json")
    data["request"] = {"body": "secret-free-preview", "state": "CAPTURED", "source": "WIRE"}
    redis.xrevrange.return_value = [("2000000000000-1", {"record": json.dumps(data)})]
    page = await DiagnosticsRepository(redis, DiagnosticsConfig()).list(ExchangeQuery())
    assert "secret-free-preview" not in page.model_dump_json()
    assert page.items[0].exchange_id == "2000000000000-1"
