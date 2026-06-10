from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.app.sys.models import SystemOutboxStatus
from src.app.workline.services.runtime_query_service import RuntimeQueryService
from src.utils.timezone import timezone


class _ResultStub:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def scalars(self) -> SimpleNamespace:
        return SimpleNamespace(all=lambda: self._rows)


@pytest.mark.asyncio
async def test_runtime_device_projection_includes_resource_wait_summary() -> None:
    service = RuntimeQueryService()
    blocked_at = timezone.now_for_db() - timedelta(seconds=30)
    last_check_at = timezone.now_for_db() - timedelta(seconds=5)
    device = SimpleNamespace(
        id=77,
        device_code="ARM-01",
        device_name="机械臂 01",
        device_role="ROBOT_ARM",
        role_index=1,
        upstream_device_id=None,
        work_line_id=22,
        device_status="IDLE",
        maintenance_mode=False,
        current_command_id=None,
        last_heartbeat_at=None,
        error_code=None,
    )
    blocked_outbox = SimpleNamespace(
        status=SystemOutboxStatus.BLOCKED_RESOURCE,
        blocked_device_id=77,
        target_code="ARM-01",
        blocked_reason="DEVICE_STATUS_PRECHECK_WAIT",
        blocked_at=blocked_at,
        last_blocked_check_at=last_check_at,
        blocked_check_count=4,
        blocked_detail_json={
            "device_code": "ARM-01",
            "status_url": "http://mock-ecs.internal:8010/api/v1/device/status?token=secret&device_code=ARM-01",
            "error_kind": "http_status",
            "http_status": 503,
            "raw_vendor_response": {"large": "should-not-leak"},
        },
        payload_json={"command_code": "CMD-BLOCKED-001"},
        created_at=blocked_at,
    )
    db = SimpleNamespace(execute=AsyncMock(return_value=_ResultStub([blocked_outbox])))

    projection = await service._load_blocked_outbox_projection(db, [device])
    summary = service._build_device_summary(
        device,
        None,
        open_command_count=0,
        recent_callback_at=None,
        blocked_outbox_count=projection.count_by_device_id[77],
        blocked_outbox_summary=projection.summary_by_device_id[77],
    )

    assert summary.blocked_outbox_count == 1
    assert summary.blocked_reason == "DEVICE_STATUS_PRECHECK_WAIT"
    assert summary.blocked_wait_seconds is not None and summary.blocked_wait_seconds >= 29
    assert summary.blocked_check_count == 4
    assert summary.blocked_detail_json == {
        "device_code": "ARM-01",
        "status_url": "/api/v1/device/status?device_code=ARM-01",
        "error_kind": "http_status",
        "http_status": 503,
    }


@pytest.mark.asyncio
async def test_blocked_outbox_projection_handles_missing_created_at() -> None:
    service = RuntimeQueryService()
    blocked_at = timezone.now_for_db() - timedelta(seconds=30)
    device = SimpleNamespace(
        id=77,
        device_code="ARM-01",
        device_name="机械臂 01",
        device_role="ROBOT_ARM",
        role_index=1,
        upstream_device_id=None,
        work_line_id=22,
        device_status="IDLE",
        maintenance_mode=False,
        current_command_id=None,
        last_heartbeat_at=None,
        error_code=None,
    )
    missing_created_at_outbox = SimpleNamespace(
        status=SystemOutboxStatus.BLOCKED_RESOURCE,
        blocked_device_id=77,
        target_code="ARM-01",
        blocked_reason="MISSING_CREATED_AT",
        blocked_at=blocked_at,
        blocked_check_count=1,
        blocked_detail_json={},
        payload_json={"command_code": "CMD-MISSING-CREATED-AT"},
        created_at=None,
    )
    dated_outbox = SimpleNamespace(
        status=SystemOutboxStatus.BLOCKED_RESOURCE,
        blocked_device_id=77,
        target_code="ARM-01",
        blocked_reason="DATED_HEAD",
        blocked_at=blocked_at,
        blocked_check_count=2,
        blocked_detail_json={},
        payload_json={"command_code": "CMD-DATED"},
        created_at=blocked_at,
    )
    db = SimpleNamespace(execute=AsyncMock(return_value=_ResultStub([missing_created_at_outbox, dated_outbox])))

    projection = await service._load_blocked_outbox_projection(db, [device])

    assert projection.count_by_device_id[77] == 2
    assert projection.command_codes_by_device_id[77] == {"CMD-MISSING-CREATED-AT", "CMD-DATED"}
    assert projection.summary_by_device_id[77]["blocked_reason"] == "DATED_HEAD"
