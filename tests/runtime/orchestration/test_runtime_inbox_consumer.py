"""Phase 2 burn-down 阶段 2 C1 — RuntimeInboxConsumer 最小实现测试。

不验证状态机业务逻辑 (阶段 3 才搬迁) ; 只验证:
- 消费者模块可 import
- 构造函数接收所有 4 个强制依赖
- consume_sync 把 payload 委托给 workline inbox_batch_processor (lazy)
- list_consumed_ids 返回只读视图
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
    """consume_sync 委托给 src.app.workline.services.inbox_batch_processor (lazy import)"""
    from src.app.runtime.orchestration.consumers import RuntimeInboxConsumer

    captured: dict[str, Any] = {}

    class _FakeBatchProcessor:
        @staticmethod
        def process_payload(payload: dict) -> dict:
            captured["payload"] = payload
            return {"status": "PROCESSED", "consumer_id": "test"}

    # 桩 wlr inbox_batch_processor 在 lazy import 解析
    import src.app.workline.services.inbox_batch_processor as bp_module

    monkeypatch.setattr(bp_module, "process_inbox_payload", _FakeBatchProcessor.process_payload, raising=False)

    registry = object()  # 占位
    ctx = object()  # 占位
    consumer = RuntimeInboxConsumer(
        inbound_registry=registry,  # type: ignore[arg-type]
        normalizer_context=ctx,  # type: ignore[arg-type]
        correlation=object(),  # type: ignore[arg-type]
        consumer_id="test",
    )

    payload = {"source_event_id": "evt-1", "provider_code": "WMS", "event_type": "X"}
    result = consumer.consume_sync(payload)

    assert captured.get("payload") == payload
    assert result["consumer_id"] == "test"


def test_consumer_list_consumed_ids_returns_immutable_view() -> None:
    """list_consumed_ids 返回只读视图 (不暴露内部 mutable list)"""
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
