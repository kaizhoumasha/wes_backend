"""Runtime reconciliation facade — runtime/orchestration 域对账能力入口 (Phase 2 launch PR)。

本 facade 解决 device/callback 域对 `src.app.workline.services.runtime_reconciliation_service`
的反向依赖:device/callback 不再 import workline 域,而是 import 本 facade (位于
runtime/orchestration 域,作为对账能力的官方归属入口)。

实现策略:
- 当前 facade 仅 re-export workline 单例的两个对外方法 (`record_late_callback_if_pending`
  / `activate_execution_deadline_after_ack`),保持现有 workline service 实现不变。
- Phase 2 burn-down 阶段会把 workline_runtime_reconciliation_service 整体迁入
  `src/app/runtime/orchestration/services/runtime_reconciliation_service_impl.py`,
  本 facade 直接 import 实现,无需再走 workline 域。
- 当前 facade 内部仍依赖 workline 域 — 这是 launch PR 阶段的合规桥接,不
  算违反分层架构 (facade 是反向依赖的反向出口,允许向下委托一次)。
"""

from __future__ import annotations

from typing import Any


class RuntimeReconciliationFacade:
    """Runtime 对账门面 — device/callback 域唯一入口。

    内部委托 workline_runtime_reconciliation_service 单例 (Phase 2 burn-down
    替换为本地实现)。
    """

    def __init__(self) -> None:
        self._delegate: Any | None = None

    def _resolve_delegate(self) -> Any:
        """惰性解析 workline 单例,避免模块加载时拉起 workline 全链路。"""
        if self._delegate is None:
            from src.app.workline.services.runtime_reconciliation_service import (
                workline_runtime_reconciliation_service,
            )

            self._delegate = workline_runtime_reconciliation_service
        return self._delegate

    async def record_late_callback_if_pending(
        self,
        db: Any,
        *,
        command: Any,
        callback_payload: dict[str, Any],
    ) -> bool:
        """委托 workline service,记录迟到 callback 证据。"""
        return await self._resolve_delegate().record_late_callback_if_pending(
            db,
            command=command,
            callback_payload=callback_payload,
        )

    async def activate_execution_deadline_after_ack(
        self,
        db: Any,
        *,
        command_id: int,
        ack_received_at: Any,
    ) -> bool:
        """委托 workline service,在 ACK 后激活执行 deadline。"""
        return await self._resolve_delegate().activate_execution_deadline_after_ack(
            db,
            command_id=command_id,
            ack_received_at=ack_received_at,
        )


runtime_reconciliation_facade = RuntimeReconciliationFacade()


__all__ = ["RuntimeReconciliationFacade", "runtime_reconciliation_facade"]
