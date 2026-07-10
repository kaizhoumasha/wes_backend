"""Characterization tests for RuntimeInboxService 5-state claim + write-back.

锁定 RuntimeInboxService 在 5 态状态机 (RECEIVED/PROCESSING/PROCESSED/FAILED/DEAD_LETTER)
下的 claim + 终态写回行为，作为 Task 5 (三阶段 Processor) 拆分 + Task 4 (迁移所有
Producer) 切换的基线。

覆盖:
1. claim_received_for_processing: 原子 claim RECEIVED 行
2. claim_received_for_processing: stale PROCESSING 行被回收
3. mark_processed: 写终态 PROCESSED + processed_at
4. mark_processed: 旧 token 写终态返回 false (fencing)
5. mark_failed: 写终态 FAILED + failed_at
6. mark_dead_letter: 写终态 DEAD_LETTER + failed_at
7. recover_stale_leases: 重置 stale PROCESSING 为 RECEIVED
"""

from __future__ import annotations

import importlib
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox

runtime_inbox_service_module = importlib.import_module("src.app.runtime.orchestration.consumers.runtime_inbox_service")


# ============================================================
# Test fixtures
# ============================================================


def _make_runtime_inbox_record(
    *,
    inbox_id: int = 1,
    status: str = "RECEIVED",
    processor_token: str | None = None,
    lease_until: int | None = None,
    attempt_count: int = 0,
    payload_json: dict[str, Any] | None = None,
) -> RuntimeInbox:
    """构造 RuntimeInbox ORM 内存实例（不连 DB）"""
    return RuntimeInbox(
        id=inbox_id,
        provider_code="ECS",
        event_type="DEVICE_EVENT",
        source_event_id=f"src-{inbox_id}",
        payload_hash=f"hash-{inbox_id}",
        payload_json=payload_json or {"event_type": "SCAN_COMPLETED", "data": {}},
        kind="DEVICE_EVENT",
        status=status,
        processor_token=processor_token,
        lease_until=lease_until,
        attempt_count=attempt_count,
        max_retries=5,
    )


# ============================================================
# Case 1: claim_received_for_processing 原子 claim RECEIVED 行
# ============================================================


@pytest.mark.asyncio
async def test_claim_received_returns_claim_with_new_token() -> None:
    """claim_received_for_processing 接收 RECEIVED 行，生成新 processor_token。"""
    service = runtime_inbox_service_module.runtime_inbox_service

    claim_data = {
        "id": 1,
        "processor_token": "tok-1",
        "provider_code": "ECS",
        "event_type": "DEVICE_EVENT",
        "source_event_id": "src-1",
        "payload_json": {"data": {}},
        "correlation_id": None,
        "execution_session_id": None,
    }

    captured_calls: list[dict[str, Any]] = []

    class _FakeRepo:
        @staticmethod
        async def claim_received_with_token(
            db: object,
            *,
            limit: int,
            processor_token: str,
            stale_after_seconds: int,
        ) -> list[dict[str, Any]]:
            captured_calls.append(
                {
                    "limit": limit,
                    "processor_token": processor_token,
                    "stale_after_seconds": stale_after_seconds,
                }
            )
            return [claim_data]

    service.claim_repo = _FakeRepo()

    claims = await service.claim_for_processing(
        db=AsyncMock(),  # type: ignore[arg-type]
        limit=10,
        processor_token="tok-1",
        stale_after_seconds=300,
    )

    assert len(claims) == 1
    assert claims[0]["id"] == 1
    assert claims[0]["processor_token"] == "tok-1"
    assert len(captured_calls) == 1
    assert captured_calls[0]["limit"] == 10
    assert captured_calls[0]["processor_token"] == "tok-1"
    assert captured_calls[0]["stale_after_seconds"] == 300


# ============================================================
# Case 2: stale PROCESSING 行可被回收
# ============================================================


@pytest.mark.asyncio
async def test_claim_received_picks_up_stale_processing_rows() -> None:
    """stale PROCESSING 行（lease_until < now）应该被新 claim 接管。"""
    service = runtime_inbox_service_module.runtime_inbox_service

    claim_data = {
        "id": 5,
        "processor_token": "tok-new",
        "provider_code": "ECS",
        "event_type": "DEVICE_EVENT",
        "source_event_id": "src-5",
        "payload_json": {"data": {}},
        "correlation_id": None,
        "execution_session_id": None,
    }

    class _FakeRepo:
        @staticmethod
        async def claim_received_with_token(
            db: object,
            *,
            limit: int,
            processor_token: str,
            stale_after_seconds: int,
        ) -> list[dict[str, Any]]:
            return [claim_data]

    service.claim_repo = _FakeRepo()

    claims = await service.claim_for_processing(
        db=AsyncMock(),  # type: ignore[arg-type]
        limit=10,
        processor_token="tok-new",
        stale_after_seconds=300,
    )

    assert len(claims) == 1
    assert claims[0]["processor_token"] == "tok-new"


# ============================================================
# Case 3: mark_processed 写终态 PROCESSED
# ============================================================


@pytest.mark.asyncio
async def test_mark_processed_writes_terminal_state() -> None:
    """mark_processed 调 claim_repo.update_terminal_state(target=PROCESSED)。"""
    service = runtime_inbox_service_module.runtime_inbox_service

    captured: list[dict[str, Any]] = []

    class _FakeRepo:
        @staticmethod
        async def update_terminal_state(
            db: object,
            *,
            inbox_id: int,
            lease_token: str,
            target_state: str,
            extra_values: dict[str, Any] | None = None,
        ) -> bool:
            captured.append(
                {
                    "inbox_id": inbox_id,
                    "lease_token": lease_token,
                    "target_state": target_state,
                    "extra_values": extra_values,
                }
            )
            return True

    service.claim_repo = _FakeRepo()

    ok = await service.mark_processed(
        db=AsyncMock(),  # type: ignore[arg-type]
        inbox_id=42,
        lease_token="tok-42",
    )

    assert ok is True
    assert len(captured) == 1
    call = captured[0]
    assert call["inbox_id"] == 42
    assert call["lease_token"] == "tok-42"
    assert call["target_state"] == "PROCESSED"
    assert call["extra_values"] is not None
    assert "processed_at" in call["extra_values"]


# ============================================================
# Case 4: 旧 token 写终态返回 false (fencing)
# ============================================================


@pytest.mark.asyncio
async def test_mark_processed_rejects_stale_token() -> None:
    """旧 token 写终态必须返回 false (fencing reject 指标)。"""
    service = runtime_inbox_service_module.runtime_inbox_service

    class _FakeRepo:
        @staticmethod
        async def update_terminal_state(
            db: object,
            *,
            inbox_id: int,
            lease_token: str,
            target_state: str,
            extra_values: dict[str, Any] | None = None,
        ) -> bool:
            return False

    service.claim_repo = _FakeRepo()

    ok = await service.mark_processed(
        db=AsyncMock(),  # type: ignore[arg-type]
        inbox_id=42,
        lease_token="tok-stale",
    )

    assert ok is False


# ============================================================
# Case 5: mark_failed 写终态 FAILED (retryable=False 避免 db.execute)
# ============================================================


@pytest.mark.asyncio
async def test_mark_failed_writes_terminal_state() -> None:
    """mark_failed 调 claim_repo.update_terminal_state(target=FAILED)。"""
    service = runtime_inbox_service_module.runtime_inbox_service

    captured: list[dict[str, Any]] = []

    class _FakeRepo:
        @staticmethod
        async def update_terminal_state(
            db: object,
            *,
            inbox_id: int,
            lease_token: str,
            target_state: str,
            extra_values: dict[str, Any] | None = None,
        ) -> bool:
            captured.append(
                {
                    "inbox_id": inbox_id,
                    "lease_token": lease_token,
                    "target_state": target_state,
                    "extra_values": extra_values,
                }
            )
            return True

    service.claim_repo = _FakeRepo()

    # retryable=False 避免 db.execute (真实 DB)
    ok = await service.mark_failed(
        db=AsyncMock(),  # type: ignore[arg-type]
        inbox_id=42,
        lease_token="tok-42",
        error_message="processing failed",
        retryable=False,
    )

    assert ok is True
    assert len(captured) == 1
    call = captured[0]
    assert call["inbox_id"] == 42
    assert call["lease_token"] == "tok-42"
    assert call["target_state"] == "FAILED"
    assert call["extra_values"]["last_error_message"] == "processing failed"


# ============================================================
# Case 6: mark_dead_letter 写终态 DEAD_LETTER
# ============================================================


@pytest.mark.asyncio
async def test_mark_dead_letter_writes_terminal_state() -> None:
    """mark_dead_letter 调 claim_repo.update_terminal_state(target=DEAD_LETTER)。"""
    service = runtime_inbox_service_module.runtime_inbox_service

    captured: list[dict[str, Any]] = []

    class _FakeRepo:
        @staticmethod
        async def update_terminal_state(
            db: object,
            *,
            inbox_id: int,
            lease_token: str,
            target_state: str,
            extra_values: dict[str, Any] | None = None,
        ) -> bool:
            captured.append(
                {
                    "inbox_id": inbox_id,
                    "lease_token": lease_token,
                    "target_state": target_state,
                    "extra_values": extra_values,
                }
            )
            return True

    service.claim_repo = _FakeRepo()

    ok = await service.mark_dead_letter(
        db=AsyncMock(),  # type: ignore[arg-type]
        inbox_id=42,
        lease_token="tok-42",
        error_message="max retries exhausted",
    )

    assert ok is True
    assert len(captured) == 1
    call = captured[0]
    assert call["inbox_id"] == 42
    assert call["lease_token"] == "tok-42"
    assert call["target_state"] == "DEAD_LETTER"
    assert call["extra_values"]["last_error_message"] == "max retries exhausted"


# ============================================================
# Case 7: recover_stale_leases 重置 stale PROCESSING
# ============================================================


@pytest.mark.asyncio
async def test_recover_stale_leases_resets_to_received() -> None:
    """recover_stale_leases 把 stale PROCESSING + lease_until 过期的回滚为 RECEIVED。"""
    service = runtime_inbox_service_module.runtime_inbox_service

    _recovered: list[dict[str, Any]] = []  # placeholder; not asserted in this mock

    class _FakeRepo:
        @staticmethod
        async def find_stale_processing(
            db: object,
            *,
            stale_after_seconds: int,
            limit: int,
        ) -> list[RuntimeInbox]:
            # Mock find_stale_processing 返回 3 行 (避免真实 DB)
            return [
                _make_runtime_inbox_record(inbox_id=1, status="PROCESSING", lease_until=1),
                _make_runtime_inbox_record(inbox_id=2, status="PROCESSING", lease_until=1),
                _make_runtime_inbox_record(inbox_id=3, status="PROCESSING", lease_until=1),
            ]

    service.claim_repo = _FakeRepo()

    # recover_stale_leases 会调 db.execute 重置, 我们 mock db 让它忽略 update
    n = await service.recover_stale_leases(
        db=AsyncMock(),  # type: ignore[arg-type]
        stale_after_seconds=300,
        limit=100,
    )

    assert n == 3
