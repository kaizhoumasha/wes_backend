"""普通批次 FAST fixture：显式声明没有 drain 成员，READY 候选仍原样传递。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock


def unbound_drain_executions():
    return SimpleNamespace(
        for_evidence=AsyncMock(return_value=SimpleNamespace(id=81, status="ACTIVE")),
        eligible_return_prefix=AsyncMock(side_effect=lambda _db, *, workline_id, rows: rows),
        freeze_return_members_in_session=AsyncMock(),
        execution_for_return_batch=AsyncMock(return_value=None),
        members_for_return_batch=AsyncMock(return_value=()),
        pending_return_for_rack=AsyncMock(return_value=None),
    )
