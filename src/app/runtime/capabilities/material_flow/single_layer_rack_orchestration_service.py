"""单层货架旧编排入口的迁移边界。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.ext.asyncio import AsyncSession


class SingleLayerRackMigrationRequiredError(RuntimeError):
    """单层货架派发尚未迁移到 typed T5 dispatcher。"""


class SingleLayerRackOrchestrationDecisionCode(StrEnum):
    """单层货架编排显式决策。"""

    WAITING = "WAITING"
    DISPATCH_WMS = "DISPATCH_WMS"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class SingleLayerRackOrchestrationDecision:
    """单层货架编排结果。"""

    decision: SingleLayerRackOrchestrationDecisionCode
    reason: str | None = None
    rack_operation_request: dict[str, Any] | None = None
    fact_payload: dict[str, Any] | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)


class SingleLayerRackOrchestrationService:
    """T5 dispatcher 上线前冻结旧单层货架生产入口。"""

    DEFAULT_OPERATION_TYPE = "SUPPLY_SINGLE_LAYER_RACK"
    DEFAULT_TIMEOUT_SECONDS = 1800

    async def plan_single_layer_rack_dispatch(
        self,
        db: AsyncSession,
        *,
        business_demand_key: str | None,
        demand_type: str | None,
        workline: Any,
        session: Any | None = None,
        station_code: str,
        rack_snapshot_ref: str | None = None,
        rack_code: str | None = None,
        dispatch_key: str | None = None,
        operation_type: str | None = None,
        target_code: str | None = None,
        trace_id: str | None = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        payload: Mapping[str, Any] | None = None,
        fact_payload: Mapping[str, Any] | None = None,
    ) -> SingleLayerRackOrchestrationDecision:
        """拒绝旧 transport 编排，且不创建 operation、lease 或 outbox。"""

        del (
            db,
            business_demand_key,
            demand_type,
            workline,
            session,
            station_code,
            rack_snapshot_ref,
            rack_code,
            dispatch_key,
            operation_type,
            target_code,
            trace_id,
            timeout_seconds,
            payload,
            fact_payload,
        )
        raise SingleLayerRackMigrationRequiredError(
            "legacy rack transport is removed; T5 dispatcher is not implemented"
        )


single_layer_rack_orchestration_service = SingleLayerRackOrchestrationService()


__all__ = [
    "SingleLayerRackMigrationRequiredError",
    "SingleLayerRackOrchestrationDecision",
    "SingleLayerRackOrchestrationDecisionCode",
    "SingleLayerRackOrchestrationService",
    "single_layer_rack_orchestration_service",
]
