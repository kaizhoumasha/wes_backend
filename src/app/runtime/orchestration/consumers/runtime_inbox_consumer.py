"""RuntimeInboxConsumer — RuntimeInbox 单点入口 (Phase 2 burn-down 阶段 2 C1)。

主计划 §3.5.1 + R-WLR 严格型唯一允许 consumer:
- 接收 inbound_registry + normalizer_context + correlation + consumer_id
- consume_sync 委托给 src.app.workline.services.inbox_batch_processor
  (wlr 内部既有实现, lazy import 阶段 3 前的过渡)
- callback ACK 权威已切到 RuntimeInbox; 这里仅保留 legacy inbox/processor
  的过渡消费职责, 不承担 ACK/source-of-truth 语义
- 不实现状态机 / idempotency / RuntimeHold 推进 (阶段 3 业务迁移)
- list_consumed_ids 返回只读视图
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

    from src.app.runtime.inbound_normalizer_registry import InboundNormalizerRegistry
    from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
    from src.app.runtime.orchestration.runtime_inbox import RuntimeInboxRecord

# 已消费 source_event_id 的环形缓冲上限,防止消费者长跑导致 list 无限增长。
_MAX_TRACKED_IDS = 10_000


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
        self._consumed_ids: deque[str] = deque(maxlen=_MAX_TRACKED_IDS)

    async def consume(self, payload: Mapping[str, Any]) -> RuntimeInboxRecord:
        # 阶段 3 实现真正的异步状态机推进。
        # 当前委托给既有 workline 同步实现, 返回 RuntimeInbox 记录占位 dict。
        return self.consume_sync(payload)  # type: ignore[return-value]

    def consume_sync(self, payload: Mapping[str, Any]) -> Any:
        # Lazy import:避免循环依赖。阶段 6 workline 域退化为配置域,
        # InboxBatchProcessor 已迁入 runtime/orchestration/services/inbox/。
        from src.app.runtime.orchestration.services.inbox import inbox_batch_processor

        # 注入 consumer_id 用于追溯; 若 payload 已带 consumer_id 则保留调用方值。
        payload_dict = dict(payload)
        payload_dict.setdefault("consumer_id", self._consumer_id)
        record = inbox_batch_processor.process_inbox_payload(payload_dict)
        source_event_id = payload.get("source_event_id")
        if isinstance(source_event_id, str) and source_event_id not in self._consumed_ids:
            self._consumed_ids.append(source_event_id)
        return record

    def list_consumed_ids(self) -> tuple[str, ...]:
        """返回已消费 source_event_id 的不可变快照 (防外部 mutate)。"""
        return tuple(self._consumed_ids)


__all__ = ["RuntimeInboxConsumer"]
