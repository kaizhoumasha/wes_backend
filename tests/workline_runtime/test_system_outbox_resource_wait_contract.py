"""SystemOutbox RETRY_WAIT 与设备资源等待投影合同。"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from src.app.runtime.orchestration.services.query.runtime_query_service import RuntimeQueryService
from src.app.sys.models import SystemOutboxStatus, is_system_outbox_resource_wait


class _Scalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _Scalars:
        return _Scalars(self._rows)


class _Db:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows
        self.statement: Any | None = None

    async def execute(self, statement: Any) -> _Result:
        self.statement = statement
        return _Result(self.rows)


def _outbox(*, reason: str | None, blocked_at: datetime | None) -> SimpleNamespace:
    return SimpleNamespace(
        status=SystemOutboxStatus.RETRY_WAIT,
        dispatch_type="DEVICE_COMMAND",
        blocked_reason=reason,
        blocked_at=blocked_at,
        blocked_device_id=7,
        target_code="ARM-7",
        payload_json={"command_code": "CMD-7"},
        blocked_check_count=1,
        blocked_detail_json={},
        created_at=datetime(2026, 7, 22, 8, 0, 0),
        id=1,
        finished_at=None,
    )


def test_resource_wait_predicate_requires_controlled_reason_and_metadata() -> None:
    blocked_at = datetime(2026, 7, 22, 8, 0, 0)

    assert is_system_outbox_resource_wait(_outbox(reason="DEVICE_BUSY", blocked_at=blocked_at)) is True
    assert is_system_outbox_resource_wait(_outbox(reason=None, blocked_at=None)) is False
    assert is_system_outbox_resource_wait(_outbox(reason="HTTP_503_BACKOFF", blocked_at=blocked_at)) is False


@pytest.mark.asyncio
async def test_blocked_projection_excludes_ordinary_retry_backoff() -> None:
    blocked_at = datetime(2026, 7, 22, 8, 0, 0)
    db = _Db(
        [
            _outbox(reason=None, blocked_at=None),
            _outbox(reason="DEVICE_BUSY", blocked_at=blocked_at),
        ]
    )

    projection = await RuntimeQueryService()._load_blocked_outbox_projection(
        db,
        [SimpleNamespace(id=7, device_code="ARM-7", device_name="Arm 7")],
    )

    assert projection.count_by_device_id == {7: 1}
    assert projection.command_codes_by_device_id == {7: {"CMD-7"}}
    assert db.statement is not None
    compiled_values = {
        item
        for value in db.statement.compile().params.values()
        for item in (value if isinstance(value, list | tuple | set | frozenset) else [value])
    }
    assert "DEVICE_BUSY" in compiled_values
    assert "DEVICE_STATUS_PRECHECK_WAIT" in compiled_values
