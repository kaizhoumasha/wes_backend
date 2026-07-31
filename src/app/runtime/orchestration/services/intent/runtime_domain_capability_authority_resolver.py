"""从持久化事实解析 Runtime domain SYSTEM_CAPABILITY 权限。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.app.runtime.orchestration.repositories.runtime_domain_capability_authority_repository import (
    RuntimeDomainCapabilityAuthorityRepository,
    runtime_domain_capability_authority_repository,
)

_SMT_INBOUND_HANDOFF_PRODUCER = "SMT_INBOUND_HANDOFF"


@dataclass(frozen=True, slots=True)
class ResolvedRuntimeDomainCapabilityAuthority:
    """由已锁定数据库事实派生的不可变 domain authority。"""

    producer: str
    correlation_id: str
    business_owner_key: str
    workline_id: int

    @property
    def binding_snapshot(self) -> dict[str, object]:
        return {
            "producer": self.producer,
            "business_owner_key": self.business_owner_key,
            "workline_id": self.workline_id,
            "correlation_id": self.correlation_id,
        }


class RuntimeDomainCapabilityAuthorityResolver:
    """校验 correlation → handoff demand → workline 持久化归属链。"""

    def __init__(
        self,
        repository: RuntimeDomainCapabilityAuthorityRepository | None = None,
    ) -> None:
        self._repository = repository or runtime_domain_capability_authority_repository

    async def resolve(
        self,
        db: Any,
        *,
        correlation_id: str,
    ) -> ResolvedRuntimeDomainCapabilityAuthority:
        if not isinstance(correlation_id, str) or not correlation_id:
            raise ValueError("runtime domain system capability requires correlation_id")
        facts = await self._repository.lock_smt_inbound_handoff_facts(
            db,
            correlation_id=correlation_id,
        )
        if facts is None:
            raise PermissionError("runtime domain system capability authority is not persisted")

        correlation = facts.correlation
        demand = facts.demand
        workline = facts.workline
        if correlation.execution_session_id is not None:
            raise PermissionError("runtime domain system capability correlation must not claim plugin session")
        if not isinstance(demand.id, int):
            raise PermissionError("runtime domain system capability demand is not persisted")
        expected_correlation_id = f"smt-inbound-handoff:{demand.id}"
        if correlation.correlation_id != expected_correlation_id:
            raise PermissionError("runtime domain system capability correlation anchor mismatch")
        if correlation.business_owner_key != demand.demand_key:
            raise PermissionError("runtime domain system capability business owner mismatch")
        if correlation.source_event_id != demand.rack_release_id:
            raise PermissionError("runtime domain system capability release anchor mismatch")
        if not isinstance(demand.trace_id, str) or not demand.trace_id or correlation.trace_id != demand.trace_id:
            raise PermissionError("runtime domain system capability trace anchor mismatch")
        if (
            not isinstance(workline.id, int)
            or demand.source_workline_id != workline.id
            or not isinstance(demand.source_workline_code, str)
            or demand.source_workline_code != workline.line_code
        ):
            raise PermissionError("runtime domain system capability workline anchor mismatch")

        return ResolvedRuntimeDomainCapabilityAuthority(
            producer=_SMT_INBOUND_HANDOFF_PRODUCER,
            correlation_id=correlation.correlation_id,
            business_owner_key=demand.demand_key,
            workline_id=workline.id,
        )


runtime_domain_capability_authority_resolver = RuntimeDomainCapabilityAuthorityResolver()

__all__ = [
    "ResolvedRuntimeDomainCapabilityAuthority",
    "RuntimeDomainCapabilityAuthorityResolver",
    "runtime_domain_capability_authority_resolver",
]
