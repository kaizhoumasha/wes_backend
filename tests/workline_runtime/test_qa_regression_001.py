from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.app.workline.models.inbox import InboxKind
from src.app.workline.services.inbox_service import WorklineInboxService


class _InboxRepoStub:
    def __init__(self, existing: object | None = None) -> None:
        self.existing = existing
        self.created: dict[str, Any] | None = None
        self.calculated_with: dict[str, Any] | None = None

    def calculate_device_event_idempotency_key(
        self,
        *,
        device_code: str,
        event_type: str,
        timestamp: int,
        data: dict[str, Any],
    ) -> str:
        self.calculated_with = {
            "device_code": device_code,
            "event_type": event_type,
            "timestamp": timestamp,
            "data": data,
        }
        return "device_event:fallback"

    async def get_by_idempotency_key(self, _db: object, _idempotency_key: str) -> object | None:
        return self.existing

    async def create(self, _db: object, data: dict[str, Any]) -> object:
        self.created = data
        return SimpleNamespace(id=99, **data)


@pytest.mark.asyncio
async def test_device_event_inbox_uses_top_level_event_id_for_idempotency() -> None:
    """顶层 event_id 是供应商事件身份，必须成为 callback/event 幂等键。"""

    # Regression: ISSUE-001 — callback/event duplicate top-level event_id created new side effects.
    # Found by /qa on 2026-04-27
    # Report: .gstack/qa-reports/qa-report-localhost-2026-04-27.md
    repo = _InboxRepoStub()
    service = WorklineInboxService()
    service.repo = repo  # type: ignore[assignment]
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())

    inbox = await service.create_device_event_inbox(
        db=db,
        device_code="ARM01",
        event_type="SCAN_COMPLETED",
        timestamp=1777200000000,
        data={"PkgID": "PKG-001"},
        trace_id="trace-001",
        event_id="evt-001",
    )

    assert inbox.id == 99
    assert repo.calculated_with is None
    assert repo.created is not None
    assert repo.created["kind"] == InboxKind.DEVICE_EVENT
    assert repo.created["idempotency_key"] == "device_event:evt-001"
    assert repo.created["event_id"] == "evt-001"


@pytest.mark.asyncio
async def test_device_event_inbox_rejects_duplicate_top_level_event_id() -> None:
    """同一个顶层 event_id 再次上报时不能创建新的 inbox。"""

    # Regression: ISSUE-001 — callback/event duplicate top-level event_id created new side effects.
    # Found by /qa on 2026-04-27
    # Report: .gstack/qa-reports/qa-report-localhost-2026-04-27.md
    repo = _InboxRepoStub(existing=SimpleNamespace(id=42))
    service = WorklineInboxService()
    service.repo = repo  # type: ignore[assignment]

    with pytest.raises(ValueError, match="设备事件已存在"):
        await service.create_device_event_inbox(
            db=object(),
            device_code="ARM01",
            event_type="SCAN_COMPLETED",
            timestamp=1777200000000,
            data={"PkgID": "PKG-001"},
            trace_id="trace-001",
            event_id="evt-001",
            auto_commit=False,
        )

    assert repo.created is None
