"""outbox_dispatch_service 的 isawaitable 防御行为测试。

`_escalate_status_precheck_wait_if_needed` 与 `_dispatch_blocked_resource_heads`
之前直接 `await updater(...)` / `await getter(...)`,假定 repo 返回 awaitable。
runtime fallback 路径(repo 走同步实现)下会抛 "object dict can't be used in
'await' expression"。

修复后加 `isawaitable` 防御,本文件锁住 sync 与 async 两条路径,作为 isawaitable
fix 的回归护栏。
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.app.runtime.orchestration.services.inbox.outbox_dispatch_service import (
    OutboxDispatchService,
    _escalate_status_precheck_wait_if_needed,
)
from src.app.sys.models.outbox import SystemOutboxStatus
from src.utils.timezone import timezone


def _make_blocked_outbox() -> Any:
    return SimpleNamespace(
        outbox_id=101,
        id=101,
        blocked_reason="DEVICE_STATUS_PRECHECK_WAIT",
        blocked_detail_json={
            "last_probe_result": "ready",
        },
        # blocked_check_count >= DEVICE_STATUS_PRECHECK_MAX_CHECK_COUNT 触发 TTL 早退
        blocked_check_count=30,
        workline_id=1,
        operation_domain="WORKLINE",
        # 数据库存储使用 naive UTC，与生产代码 now_for_db() 的减法保持同类型。
        blocked_at=timezone.now_for_db() - timedelta(seconds=600),
    )


def _make_outbox() -> Any:
    return SimpleNamespace(
        outbox_id=101,
        id=101,
        workline_id=1,
        operation_domain="WORKLINE",
    )


def _build_fake_repo(updater_return: Any) -> Any:
    """repo.update_resource_wait_detail 返回 updater_return(sync 或 awaitable)。"""

    class _Repo:
        def __init__(self) -> None:
            self.update_calls: list[dict[str, Any]] = []

        def update_resource_wait_detail(
            self,
            _db: Any,
            outbox_id: int,
            *,
            expected_reason: str,
            last_error: str,
            detail: dict[str, Any],
        ) -> Any:
            self.update_calls.append(
                {
                    "outbox_id": outbox_id,
                    "expected_reason": expected_reason,
                    "last_error": last_error,
                    "detail": detail,
                }
            )
            return updater_return

    return _Repo()


@pytest.mark.asyncio
async def test_escalate_status_precheck_tolerates_sync_repo_returning_dict() -> None:
    """sync repo 返回 dict 时,`isawaitable` 防御应早退,无 TypeError。"""
    blocked_outbox = _make_blocked_outbox()
    outbox = _make_outbox()
    updated_payload = {"id": 101, "blocked_reason": "DEVICE_STATUS_PRECHECK_WAIT"}
    repo = _build_fake_repo(updater_return=updated_payload)

    with (
        patch(
            "src.app.runtime.orchestration.services.inbox.outbox_dispatch_service._record_diagnostic",
            new=AsyncMock(),
        ) as record_diag,
    ):
        await _escalate_status_precheck_wait_if_needed(
            object(),
            outbox_repo=repo,
            outbox=outbox,
            outbox_id=101,
            blocked_outbox=blocked_outbox,
            message="probe failed",
        )

    # sync 返回时:`_record_diagnostic` 在 `isawaitable` 早退分支不调用
    record_diag.assert_not_called()
    assert len(repo.update_calls) == 1, "update_resource_wait_detail 必须被调用"
    assert repo.update_calls[0]["outbox_id"] == 101


@pytest.mark.asyncio
async def test_escalate_status_precheck_tolerates_async_repo_returning_dict() -> None:
    """async repo 返回 dict 时,`isawaitable` 通过,正常 await + record diagnostic。"""
    blocked_outbox = _make_blocked_outbox()
    outbox = _make_outbox()
    updated_payload = {"id": 101, "blocked_reason": "DEVICE_STATUS_PRECHECK_WAIT"}

    class _AsyncRepo:
        def __init__(self) -> None:
            self.update_calls: list[dict[str, Any]] = []

        async def update_resource_wait_detail(
            self,
            _db: Any,
            outbox_id: int,
            *,
            expected_reason: str,
            last_error: str,
            detail: dict[str, Any],
        ) -> dict[str, Any]:
            self.update_calls.append(
                {
                    "outbox_id": outbox_id,
                    "expected_reason": expected_reason,
                    "last_error": last_error,
                    "detail": detail,
                }
            )
            return updated_payload

    repo = _AsyncRepo()

    with (
        patch(
            "src.app.runtime.orchestration.services.inbox.outbox_dispatch_service._record_diagnostic",
            new=AsyncMock(),
        ) as record_diag,
    ):
        await _escalate_status_precheck_wait_if_needed(
            object(),
            outbox_repo=repo,
            outbox=outbox,
            outbox_id=101,
            blocked_outbox=blocked_outbox,
            message="probe failed",
        )

    # async 返回时:`_record_diagnostic` 必须被调用
    record_diag.assert_awaited_once()
    assert len(repo.update_calls) == 1


@pytest.mark.asyncio
async def test_escalate_status_precheck_no_op_when_repo_lacks_updater() -> None:
    """repo 没有 `update_resource_wait_detail` 时,直接 return,无 TypeError。"""
    blocked_outbox = _make_blocked_outbox()
    outbox = _make_outbox()

    class _NoUpdaterRepo:
        pass

    with (
        patch(
            "src.app.runtime.orchestration.services.inbox.outbox_dispatch_service._record_diagnostic",
            new=AsyncMock(),
        ) as record_diag,
    ):
        await _escalate_status_precheck_wait_if_needed(
            object(),
            outbox_repo=_NoUpdaterRepo(),
            outbox=outbox,
            outbox_id=101,
            blocked_outbox=blocked_outbox,
            message="probe failed",
        )

    record_diag.assert_not_called()


def test_outbox_dispatch_service_dispatch_blocked_resource_heads_tolerates_sync_getter() -> None:
    """`_dispatch_blocked_resource_heads` 在 sync getter 时不抛 TypeError。"""
    repo = SimpleNamespace(
        get_probeable_blocked_device_heads=lambda *_, **__: [{"id": 1}],
    )
    service = OutboxDispatchService()
    result: dict[str, int] = {"dispatched": 0, "skipped": 0}

    import asyncio

    processed_device_ids, processed_target_codes = asyncio.run(
        service._dispatch_blocked_resource_heads(
            object(),
            outbox_repo=repo,
            limit=10,
            result=result,
        )
    )

    # sync getter 返回 1 个 outbox,但走 process 时需更多依赖,这里只断言
    # 早退/异常前能进入函数体 — 不抛 TypeError 即视为防御生效
    assert isinstance(processed_device_ids, set)
    assert isinstance(processed_target_codes, set)


@pytest.mark.asyncio
async def test_blocked_resource_head_rechecks_physical_dispatch_fence_after_commits() -> None:
    outbox = SimpleNamespace(
        id=201,
        blocked_device_id=7,
        device_id=7,
        target_code="ECS-7",
        blocked_reason="DEVICE_BUSY",
        provider_profile_identity="ecs.device-command.v1",
        operation_identity="device.command",
        lease_owner_token=None,
        status=SystemOutboxStatus.RETRY_WAIT,
    )
    claimed = SimpleNamespace(**vars(outbox))
    claimed.status = SystemOutboxStatus.DISPATCHING
    repo = SimpleNamespace(
        get_probeable_blocked_device_heads=AsyncMock(return_value=[outbox]),
        claim_blocked_resource_wait_for_dispatch=AsyncMock(return_value=claimed),
        begin_physical_dispatch=AsyncMock(return_value=None),
    )
    service = OutboxDispatchService()
    service._probe_blocked_resource_head_ready = AsyncMock(return_value=True)  # type: ignore[method-assign]
    service._prepare_claimed_blocked_head_dispatch = AsyncMock(  # type: ignore[method-assign]
        return_value=(SimpleNamespace(), False)
    )
    service._dispatch_single = AsyncMock(return_value=True)  # type: ignore[method-assign]
    db = SimpleNamespace(commit=AsyncMock())
    result = {"dispatched": 0, "success": 0, "failed": 0, "skipped": 0}

    await service._dispatch_blocked_resource_heads(
        db,
        outbox_repo=repo,
        limit=1,
        result=result,
    )

    repo.begin_physical_dispatch.assert_awaited_once()
    service._dispatch_single.assert_not_awaited()
    assert result == {"dispatched": 1, "success": 0, "failed": 0, "skipped": 1}


@pytest.mark.asyncio
async def test_blocked_resource_claim_and_attempt_are_committed_atomically() -> None:
    outbox = SimpleNamespace(
        id=202,
        blocked_device_id=8,
        device_id=8,
        target_code="ECS-8",
        blocked_reason="DEVICE_BUSY",
        provider_profile_identity="ecs.device-command.v1",
        operation_identity="device.command",
        lease_owner_token=None,
        status=SystemOutboxStatus.RETRY_WAIT,
    )
    claimed = SimpleNamespace(**vars(outbox))
    claimed.status = SystemOutboxStatus.DISPATCHING
    repo = SimpleNamespace(
        get_probeable_blocked_device_heads=AsyncMock(return_value=[outbox]),
        claim_blocked_resource_wait_for_dispatch=AsyncMock(return_value=claimed),
        begin_physical_dispatch=AsyncMock(return_value=None),
    )
    db = SimpleNamespace(commit=AsyncMock())

    async def prepare_claimed(*_args: object, **_kwargs: object) -> tuple[SimpleNamespace, bool]:
        assert db.commit.await_count == 0
        await db.commit()
        return SimpleNamespace(), False

    service = OutboxDispatchService()
    service._probe_blocked_resource_head_ready = AsyncMock(return_value=True)  # type: ignore[method-assign]
    service._prepare_claimed_blocked_head_dispatch = AsyncMock(side_effect=prepare_claimed)  # type: ignore[method-assign]
    service._dispatch_single = AsyncMock(return_value=True)  # type: ignore[method-assign]
    result = {"dispatched": 0, "success": 0, "failed": 0, "skipped": 0}

    await service._dispatch_blocked_resource_heads(
        db,
        outbox_repo=repo,
        limit=1,
        result=result,
    )

    service._prepare_claimed_blocked_head_dispatch.assert_awaited_once()
    repo.begin_physical_dispatch.assert_awaited_once()
    service._dispatch_single.assert_not_awaited()


@pytest.mark.asyncio
async def test_blocked_resource_final_safety_guard_keeps_transaction_open_for_send_boundary() -> None:
    db = SimpleNamespace(commit=AsyncMock())
    claimed = SimpleNamespace(id=203, workline_id=9)
    attempt = SimpleNamespace()
    attempt_service = SimpleNamespace(create_attempt=AsyncMock(return_value=attempt))
    safety_service = SimpleNamespace(assert_accepting_work=AsyncMock())

    prepared_attempt, blocked = await OutboxDispatchService()._prepare_claimed_blocked_head_dispatch(
        db,
        outbox_repo=SimpleNamespace(),
        claimed=claimed,
        outbox_id=203,
        attempt_service=attempt_service,
        safety_service=safety_service,
        result={"dispatched": 0, "success": 0, "failed": 0, "skipped": 0},
        lease_owner_token="blocked-owner-203",
    )

    assert prepared_attempt is attempt
    assert blocked is False
    # claim + attempt 先提交；final safety guard 的 WorkLine 行锁由 caller
    # 持有到 begin_physical_dispatch 后再一并提交。
    db.commit.assert_awaited_once()
    safety_service.assert_accepting_work.assert_awaited_once_with(db, workline_id=9)
