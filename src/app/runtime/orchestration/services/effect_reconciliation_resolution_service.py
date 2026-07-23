"""EFFECT reconciliation 人工决议服务。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentStatus
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from src.app.runtime.orchestration.effect_bridges import EffectReconciliationBridge


class EffectReconciliationResolutionService:
    """以稳定请求身份原子提交 EFFECT 人工对账决议。"""

    def __init__(self, *, reconciliation_bridge: EffectReconciliationBridge | None = None) -> None:
        self._reconciliation_bridge = reconciliation_bridge

    def _resolve_reconciliation_bridge(self) -> EffectReconciliationBridge:
        if self._reconciliation_bridge is None:
            from src.app.runtime.orchestration.effect_bridges import effect_reconciliation_bridge

            self._reconciliation_bridge = effect_reconciliation_bridge
        return self._reconciliation_bridge

    async def resolve(
        self,
        db: Any,
        *,
        dispatch_key: str,
        request_id: str,
        resolution: str,
        operator_note: str,
        operator_id: int,
    ) -> dict[str, Any]:
        normalized_request_id = request_id.strip()
        if not normalized_request_id:
            raise ValueError("request_id 不能为空")
        target = RuntimeIntentStatus(resolution)
        result = await self._resolve_reconciliation_bridge().resolve(
            db,
            dispatch_key=dispatch_key,
            occurred_at_ms=int(timezone.now_utc().timestamp() * 1000),
            resolution=target,
            reason_code="MANUAL_EFFECT_RECONCILIATION_RESOLUTION",
            source_event_id=normalized_request_id,
            evidence_json={
                "operator_id": operator_id,
                "operator_note": operator_note,
                "request_id": normalized_request_id,
            },
        )
        await db.commit()
        return {
            "dispatch_key": dispatch_key,
            "resolution": target.value,
            "request_id": normalized_request_id,
            "intent_status": result.intent_status.value,
            "case_status": result.case_status.value if result.case_status is not None else None,
            "state_changed": result.state_changed,
        }


effect_reconciliation_resolution_service = EffectReconciliationResolutionService()

__all__ = ["EffectReconciliationResolutionService", "effect_reconciliation_resolution_service"]
