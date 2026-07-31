"""Runtime domain SYSTEM_CAPABILITY 权限事实的锁定读取。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import select

from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.models.smt_inbound_handoff import SmtInboundHandoffDemand
from src.app.workline.models.workline import WorkLine


@dataclass(frozen=True, slots=True)
class LockedRuntimeDomainCapabilityFacts:
    """同一事务中锁定的 correlation、handoff demand 与来源工作线。"""

    correlation: ExecutionCorrelation
    demand: SmtInboundHandoffDemand
    workline: WorkLine


class RuntimeDomainCapabilityAuthorityRepository:
    """仅负责从数据库锁定 domain capability 的权限事实。"""

    async def lock_smt_inbound_handoff_facts(
        self,
        db: Any,
        *,
        correlation_id: str,
    ) -> LockedRuntimeDomainCapabilityFacts | None:
        correlation_columns = cast("Any", ExecutionCorrelation).__table__.c
        correlation_result = await db.execute(
            select(ExecutionCorrelation).where(correlation_columns.correlation_id == correlation_id).with_for_update()
        )
        correlation = correlation_result.scalar_one_or_none()
        if correlation is None:
            return None

        business_owner_key = correlation.business_owner_key
        source_event_id = correlation.source_event_id
        if not isinstance(business_owner_key, str) or not business_owner_key:
            return None
        if not isinstance(source_event_id, str) or not source_event_id:
            return None

        demand_columns = cast("Any", SmtInboundHandoffDemand).__table__.c
        demand_result = await db.execute(
            select(SmtInboundHandoffDemand)
            .where(
                demand_columns.demand_key == business_owner_key,
                demand_columns.rack_release_id == source_event_id,
            )
            .with_for_update()
        )
        demand = demand_result.scalar_one_or_none()
        if demand is None or not isinstance(demand.source_workline_id, int):
            return None

        workline_columns = cast("Any", WorkLine).__table__.c
        workline_result = await db.execute(
            select(WorkLine)
            .where(
                workline_columns.id == demand.source_workline_id,
                workline_columns.is_deleted.is_(False),
            )
            .with_for_update()
        )
        workline = workline_result.scalar_one_or_none()
        if workline is None:
            return None
        return LockedRuntimeDomainCapabilityFacts(
            correlation=correlation,
            demand=demand,
            workline=workline,
        )


runtime_domain_capability_authority_repository = RuntimeDomainCapabilityAuthorityRepository()

__all__ = [
    "LockedRuntimeDomainCapabilityFacts",
    "RuntimeDomainCapabilityAuthorityRepository",
    "runtime_domain_capability_authority_repository",
]
