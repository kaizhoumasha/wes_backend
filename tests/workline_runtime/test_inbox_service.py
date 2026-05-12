"""WorklineInbox Service 单元测试（纯逻辑测试）

注意：数据库集成测试需要真实的 PostgreSQL 环境（Docker 中的 wes_postgres），
应该作为集成测试单独运行。本文件只测试纯逻辑，不依赖数据库。

参考文档：
- docs/workline_plugin_architecture_design.md 第 6.3.1 节（幂等性设计）
- docs/workline_plugin_architecture_design.md 第 8.7 节（收件箱模式）
"""

import hashlib
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from src.app.workline.models.inbox import InboxKind, InboxStatus, SourceSystem
from src.app.workline.repositories.inbox_repository import WorklineInboxRepository
from src.app.workline.services.inbox_service import WorklineInboxService

# ==================== 测试幂等键计算逻辑 ====================


def test_calculate_device_event_idempotency_key_with_vendor_id():
    """测试设备事件幂等键计算（有厂商事件 ID）"""
    repository = WorklineInboxRepository()

    # 场景 1：有厂商事件 ID（优先使用）
    key = repository.calculate_device_event_idempotency_key(
        device_code="SCANNER_01",
        event_type="MATERIAL_ARRIVED",
        timestamp=1702627300000,
        data={"event_id": "VENDOR-EVT-12345", "barcode": "PKG12345678"},
    )

    assert key == "device_event:VENDOR-EVT-12345"


def test_calculate_device_event_idempotency_key_without_vendor_id():
    """测试设备事件幂等键计算（无厂商事件 ID）"""
    repository = WorklineInboxRepository()

    # 场景 2：无厂商事件 ID（使用 hash）
    key = repository.calculate_device_event_idempotency_key(
        device_code="SCANNER_01",
        event_type="MATERIAL_ARRIVED",
        timestamp=1702627300000,
        data={"barcode": "PKG12345678", "location": "STATION_04"},
    )

    # 验证格式：device_code:event_type:timestamp:payload_hash
    assert key.startswith("device_event:SCANNER_01:MATERIAL_ARRIVED:1702627300000:")
    # 验证 hash 长度（MD5 前 8 位）
    hash_part = key.split(":")[-1]
    assert len(hash_part) == 8


def test_calculate_device_event_idempotency_key_hash_consistency():
    """测试设备事件幂等键 hash 一致性"""
    repository = WorklineInboxRepository()

    data1 = {"barcode": "PKG12345678", "location": "STATION_04"}
    data2 = {"location": "STATION_04", "barcode": "PKG12345678"}  # 顺序不同

    key1 = repository.calculate_device_event_idempotency_key(
        device_code="SCANNER_01",
        event_type="MATERIAL_ARRIVED",
        timestamp=1702627300000,
        data=data1,
    )

    key2 = repository.calculate_device_event_idempotency_key(
        device_code="SCANNER_01",
        event_type="MATERIAL_ARRIVED",
        timestamp=1702627300000,
        data=data2,
    )

    # 相同数据应该生成相同 hash（sorted 保证顺序无关）
    assert key1 == key2


def test_calculate_command_result_idempotency_key():
    """测试指令结果幂等键计算"""
    repository = WorklineInboxRepository()

    key = repository.calculate_command_result_idempotency_key(
        command_code="CMD-20251215-1001",
        result="SUCCESS",
        finish_time=1702627250000,
        data={"actual_qty": 10, "scan_result": "PKG-X-99"},
    )

    # 验证格式：command_result:command_code:result:finish_time:payload_hash
    assert key.startswith("command_result:CMD-20251215-1001:SUCCESS:1702627250000:")
    # 验证 hash 长度
    hash_part = key.split(":")[-1]
    assert len(hash_part) == 8


def test_idempotency_key_collision_prevention():
    """测试幂等键防碰撞：不同数据应生成不同键"""
    repository = WorklineInboxRepository()

    key1 = repository.calculate_device_event_idempotency_key(
        device_code="SCANNER_01",
        event_type="MATERIAL_ARRIVED",
        timestamp=1702627300000,
        data={"barcode": "PKG12345678"},
    )

    key2 = repository.calculate_device_event_idempotency_key(
        device_code="SCANNER_01",
        event_type="MATERIAL_ARRIVED",
        timestamp=1702627300000,
        data={"barcode": "PKG12345679"},  # 不同的 barcode
    )

    # 不同数据应该生成不同幂等键
    assert key1 != key2


def test_vendor_id_has_priority_over_hash():
    """测试厂商事件 ID 优先级高于 hash 计算"""
    repository = WorklineInboxRepository()

    # 即使 payload 不同，有厂商 ID 也应该直接使用
    key1 = repository.calculate_device_event_idempotency_key(
        device_code="SCANNER_01",
        event_type="MATERIAL_ARRIVED",
        timestamp=1702627300000,
        data={"event_id": "VENDOR-123", "value": "A"},
    )

    key2 = repository.calculate_device_event_idempotency_key(
        device_code="SCANNER_01",
        event_type="MATERIAL_ARRIVED",
        timestamp=1702627300000,
        data={"event_id": "VENDOR-123", "value": "B"},  # payload 不同
    )

    # 应该都使用厂商 ID，忽略 payload
    assert key1 == key2 == "device_event:VENDOR-123"


def test_payload_hash_algorithm():
    """验证 payload hash 算法正确性（MD5 前 8 位）"""
    repository = WorklineInboxRepository()

    key = repository.calculate_device_event_idempotency_key(
        device_code="TEST",
        event_type="TEST_EVENT",
        timestamp=1000,
        data={"key": "value"},
    )

    # 手动计算预期的 hash
    # sorted(data.items()) → [('key', 'value')]
    # str(...) → "[('key', 'value')]"
    payload_str = str(sorted({"key": "value"}.items()))
    expected_hash = hashlib.md5(payload_str.encode()).hexdigest()[:8]  # noqa: S324

    assert key.endswith(expected_hash)


@pytest.mark.asyncio
async def test_create_idempotent_conflict_target_matches_partial_unique_index(monkeypatch: pytest.MonkeyPatch) -> None:
    """PostgreSQL ON CONFLICT 必须匹配迁移中的部分唯一索引。"""
    repository = WorklineInboxRepository()
    captured: dict[str, object] = {}

    class _FakeResult:
        def scalar_one_or_none(self) -> int:
            return 1

    class _FakeDB:
        async def execute(self, statement: object) -> _FakeResult:
            captured["statement"] = statement
            return _FakeResult()

    async def _fake_get_by_id(_db: object, inbox_id: int) -> SimpleNamespace:
        return SimpleNamespace(id=inbox_id)

    monkeypatch.setattr(repository, "get_by_id", _fake_get_by_id)

    _ = await repository.create_idempotent(
        _FakeDB(),  # type: ignore[arg-type]
        {"idempotency_key": "timer_timeout:session:1"},
        idempotency_key="timer_timeout:session:1",
    )

    statement = captured["statement"]
    sql = str(statement.compile(dialect=postgresql.dialect()))  # type: ignore[attr-defined]

    assert "ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL" in sql


@pytest.mark.asyncio
async def test_get_new_messages_only_selects_retry_ready_messages() -> None:
    repository = WorklineInboxRepository()

    class _FakeResult:
        def scalars(self) -> "_FakeResult":
            return self

        def all(self) -> list[object]:
            return []

    class _FakeDB:
        statement = None

        async def execute(self, statement):
            self.statement = statement
            return _FakeResult()

    db = _FakeDB()

    await repository.get_new_messages(db, limit=10)

    assert db.statement is not None
    sql = str(db.statement)
    assert "status = :status_1 OR wes_biz.workline_inbox.status = :status_2 AND" in sql
    assert "status = :status_1 OR wes_biz.workline_inbox.status = :status_2 OR" not in sql


class _FakeInboxRepo:
    def __init__(self, inbox: object | None) -> None:
        self.inbox = inbox
        self.update_calls: list[tuple[object, int, dict[str, object]]] = []
        self.created_data: dict[str, object] | None = None
        self.create_idempotent_calls: list[tuple[object, dict[str, object], str]] = []

    def calculate_command_result_idempotency_key(
        self,
        *,
        command_code: str,
        result: str,
        finish_time: int,
        data: dict[str, object],
    ) -> str:
        _ = result, finish_time, data
        return f"command_result:{command_code}"

    async def get_by_id(self, db: object, inbox_id: int) -> object | None:
        return self.inbox

    async def get_by_idempotency_key(self, db: object, idempotency_key: str) -> object | None:
        _ = db, idempotency_key
        return None

    async def create(self, db: object, data: dict[str, object]) -> object:
        self.created_data = data
        return SimpleNamespace(id=99, **data)

    async def create_idempotent(
        self,
        db: object,
        data: dict[str, object],
        *,
        idempotency_key: str,
    ) -> object:
        self.create_idempotent_calls.append((db, data, idempotency_key))
        self.created_data = data
        return SimpleNamespace(id=100, **data)

    async def update(self, db: object, inbox_id: int, data: dict[str, object]) -> object:
        self.update_calls.append((db, inbox_id, data))
        return SimpleNamespace(id=inbox_id, **data)


@pytest.mark.asyncio
async def test_mark_as_processing_updates_by_id_with_payload() -> None:
    service = WorklineInboxService()
    fake_repo = _FakeInboxRepo(inbox=SimpleNamespace(id=1))
    service.repo = fake_repo  # type: ignore[assignment]

    db = object()
    result = await service.mark_as_processing(db, 1, "worker-1")

    assert fake_repo.update_calls == [
        (
            db,
            1,
            {
                "status": InboxStatus.PROCESSING,
                "processor_token": "worker-1",
            },
        )
    ]
    assert result.status == InboxStatus.PROCESSING
    assert result.processor_token == "worker-1"


@pytest.mark.asyncio
async def test_mark_as_failed_schedules_retry_with_backoff() -> None:
    service = WorklineInboxService()
    fake_repo = _FakeInboxRepo(inbox=SimpleNamespace(id=2, attempt_count=0, max_attempts=3))
    service.repo = fake_repo  # type: ignore[assignment]

    db = object()
    result = await service.mark_as_failed(db, 2, "boom")

    assert len(fake_repo.update_calls) == 1
    _, inbox_id, data = fake_repo.update_calls[0]
    assert inbox_id == 2
    assert data["status"] == InboxStatus.RETRY
    assert data["error_message"] == "boom"
    assert data["attempt_count"] == 1
    assert data["next_retry_at"] is not None
    assert data["processed_at"] is not None
    assert result.status == InboxStatus.RETRY


@pytest.mark.asyncio
async def test_mark_as_failed_moves_to_dead_letter_after_max_attempts() -> None:
    service = WorklineInboxService()
    fake_repo = _FakeInboxRepo(inbox=SimpleNamespace(id=3, attempt_count=3, max_attempts=3))
    service.repo = fake_repo  # type: ignore[assignment]

    db = object()
    result = await service.mark_as_failed(db, 3, "boom")

    assert len(fake_repo.update_calls) == 1
    _, inbox_id, data = fake_repo.update_calls[0]
    assert inbox_id == 3
    assert data["status"] == InboxStatus.DEAD_LETTER
    assert data["error_message"] == "boom"
    assert "attempt_count" not in data
    assert "next_retry_at" not in data
    assert data["processed_at"] is not None
    assert result.status == InboxStatus.DEAD_LETTER


@pytest.mark.asyncio
async def test_mark_as_processed_clears_retry_error_projection() -> None:
    service = WorklineInboxService()
    fake_repo = _FakeInboxRepo(
        inbox=SimpleNamespace(
            id=4,
            status=InboxStatus.RETRY,
            error_message="old transition error",
            next_retry_at=datetime.now(),
            processor_token="worker-1",
        )
    )
    service.repo = fake_repo  # type: ignore[assignment]

    db = object()
    result = await service.mark_as_processed(db, 4)

    assert len(fake_repo.update_calls) == 1
    _, inbox_id, data = fake_repo.update_calls[0]
    assert inbox_id == 4
    assert data["status"] == InboxStatus.PROCESSED
    assert data["error_message"] is None
    assert data["next_retry_at"] is None
    assert data["processor_token"] is None
    assert data["processed_at"] is not None
    assert result.status == InboxStatus.PROCESSED


@pytest.mark.asyncio
async def test_mark_as_dead_letter_clears_retry_error_projection() -> None:
    service = WorklineInboxService()
    fake_repo = _FakeInboxRepo(
        inbox=SimpleNamespace(
            id=5,
            status=InboxStatus.RETRY,
            error_message="old retryable error",
            next_retry_at=datetime.now(),
            processor_token="worker-1",
        )
    )
    service.repo = fake_repo  # type: ignore[assignment]

    db = object()
    result = await service.mark_as_dead_letter(db, 5, "terminal data conflict")

    assert len(fake_repo.update_calls) == 1
    _, inbox_id, data = fake_repo.update_calls[0]
    assert inbox_id == 5
    assert data["status"] == InboxStatus.DEAD_LETTER
    assert data["error_message"] == "terminal data conflict"
    assert data["next_retry_at"] is None
    assert data["processor_token"] is None
    assert data["processed_at"] is not None
    assert result.status == InboxStatus.DEAD_LETTER


@pytest.mark.asyncio
async def test_mark_as_processed_raises_when_message_missing() -> None:
    service = WorklineInboxService()
    fake_repo = _FakeInboxRepo(inbox=None)
    service.repo = fake_repo  # type: ignore[assignment]

    with pytest.raises(ValueError, match="消息不存在: 99"):
        await service.mark_as_processed(object(), 99)


@pytest.mark.asyncio
async def test_create_command_result_inbox_uses_command_result_kind() -> None:
    service = WorklineInboxService()
    fake_repo = _FakeInboxRepo(inbox=None)
    service.repo = fake_repo  # type: ignore[assignment]

    result = await service.create_command_result_inbox(
        db=object(),
        command_code="CMD-001",
        device_code="ARM_01",
        result="SUCCESS",
        finish_time=1702627250000,
        data={"foo": "bar"},
        command_type="PICK_AND_PUT",
        error_detail={"message": "ok"},
        source_message_id="req-001",
        trace_id="trace-001",
    )

    assert result.id == 99
    assert fake_repo.created_data is not None
    assert fake_repo.created_data["kind"] == InboxKind.COMMAND_RESULT
    assert fake_repo.created_data["source_system"] == SourceSystem.DEVICE
    assert fake_repo.created_data["source_message_id"] == "req-001"
    assert fake_repo.created_data["trace_id"] == "trace-001"
    assert fake_repo.created_data["payload_json"]["command_type"] == "PICK_AND_PUT"
    assert fake_repo.created_data["payload_json"]["error_detail"] == {"message": "ok"}


@pytest.mark.asyncio
async def test_create_command_result_inbox_auto_commits_by_default() -> None:
    service = WorklineInboxService()
    fake_repo = _FakeInboxRepo(inbox=None)
    service.repo = fake_repo  # type: ignore[assignment]

    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())

    _ = await service.create_command_result_inbox(
        db=db,
        command_code="CMD-002",
        device_code="ARM_02",
        result="SUCCESS",
        finish_time=1702627250001,
        data={"foo": "bar"},
    )

    db.commit.assert_awaited_once()
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_timeout_inbox_uses_idempotent_insert_without_rollback() -> None:
    service = WorklineInboxService()
    fake_repo = _FakeInboxRepo(inbox=None)
    service.repo = fake_repo  # type: ignore[assignment]
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())

    result = await service.create_timeout_inbox(
        db,
        session_id=42,
        workline_id=7,
        deadline_at=datetime(2026, 5, 8, 8, 0, 0),
        wait_token="CMD-1",
        awaiting_command_id=9,
        auto_commit=False,
    )

    assert result.id == 100
    assert fake_repo.create_idempotent_calls
    assert fake_repo.created_data is not None
    assert fake_repo.created_data["idempotency_key"] == "timeout:42:2026-05-08T08:00:00:CMD-1:9"
    db.commit.assert_not_awaited()
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_as_processing_can_skip_auto_commit() -> None:
    service = WorklineInboxService()
    fake_repo = _FakeInboxRepo(inbox=SimpleNamespace(id=1))
    service.repo = fake_repo  # type: ignore[assignment]

    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())

    _ = await service.mark_as_processing(db, 1, "worker-2", auto_commit=False)

    db.commit.assert_not_awaited()
    db.rollback.assert_not_awaited()
