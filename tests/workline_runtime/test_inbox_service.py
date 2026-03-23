"""WorklineInbox Service 单元测试（纯逻辑测试）

注意：数据库集成测试需要真实的 PostgreSQL 环境（Docker 中的 wes_postgres），
应该作为集成测试单独运行。本文件只测试纯逻辑，不依赖数据库。

参考文档：
- docs/workline_plugin_architecture_design.md 第 6.3.1 节（幂等性设计）
- docs/workline_plugin_architecture_design.md 第 8.7 节（收件箱模式）
"""

import hashlib
from types import SimpleNamespace

import pytest

from src.app.workline.models.inbox import InboxStatus
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


class _FakeInboxRepo:
    def __init__(self, inbox: object | None) -> None:
        self.inbox = inbox
        self.update_calls: list[tuple[object, int, dict[str, object]]] = []

    async def get_by_id(self, db: object, inbox_id: int) -> object | None:
        return self.inbox

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
async def test_mark_as_failed_updates_by_id_with_error_and_processed_at() -> None:
    service = WorklineInboxService()
    fake_repo = _FakeInboxRepo(inbox=SimpleNamespace(id=2))
    service.repo = fake_repo  # type: ignore[assignment]

    db = object()
    result = await service.mark_as_failed(db, 2, "boom")

    assert len(fake_repo.update_calls) == 1
    _, inbox_id, data = fake_repo.update_calls[0]
    assert inbox_id == 2
    assert data["status"] == InboxStatus.FAILED
    assert data["error_message"] == "boom"
    assert data["processed_at"] is not None
    assert result.status == InboxStatus.FAILED


@pytest.mark.asyncio
async def test_mark_as_processed_raises_when_message_missing() -> None:
    service = WorklineInboxService()
    fake_repo = _FakeInboxRepo(inbox=None)
    service.repo = fake_repo  # type: ignore[assignment]

    with pytest.raises(ValueError, match="消息不存在: 99"):
        await service.mark_as_processed(object(), 99)
