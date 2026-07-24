"""QUERY shadow comparison consumer 与 readiness 应用服务。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.app.runtime.system_capabilities.shadow_readiness import (
    QueryShadowReadinessPolicy,
    QueryShadowReadinessReport,
    build_query_shadow_readiness_report,
)
from src.app.runtime.system_capabilities.shadow_repository import (
    QueryShadowComparisonRepository,
    QueryShadowReadinessRepository,
    query_shadow_comparison_repository,
    query_shadow_readiness_repository,
)

if TYPE_CHECKING:
    from datetime import datetime


class QueryShadowComparisonService:
    def __init__(self, repository: QueryShadowComparisonRepository | None = None) -> None:
        self._repository = repository or query_shadow_comparison_repository

    async def store(self, db: Any, *, payload: dict[str, Any]) -> None:
        await self._repository.append_from_task(db, payload=payload)


class QueryShadowReadinessService:
    def __init__(self, repository: QueryShadowReadinessRepository | None = None) -> None:
        self._repository = repository or query_shadow_readiness_repository

    async def generate(
        self,
        db: Any,
        *,
        provider_profile_identity: str,
        operation_identity: str,
        observed_from: datetime,
        observed_until: datetime,
        generated_at: datetime,
        policy: QueryShadowReadinessPolicy | None = None,
    ) -> QueryShadowReadinessReport:
        expected = await self._repository.list_expected(
            db,
            provider_profile_identity=provider_profile_identity,
            operation_identity=operation_identity,
            observed_from=observed_from,
            observed_until=observed_until,
        )
        comparisons = await self._repository.list_comparisons(
            db,
            provider_profile_identity=provider_profile_identity,
            operation_identity=operation_identity,
            observed_from=observed_from,
            observed_until=observed_until,
        )
        report = build_query_shadow_readiness_report(
            provider_profile_identity=provider_profile_identity,
            operation_identity=operation_identity,
            expected_samples=expected,
            comparisons=comparisons,
            generated_at=generated_at,
            policy=policy,
        )
        self._repository.add_report(db, report)
        return report


query_shadow_comparison_service = QueryShadowComparisonService()
query_shadow_readiness_service = QueryShadowReadinessService()

__all__ = [
    "QueryShadowComparisonService",
    "QueryShadowReadinessService",
    "query_shadow_comparison_service",
    "query_shadow_readiness_service",
]
