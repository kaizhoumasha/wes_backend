"""Phase 2 burn-down 阶段 2 C1 — RuntimeInboxConsumer 最小实现测试。

不验证状态机业务逻辑 (阶段 3 才搬迁) ; 只验证:
- 消费者模块可 import
- 构造函数接收所有 4 个强制依赖
- consume_sync 把 payload 委托给 workline inbox_batch_processor (lazy)
- payload 中 caller 已带 consumer_id 时保留原值, 未带时由 consumer 注入 (P2)
- source_event_id 去重 (N2)
- 已消费 id 列表为只读, 环形缓冲上限生效 (N1)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from src.app.runtime.orchestration.consumers.runtime_inbox_consumer import (
        RuntimeInboxConsumer,
    )


def test_consumer_module_imports() -> None:
    from src.app.runtime.orchestration.consumers import RuntimeInboxConsumer

    assert RuntimeInboxConsumer is not None


def test_consumer_constructor_signals_required_dependencies() -> None:
    """构造函数声明 4 个强制依赖 (inbound_registry, normalizer_context, correlation, consumer_id)"""
    import inspect

    from src.app.runtime.orchestration.consumers import RuntimeInboxConsumer

    sig = inspect.signature(RuntimeInboxConsumer.__init__)
    required = {"inbound_registry", "normalizer_context", "correlation", "consumer_id"}
    actual = set(sig.parameters.keys())
    assert required.issubset(actual), f"缺强制依赖: {required - actual}"


def test_consumer_consume_sync_delegates_to_workline_batch_processor(monkeypatch: pytest.MonkeyPatch) -> None:
    """consume_sync 委托 wlr inbox_batch_processor + 注入 consumer_id。"""
    from src.app.runtime.orchestration.consumers import RuntimeInboxConsumer

    captured: dict[str, Any] = {}

    class _FakeBatchProcessor:
        @staticmethod
        def process_payload(payload: dict) -> dict:
            captured["payload"] = payload
            return {"status": "PROCESSED", "consumer_id": payload.get("consumer_id")}

    # 桩 wlr inbox_batch_processor 在 lazy import 解析
    import src.app.workline.services.inbox_batch_processor as bp_module

    monkeypatch.setattr(bp_module, "process_inbox_payload", _FakeBatchProcessor.process_payload, raising=False)

    registry = object()  # 占位
    ctx = object()  # 占位
    consumer = RuntimeInboxConsumer(
        inbound_registry=registry,  # type: ignore[arg-type]
        normalizer_context=ctx,  # type: ignore[arg-type]
        correlation=object(),  # type: ignore[arg-type]
        consumer_id="my-consumer",
    )

    payload = {"source_event_id": "evt-1", "provider_code": "WMS", "event_type": "X"}
    result = consumer.consume_sync(payload)

    # consumer 注入 consumer_id, 调用方原始 dict 不被 mutate
    assert captured.get("payload") == {
        "source_event_id": "evt-1",
        "provider_code": "WMS",
        "event_type": "X",
        "consumer_id": "my-consumer",
    }
    assert result["consumer_id"] == "my-consumer"
    assert "consumer_id" not in payload


def test_consumer_preserves_caller_supplied_consumer_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """caller payload 已带 consumer_id 时不被 consumer 覆盖 (P2 — setdefault 语义)。"""
    from src.app.runtime.orchestration.consumers import RuntimeInboxConsumer

    captured: dict[str, Any] = {}

    class _FakeBatchProcessor:
        @staticmethod
        def process_payload(payload: dict) -> dict:
            captured["payload"] = payload
            return {"status": "PROCESSED"}

    import src.app.workline.services.inbox_batch_processor as bp_module

    monkeypatch.setattr(bp_module, "process_inbox_payload", _FakeBatchProcessor.process_payload, raising=False)

    consumer = RuntimeInboxConsumer(
        inbound_registry=object(),  # type: ignore[arg-type]
        normalizer_context=object(),  # type: ignore[arg-type]
        correlation=object(),  # type: ignore[arg-type]
        consumer_id="default-consumer",
    )

    consumer.consume_sync({"source_event_id": "evt-1", "consumer_id": "explicit-consumer"})

    assert captured["payload"]["consumer_id"] == "explicit-consumer", (
        "caller payload 中的 consumer_id 必须保留; consumer 不应覆盖调用方明示值"
    )


def test_consumer_dedups_duplicate_source_event_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """同一 source_event_id 多次投递只记录一次 (N2 — 去重)。"""
    from src.app.runtime.orchestration.consumers import RuntimeInboxConsumer

    class _FakeBatchProcessor:
        @staticmethod
        def process_payload(payload: dict) -> dict:
            return {"status": "PROCESSED"}

    import src.app.workline.services.inbox_batch_processor as bp_module

    monkeypatch.setattr(bp_module, "process_inbox_payload", _FakeBatchProcessor.process_payload, raising=False)

    consumer = RuntimeInboxConsumer(
        inbound_registry=object(),  # type: ignore[arg-type]
        normalizer_context=object(),  # type: ignore[arg-type]
        correlation=object(),  # type: ignore[arg-type]
        consumer_id="dedup-consumer",
    )

    for _ in range(5):
        consumer.consume_sync({"source_event_id": "evt-dup", "provider_code": "WMS"})

    consumed = consumer.list_consumed_ids()
    assert consumed == ("evt-dup",), f"source_event_id 必须去重; actual={consumed}"


def test_consumer_list_consumed_ids_returns_immutable_view() -> None:
    """list_consumed_ids 返回只读视图 (不暴露内部 mutable deque)。"""
    from src.app.runtime.orchestration.consumers import RuntimeInboxConsumer

    consumer = RuntimeInboxConsumer(
        inbound_registry=object(),  # type: ignore[arg-type]
        normalizer_context=object(),  # type: ignore[arg-type]
        correlation=object(),  # type: ignore[arg-type]
        consumer_id="test",
    )
    view = consumer.list_consumed_ids()
    with pytest.raises((TypeError, AttributeError)):
        view.append("x")  # type: ignore[attr-defined]


def test_consumer_consumed_ids_bounded_by_ring_buffer(monkeypatch: pytest.MonkeyPatch) -> None:
    """环形缓冲上限生效 (N1 — 防止长跑消费者 list 无限增长)。"""
    from src.app.runtime.orchestration.consumers import RuntimeInboxConsumer
    from src.app.runtime.orchestration.consumers.runtime_inbox_consumer import (
        _MAX_TRACKED_IDS,
    )

    class _FakeBatchProcessor:
        @staticmethod
        def process_payload(payload: dict) -> dict:
            return {"status": "PROCESSED"}

    import src.app.workline.services.inbox_batch_processor as bp_module

    monkeypatch.setattr(bp_module, "process_inbox_payload", _FakeBatchProcessor.process_payload, raising=False)

    consumer = RuntimeInboxConsumer(
        inbound_registry=object(),  # type: ignore[arg-type]
        normalizer_context=object(),  # type: ignore[arg-type]
        correlation=object(),  # type: ignore[arg-type]
        consumer_id="ringbuf-consumer",
    )

    # 投递 _MAX_TRACKED_IDS + 50 条不同 id 的 payload
    for i in range(_MAX_TRACKED_IDS + 50):
        consumer.consume_sync({"source_event_id": f"evt-{i}", "provider_code": "WMS"})

    consumed = consumer.list_consumed_ids()
    assert len(consumed) == _MAX_TRACKED_IDS, f"环形缓冲必须保留最后 {_MAX_TRACKED_IDS} 条; actual={len(consumed)}"
    # 最旧的 50 条 (evt-0..evt-49) 被驱逐, 最早的应是 evt-50
    assert consumed[0] == "evt-50"
    assert consumed[-1] == f"evt-{_MAX_TRACKED_IDS + 49}"


def test_consumer_ignores_non_string_source_event_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """source_event_id 不是 str 时不写入 consumed_ids (类型守护)。"""
    from src.app.runtime.orchestration.consumers import RuntimeInboxConsumer

    class _FakeBatchProcessor:
        @staticmethod
        def process_payload(payload: dict) -> dict:
            return {"status": "PROCESSED"}

    import src.app.workline.services.inbox_batch_processor as bp_module

    monkeypatch.setattr(bp_module, "process_inbox_payload", _FakeBatchProcessor.process_payload, raising=False)

    consumer = RuntimeInboxConsumer(
        inbound_registry=object(),  # type: ignore[arg-type]
        normalizer_context=object(),  # type: ignore[arg-type]
        correlation=object(),  # type: ignore[arg-type]
        consumer_id="guard-consumer",
    )

    consumer.consume_sync({"source_event_id": None})
    consumer.consume_sync({"source_event_id": 42})
    consumer.consume_sync({"source_event_id": ["list"]})

    assert consumer.list_consumed_ids() == ()
