"""北向只读运维入口的公开、脱敏响应模型。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime  # noqa: TC003 - Pydantic 运行时需要。
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.app.runtime.orchestration.operation_observability import NORTHBOUND_OPERATION_SLO_CATALOG_VERSION


@dataclass(frozen=True, slots=True)
class NorthboundOperationalPrincipal:
    """认证用户与当前 v1 WorkLine owner tenant scope。"""

    tenant_id: int
    user_id: int
    is_superuser: bool = False

    def __post_init__(self) -> None:
        if self.tenant_id <= 0 or self.user_id <= 0:
            raise ValueError("operational principal ids must be positive")


class NorthboundOperationHealth(BaseModel):
    """只暴露低基数 identity 和聚合 SLI，不暴露行级证据或 payload。"""

    model_config = ConfigDict(extra="forbid", from_attributes=True, frozen=True)

    provider_profile_identity: str = Field(min_length=1, max_length=240)
    operation_identity: str = Field(min_length=1, max_length=240)
    backlog_count: int = Field(ge=0)
    active_lease_count: int = Field(ge=0)
    unknown_count: int = Field(ge=0)
    oldest_queue_age_seconds: int = Field(ge=0)
    rate_limited_count: int = Field(ge=0)
    lease_loss_count: int = Field(ge=0)
    reconciliation_open_count: int = Field(ge=0)
    readiness: Literal["READY", "NOT_READY", "INVALID", "UNKNOWN"]


class NorthboundOperationalSnapshot(BaseModel):
    """租户作用域的北向运维快照。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["northbound-operational-snapshot.v1"] = "northbound-operational-snapshot.v1"
    catalog_version: Literal["northbound-operation-slo.v1"] = NORTHBOUND_OPERATION_SLO_CATALOG_VERSION
    generated_at: datetime
    tenant_scope: Literal["WORKLINE_OWNER", "PLATFORM"]
    tenant_id: int | None
    workline_id: int | None
    operations: tuple[NorthboundOperationHealth, ...]


__all__ = [
    "NorthboundOperationHealth",
    "NorthboundOperationalPrincipal",
    "NorthboundOperationalSnapshot",
]
