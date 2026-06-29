"""RuntimeInboxConsumer — RuntimeInbox 单点入口 (Phase 2 burn-down 阶段 2 C1)。

主计划 §3.5.1 + R-WLR 严格型唯一允许 consumer:
- 接收 inbound_registry + normalizer_context + correlation + consumer_id
- consume_sync 委托给 src.app.workline.services.inbox_batch_processor
  (wlr 内部既有实现, lazy import 阶段 3 前的过渡)
- 不实现状态机 / idempotency / RuntimeHold 推进 (阶段 3 业务迁移)
- list_consumed_ids 返回只读视图

不在 consumers/ 之外的任何 production 路径 import src.workline_runtime。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

    from src.app.runtime.inbound_normalizer_registry import InboundNormalizerRegistry
    from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
    from src.app.runtime.orchestration.runtime_inbox import RuntimeInboxRecord


class RuntimeInboxConsumer:
    """RuntimeInbox 入口消费者 facade (主计划 §3.5.1 + 阶段 2 burn-down C1)。

    不实现 inbox 状态机业务逻辑 (阶段 3 才搬迁) ; 本类作为
    InboundNormalizerContext 唯一合法 consumer 的占位 facade, 委托给
    src.app.workline.services.inbox_batch_processor 既有实现。
    阶段 3 时把内部状态机迁入。
    """

    def __init__(
        self,
        inbound_registry: InboundNormalizerRegistry,
        normalizer_context: Any,
        *,
        correlation: ExecutionCorrelation,
        consumer_id: str,
    ) -> None:
        self._registry = inbound_registry
        self._context = normalizer_context
        self._correlation = correlation
        self._consumer_id = consumer_id
        self._consumed_ids: list[str] = []

    async def consume(self, payload: Mapping[str, Any]) -> RuntimeInboxRecord:
        # 阶段 3 实现真正的异步状态机推进。
        # 当前委托给既有 workline 同步实现, 返回 RuntimeInbox 记录占位 dict。
        return self.consume_sync(payload)  # type: ignore[return-value]

    def consume_sync(self, payload: Mapping[str, Any]) -> Any:
        # Lazy import: 阶段 3 前的过渡, 避免循环依赖。
        from src.app.workline.services import inbox_batch_processor

        record = inbox_batch_processor.process_inbox_payload(dict(payload))
        source_event_id = payload.get("source_event_id")
        if isinstance(source_event_id, str):
            self._consumed_ids.append(source_event_id)
        return record

    def list_consumed_ids(self) -> tuple[str, ...]:
        """返回已消费 source_event_id 的只读视图 (防外部 mutate)。"""
        return tuple(self._consumed_ids)


__all__ = ["RuntimeInboxConsumer"]
